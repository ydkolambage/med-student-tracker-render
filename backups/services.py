import hashlib
import os
import shutil
import subprocess
import tarfile
from datetime import timedelta
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone

from audits.models import AuditEvent
from audits.utils import record_audit_event
from backups.models import BackupRecord


class BackupError(Exception):
    pass


@dataclass(frozen=True)
class BackupExecutionResult:
    record: BackupRecord
    output_path: Path

    @property
    def succeeded(self):
        return self.record.status == BackupRecord.Status.SUCCESS


def perform_full_system_backup(*, initiated_by=None, request=None, channel=AuditEvent.Channel.WEB):
    timestamp = _backup_timestamp()
    root = _validate_root_path(settings.BACKUP_FULL_ROOT, label='Full backup root')
    backup_dir = _build_child_path(root, f'full_backup_{timestamp}')

    try:
        root.mkdir(parents=True, exist_ok=True)
        backup_dir.mkdir(parents=False, exist_ok=False)
        database_dump_path = backup_dir / f'database_{timestamp}.sql'
        project_archive_path = backup_dir / f'project_files_{timestamp}.tar.gz'
        _run_pg_dump(database_dump_path)
        _create_project_archive(project_archive_path)
        record = BackupRecord.objects.create(
            initiated_by=initiated_by,
            backup_type=BackupRecord.BackupType.FULL_SYSTEM,
            filesystem_path=str(backup_dir),
            status=BackupRecord.Status.SUCCESS,
        )
        _finalize_verified_record(record)
        pruned_count = prune_expired_backups(actor=initiated_by, request=request, channel=channel)
    except Exception as exc:
        record = BackupRecord.objects.create(
            initiated_by=initiated_by,
            backup_type=BackupRecord.BackupType.FULL_SYSTEM,
            filesystem_path=str(backup_dir),
            status=BackupRecord.Status.FAILED,
            error_message=str(exc),
            last_verified_error=str(exc),
        )
        _record_backup_audit(
            action='backups.full.run',
            actor=initiated_by,
            request=request,
            channel=channel,
            instance=record,
            outcome=AuditEvent.Outcome.FAILED,
            metadata={'filesystem_path': str(backup_dir), 'error': str(exc)},
        )
        return BackupExecutionResult(record=record, output_path=backup_dir)

    _record_backup_audit(
        action='backups.full.run',
        actor=initiated_by,
        request=request,
        channel=channel,
        instance=record,
        outcome=AuditEvent.Outcome.SUCCESS,
        metadata={'filesystem_path': str(backup_dir), 'checksum_sha256': record.checksum_sha256, 'size_bytes': record.size_bytes, 'pruned_records': pruned_count},
    )
    return BackupExecutionResult(record=record, output_path=backup_dir)


def perform_daily_sql_backup(*, initiated_by=None, request=None, channel=AuditEvent.Channel.MANAGEMENT_COMMAND):
    timestamp = _backup_timestamp()
    root = _validate_root_path(settings.BACKUP_DAILY_SQL_ROOT, label='Daily SQL backup root')
    backup_file = _build_child_path(root, f'medtracker_{timestamp}.sql')

    try:
        root.mkdir(parents=True, exist_ok=True)
        _run_pg_dump(backup_file)
        record = BackupRecord.objects.create(
            initiated_by=initiated_by,
            backup_type=BackupRecord.BackupType.DAILY_SQL,
            filesystem_path=str(backup_file),
            status=BackupRecord.Status.SUCCESS,
        )
        _finalize_verified_record(record)
        pruned_count = prune_expired_backups(actor=initiated_by, request=request, channel=channel)
    except Exception as exc:
        record = BackupRecord.objects.create(
            initiated_by=initiated_by,
            backup_type=BackupRecord.BackupType.DAILY_SQL,
            filesystem_path=str(backup_file),
            status=BackupRecord.Status.FAILED,
            error_message=str(exc),
            last_verified_error=str(exc),
        )
        _record_backup_audit(
            action='backups.daily_sql.run',
            actor=initiated_by,
            request=request,
            channel=channel,
            instance=record,
            outcome=AuditEvent.Outcome.FAILED,
            metadata={'filesystem_path': str(backup_file), 'error': str(exc)},
        )
        return BackupExecutionResult(record=record, output_path=backup_file)

    _record_backup_audit(
        action='backups.daily_sql.run',
        actor=initiated_by,
        request=request,
        channel=channel,
        instance=record,
        outcome=AuditEvent.Outcome.SUCCESS,
        metadata={'filesystem_path': str(backup_file), 'checksum_sha256': record.checksum_sha256, 'size_bytes': record.size_bytes, 'pruned_records': pruned_count},
    )
    return BackupExecutionResult(record=record, output_path=backup_file)


def restore_database_backup(record, *, actor=None, request=None, channel=AuditEvent.Channel.ADMIN):
    sql_backup_path = get_restore_sql_path(record)
    restore_sql = _build_restore_sql(sql_backup_path)
    env = os.environ.copy()
    if settings.BACKUP_DATABASE_PASSWORD:
        env['PGPASSWORD'] = str(settings.BACKUP_DATABASE_PASSWORD)

    completed = subprocess.run(
        _database_restore_command(),
        capture_output=True,
        check=False,
        env=env,
        input=restore_sql,
        text=True,
    )
    if completed.returncode != 0:
        error_output = completed.stderr.strip() or completed.stdout.strip() or f'psql exited with code {completed.returncode}.'
        record.last_verified_error = error_output
        record.save(update_fields=['last_verified_error'])
        _record_backup_audit(
            action='backups.sql.restore',
            actor=actor,
            request=request,
            channel=channel,
            instance=record,
            outcome=AuditEvent.Outcome.FAILED,
            metadata={'restore_path': str(sql_backup_path), 'error': error_output},
        )
        raise BackupError(error_output)

    _verify_restore(record)
    _record_backup_audit(
        action='backups.sql.restore',
        actor=actor,
        request=request,
        channel=channel,
        instance=record,
        outcome=AuditEvent.Outcome.SUCCESS,
        metadata={'restore_path': str(sql_backup_path), 'restore_verified_at': record.restore_verified_at.isoformat() if record.restore_verified_at else ''},
    )
    return sql_backup_path


def register_existing_backup(*, backup_type, filesystem_path, initiated_by=None, request=None, channel=AuditEvent.Channel.ADMIN):
    resolved_path = validate_existing_backup_artifact(filesystem_path, backup_type)
    if BackupRecord.objects.filter(filesystem_path=str(resolved_path)).exists():
        raise BackupError(f'A backup entry already exists for {resolved_path}.')

    record = BackupRecord.objects.create(
        initiated_by=initiated_by,
        backup_type=backup_type,
        filesystem_path=str(resolved_path),
        status=BackupRecord.Status.SUCCESS,
    )
    _finalize_verified_record(record)
    _record_backup_audit(
        action='backups.record.register',
        actor=initiated_by,
        request=request,
        channel=channel,
        instance=record,
        outcome=AuditEvent.Outcome.SUCCESS,
        metadata={'filesystem_path': str(resolved_path), 'backup_type': backup_type, 'checksum_sha256': record.checksum_sha256, 'size_bytes': record.size_bytes},
    )
    return record


def validate_existing_backup_artifact(filesystem_path, backup_type):
    artifact_path = _validate_backup_artifact_path(filesystem_path)
    if not artifact_path.exists():
        raise BackupError(f'Backup artifact does not exist: {artifact_path}')

    if backup_type == BackupRecord.BackupType.FULL_SYSTEM:
        if not artifact_path.is_dir():
            raise BackupError('Full system backups must point to a backup directory.')
        candidates = sorted(artifact_path.glob('database_*.sql'))
        archives = sorted(artifact_path.glob('project_files_*.tar.gz'))
        if len(candidates) != 1:
            raise BackupError(f'Expected exactly one database_*.sql file in {artifact_path}, found {len(candidates)}.')
        if len(archives) != 1:
            raise BackupError(f'Expected exactly one project_files_*.tar.gz file in {artifact_path}, found {len(archives)}.')
        return artifact_path

    if backup_type == BackupRecord.BackupType.DAILY_SQL:
        if artifact_path.is_dir() or artifact_path.suffix != '.sql':
            raise BackupError('Daily SQL backups must point to a .sql file.')
        return artifact_path

    raise BackupError(f'Unsupported backup type: {backup_type}')


def get_restore_sql_path(record):
    artifact_path = _validate_backup_artifact_path(record.filesystem_path)
    if artifact_path.is_dir():
        candidates = sorted(artifact_path.glob('database_*.sql'))
        if len(candidates) != 1:
            raise BackupError(f'Expected exactly one database_*.sql file in {artifact_path}, found {len(candidates)}.')
        return candidates[0]
    if artifact_path.suffix != '.sql':
        raise BackupError(f'Backup artifact is not a .sql file: {artifact_path}')
    return artifact_path


def delete_backup_artifact(record):
    artifact_path = _validate_backup_artifact_path(record.filesystem_path)
    if not artifact_path.exists():
        return artifact_path
    if artifact_path.is_dir():
        shutil.rmtree(artifact_path)
    else:
        artifact_path.unlink()
    return artifact_path


def prune_expired_backups(*, actor=None, request=None, channel=AuditEvent.Channel.SERVICE):
    expired_records = list(
        BackupRecord.objects.filter(status=BackupRecord.Status.SUCCESS, retention_expires_at__isnull=False, retention_expires_at__lte=timezone.now()).order_by('retention_expires_at')
    )
    for record in expired_records:
        delete_backup_artifact(record)
        _record_backup_audit(
            action='backups.retention.prune',
            actor=actor,
            request=request,
            channel=channel,
            instance=record,
            outcome=AuditEvent.Outcome.SUCCESS,
            metadata={'filesystem_path': record.filesystem_path, 'retention_expires_at': record.retention_expires_at.isoformat() if record.retention_expires_at else ''},
        )
        record.delete()
    return len(expired_records)


def _backup_timestamp():
    backup_timezone = ZoneInfo(settings.BACKUP_TIME_ZONE)
    return timezone.now().astimezone(backup_timezone).strftime('%Y%m%d_%H%M%S')


def _retention_deadline():
    return timezone.now() + timedelta(days=settings.BACKUP_RETENTION_DAYS)


def _validate_root_path(path_value, *, label):
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        raise BackupError(f'{label} must be an absolute path.')
    return path.resolve(strict=False)


def _validate_existing_directory(path_value, *, label):
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        raise BackupError(f'{label} must be an absolute path.')
    resolved = path.resolve(strict=True)
    if not resolved.is_dir():
        raise BackupError(f'{label} is not a directory: {resolved}')
    return resolved


def _build_child_path(root, name):
    child = root / name
    if child.parent.resolve(strict=False) != root.resolve(strict=False):
        raise BackupError('Backup path validation failed.')
    return child


def _database_dump_command(output_path):
    command = [
        settings.BACKUP_PG_DUMP_BINARY,
        '--file',
        str(output_path),
        '--format=plain',
        '--no-owner',
        '--no-privileges',
    ]
    if settings.BACKUP_DATABASE_HOST:
        command.extend(['--host', str(settings.BACKUP_DATABASE_HOST)])
    if settings.BACKUP_DATABASE_PORT:
        command.extend(['--port', str(settings.BACKUP_DATABASE_PORT)])
    if settings.BACKUP_DATABASE_USER:
        command.extend(['--username', str(settings.BACKUP_DATABASE_USER)])
    command.append(str(settings.BACKUP_DATABASE_NAME))
    return command


def _database_restore_command():
    command = [
        settings.BACKUP_PSQL_BINARY,
        '--set',
        'ON_ERROR_STOP=1',
        '--single-transaction',
    ]
    if settings.BACKUP_DATABASE_HOST:
        command.extend(['--host', str(settings.BACKUP_DATABASE_HOST)])
    if settings.BACKUP_DATABASE_PORT:
        command.extend(['--port', str(settings.BACKUP_DATABASE_PORT)])
    if settings.BACKUP_DATABASE_USER:
        command.extend(['--username', str(settings.BACKUP_DATABASE_USER)])
    command.extend(['--dbname', str(settings.BACKUP_DATABASE_NAME)])
    return command


def _build_restore_sql(sql_backup_path):
    reset_sql = _public_schema_reset_sql()
    dump_sql = sql_backup_path.read_text(encoding='utf-8')
    return reset_sql + '\n' + dump_sql


def _public_schema_reset_sql():
    schema_owner = settings.BACKUP_DATABASE_USER or 'postgres'
    return (
        'DROP SCHEMA IF EXISTS public CASCADE;\n'
        f'CREATE SCHEMA public AUTHORIZATION "{schema_owner}";\n'
        f'GRANT ALL ON SCHEMA public TO "{schema_owner}";\n'
        'GRANT ALL ON SCHEMA public TO public;\n'
    )


def _run_pg_dump(output_path):
    env = os.environ.copy()
    if settings.BACKUP_DATABASE_PASSWORD:
        env['PGPASSWORD'] = str(settings.BACKUP_DATABASE_PASSWORD)

    completed = subprocess.run(
        _database_dump_command(output_path),
        capture_output=True,
        check=False,
        env=env,
        text=True,
    )
    if completed.returncode != 0:
        output_path.unlink(missing_ok=True)
        error_output = completed.stderr.strip() or completed.stdout.strip() or f'pg_dump exited with code {completed.returncode}.'
        raise BackupError(error_output)


def _create_project_archive(archive_path):
    project_root = _validate_existing_directory(settings.BACKUP_PROJECT_ROOT, label='Backup project root')
    excluded_dir_names = set(getattr(settings, 'BACKUP_EXCLUDED_DIR_NAMES', ()))
    excluded_file_suffixes = tuple(getattr(settings, 'BACKUP_EXCLUDED_FILE_SUFFIXES', ()))
    excluded_file_names = set(getattr(settings, 'BACKUP_EXCLUDED_FILE_NAMES', ()))
    excluded_relative_roots = _excluded_relative_roots(project_root)

    with tarfile.open(archive_path, mode='w:gz') as archive:
        for current_root, dir_names, file_names in os.walk(project_root, topdown=True):
            current_path = Path(current_root)
            relative_root = current_path.relative_to(project_root)
            dir_names[:] = [
                dir_name
                for dir_name in dir_names
                if not _is_excluded(relative_root / dir_name, excluded_dir_names, excluded_relative_roots)
            ]
            for file_name in file_names:
                file_path = current_path / file_name
                relative_file_path = file_path.relative_to(project_root)
                if _is_excluded(relative_file_path, excluded_dir_names, excluded_relative_roots):
                    continue
                if file_name in excluded_file_names:
                    continue
                if file_path.suffix in excluded_file_suffixes:
                    continue
                if not file_path.is_file() and not file_path.is_symlink():
                    continue
                archive.add(file_path, arcname=str(relative_file_path), recursive=False)


def _excluded_relative_roots(project_root):
    excluded_paths = []
    for configured_path in (settings.BACKUP_FULL_ROOT, settings.BACKUP_DAILY_SQL_ROOT):
        candidate = Path(configured_path).expanduser().resolve(strict=False)
        try:
            excluded_paths.append(candidate.relative_to(project_root))
        except ValueError:
            continue
    return tuple(excluded_paths)


def _is_excluded(relative_path, excluded_dir_names, excluded_relative_roots):
    if any(part in excluded_dir_names for part in relative_path.parts):
        return True
    return any(relative_path == excluded_root or excluded_root in relative_path.parents for excluded_root in excluded_relative_roots)


def _allowed_backup_roots():
    return (
        _validate_root_path(settings.BACKUP_FULL_ROOT, label='Full backup root'),
        _validate_root_path(settings.BACKUP_DAILY_SQL_ROOT, label='Daily SQL backup root'),
    )


def _validate_backup_artifact_path(path_value):
    artifact_path = Path(path_value).expanduser()
    if not artifact_path.is_absolute():
        raise BackupError('Backup artifact path must be absolute.')
    resolved = artifact_path.resolve(strict=False)
    if not any(_is_relative_to(resolved, root) for root in _allowed_backup_roots()):
        raise BackupError(f'Refusing to access backup artifact outside configured roots: {resolved}')
    return resolved


def _is_relative_to(path, root):
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _record_backup_audit(*, action, actor, request, channel, instance, outcome, metadata):
    record_audit_event(
        action=action,
        actor=actor,
        instance=instance,
        request=request,
        channel=channel,
        outcome=outcome,
        metadata=metadata,
    )


def _artifact_file_paths(artifact_path):
    if artifact_path.is_dir():
        return [path for path in sorted(artifact_path.rglob('*')) if path.is_file()]
    return [artifact_path]


def _compute_artifact_integrity(artifact_path):
    digest = hashlib.sha256()
    total_size = 0
    for path in _artifact_file_paths(artifact_path):
        relative_name = str(path.relative_to(artifact_path)) if artifact_path.is_dir() else path.name
        digest.update(relative_name.encode('utf-8'))
        with path.open('rb') as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                digest.update(chunk)
                total_size += len(chunk)
    return digest.hexdigest(), total_size


def _finalize_verified_record(record):
    artifact_path = validate_existing_backup_artifact(record.filesystem_path, record.backup_type)
    checksum, size_bytes = _compute_artifact_integrity(artifact_path)
    record.checksum_sha256 = checksum
    record.size_bytes = size_bytes
    record.retention_expires_at = _retention_deadline()
    record.verified_at = timezone.now()
    record.last_verified_error = ''
    record.save(update_fields=['checksum_sha256', 'size_bytes', 'retention_expires_at', 'verified_at', 'last_verified_error'])
    return record


def _verify_restore(record):
    env = os.environ.copy()
    if settings.BACKUP_DATABASE_PASSWORD:
        env['PGPASSWORD'] = str(settings.BACKUP_DATABASE_PASSWORD)
    completed = subprocess.run(
        _database_restore_command() + ['--command', settings.BACKUP_VERIFY_RESTORE_QUERY],
        capture_output=True,
        check=False,
        env=env,
        text=True,
    )
    if completed.returncode != 0:
        error_output = completed.stderr.strip() or completed.stdout.strip() or f'psql verification exited with code {completed.returncode}.'
        record.last_verified_error = error_output
        record.save(update_fields=['last_verified_error'])
        raise BackupError(error_output)
    record.restore_verified_at = timezone.now()
    record.last_verified_error = ''
    record.save(update_fields=['restore_verified_at', 'last_verified_error'])


