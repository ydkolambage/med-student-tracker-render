import shutil
from datetime import datetime, timedelta, timezone as dt_timezone
import tarfile
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from audits.models import AuditEvent
from audits.roles import Role
from backups.admin import BackupRecordAdmin
from backups.models import BackupRecord
from backups.services import BackupError, _build_restore_sql, perform_full_system_backup, register_existing_backup


def assign_role(user, role_name):
    group, _ = Group.objects.get_or_create(name=role_name)
    user.groups.add(group)


class BackupTestMixin:
    def setUp(self):
        super().setUp()
        self.temp_dir = tempfile.mkdtemp()
        self.project_root = Path(self.temp_dir) / 'project'
        self.full_root = Path(self.temp_dir) / 'Backups'
        self.daily_root = self.project_root / 'Backups_daily_sql'
        self.project_root.mkdir(parents=True, exist_ok=True)
        self._build_project_tree()
        self.settings_override = override_settings(BACKUP_PROJECT_ROOT=self.project_root, BACKUP_FULL_ROOT=self.full_root, BACKUP_DAILY_SQL_ROOT=self.daily_root, BACKUP_RETENTION_DAYS=30)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(lambda: shutil.rmtree(self.temp_dir, ignore_errors=True))

    def _build_project_tree(self):
        (self.project_root / 'app.py').write_text("print('med tracker')\n", encoding='utf-8')
        (self.project_root / 'docs').mkdir(parents=True, exist_ok=True)
        (self.project_root / 'docs' / 'notes.txt').write_text('keep me\n', encoding='utf-8')
        (self.project_root / '.venv').mkdir(parents=True, exist_ok=True)
        (self.project_root / '.venv' / 'ignored.txt').write_text('skip\n', encoding='utf-8')
        (self.project_root / 'node_modules').mkdir(parents=True, exist_ok=True)
        (self.project_root / 'node_modules' / 'ignored.js').write_text('skip\n', encoding='utf-8')
        (self.project_root / '.git').mkdir(parents=True, exist_ok=True)
        (self.project_root / '.git' / 'config').write_text('skip\n', encoding='utf-8')
        (self.project_root / 'nested' / '__pycache__').mkdir(parents=True, exist_ok=True)
        (self.project_root / 'nested' / '__pycache__' / 'ignored.pyc').write_bytes(b'pyc')
        (self.project_root / 'tmp').mkdir(parents=True, exist_ok=True)
        (self.project_root / 'tmp' / 'ignored.tmp').write_text('skip\n', encoding='utf-8')
        self.daily_root.mkdir(parents=True, exist_ok=True)
        (self.daily_root / 'existing.sql').write_text('skip\n', encoding='utf-8')

    def fake_pg_dump(self, output_path):
        Path(output_path).write_text('-- fake pg dump --\n', encoding='utf-8')


class BackupServiceTests(BackupTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.registry_admin = get_user_model().objects.create_user(username='backupadmin', email='backupadmin@example.com', password='safe-password-123')
        assign_role(self.registry_admin, Role.REGISTRY_ADMIN)

    def test_full_backup_creates_directory_archive_record_and_audit(self):
        timestamp = '20260415_010203'
        with patch('backups.services._backup_timestamp', return_value=timestamp), patch('backups.services._run_pg_dump', side_effect=self.fake_pg_dump):
            result = perform_full_system_backup(initiated_by=self.registry_admin)

        backup_dir = self.full_root / f'full_backup_{timestamp}'
        archive_path = backup_dir / f'project_files_{timestamp}.tar.gz'
        dump_path = backup_dir / f'database_{timestamp}.sql'
        record = BackupRecord.objects.get(backup_type=BackupRecord.BackupType.FULL_SYSTEM)

        self.assertTrue(result.succeeded)
        self.assertEqual(result.output_path, backup_dir)
        self.assertTrue(dump_path.exists())
        self.assertTrue(archive_path.exists())
        self.assertEqual(record.initiated_by, self.registry_admin)
        self.assertEqual(len(record.checksum_sha256), 64)
        self.assertGreater(record.size_bytes, 0)
        self.assertIsNotNone(record.verified_at)
        self.assertIsNotNone(record.retention_expires_at)
        self.assertTrue(AuditEvent.objects.filter(action='backups.full.run', outcome=AuditEvent.Outcome.SUCCESS).exists())

        with tarfile.open(archive_path, mode='r:gz') as archive:
            archived_names = set(archive.getnames())

        self.assertIn('app.py', archived_names)
        self.assertIn('docs/notes.txt', archived_names)
        self.assertNotIn('.venv/ignored.txt', archived_names)

    def test_full_backup_records_failures(self):
        timestamp = '20260415_020304'
        with patch('backups.services._backup_timestamp', return_value=timestamp), patch('backups.services._run_pg_dump', side_effect=BackupError('pg_dump failed')):
            result = perform_full_system_backup(initiated_by=self.registry_admin)

        record = BackupRecord.objects.get()
        self.assertFalse(result.succeeded)
        self.assertEqual(record.status, BackupRecord.Status.FAILED)
        self.assertIn('pg_dump failed', record.error_message)
        self.assertTrue(AuditEvent.objects.filter(action='backups.full.run', outcome=AuditEvent.Outcome.FAILED).exists())

    def test_restore_sql_resets_public_schema_before_dump_contents(self):
        backup_file = self.daily_root / 'medtracker_20260415_030405.sql'
        backup_file.write_text('CREATE TABLE example (id integer);\n', encoding='utf-8')
        restore_sql = _build_restore_sql(backup_file)
        self.assertIn('DROP SCHEMA IF EXISTS public CASCADE;', restore_sql)
        self.assertIn('CREATE SCHEMA public AUTHORIZATION "postgres";', restore_sql)
        self.assertIn('CREATE TABLE example (id integer);', restore_sql)

    def test_register_existing_backup_service_creates_success_record(self):
        backup_file = self.daily_root / 'medtracker_20260415_030405.sql'
        backup_file.write_text('select 1;\n', encoding='utf-8')
        record = register_existing_backup(backup_type=BackupRecord.BackupType.DAILY_SQL, filesystem_path=str(backup_file), initiated_by=self.registry_admin)
        self.assertEqual(record.filesystem_path, str(backup_file))
        self.assertEqual(record.status, BackupRecord.Status.SUCCESS)
        self.assertEqual(len(record.checksum_sha256), 64)
        self.assertGreater(record.size_bytes, 0)
        self.assertIsNotNone(record.verified_at)

    def test_retention_prunes_expired_success_records(self):
        expired_dir = self.full_root / 'full_backup_20260401_010101'
        expired_dir.mkdir(parents=True, exist_ok=True)
        (expired_dir / 'database_20260401_010101.sql').write_text('select 1;\n', encoding='utf-8')
        (expired_dir / 'project_files_20260401_010101.tar.gz').write_text('archive', encoding='utf-8')
        expired_record = BackupRecord.objects.create(
            initiated_by=self.registry_admin,
            backup_type=BackupRecord.BackupType.FULL_SYSTEM,
            filesystem_path=str(expired_dir),
            status=BackupRecord.Status.SUCCESS,
            retention_expires_at=timezone.now() - timedelta(days=1),
        )

        with patch('backups.services._backup_timestamp', return_value='20260415_040506'), patch('backups.services._run_pg_dump', side_effect=self.fake_pg_dump):
            perform_full_system_backup(initiated_by=self.registry_admin)

        self.assertFalse(BackupRecord.objects.filter(pk=expired_record.pk).exists())
        self.assertFalse(expired_dir.exists())
        self.assertTrue(AuditEvent.objects.filter(action='backups.retention.prune', outcome=AuditEvent.Outcome.SUCCESS).exists())


class BackupAdminPermissionTests(BackupTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.registry_admin = get_user_model().objects.create_user(username='registrybackup', email='registrybackup@example.com', password='safe-password-123', is_staff=True)
        assign_role(self.registry_admin, Role.REGISTRY_ADMIN)
        self.auditor = get_user_model().objects.create_user(username='auditbackup', email='auditbackup@example.com', password='safe-password-123', is_staff=True)
        assign_role(self.auditor, Role.AUDITOR)
        self.staff_user = get_user_model().objects.create_user(username='staffbackup', email='staffbackup@example.com', password='safe-password-123', is_staff=True)

    def test_run_full_backup_view_forbids_non_registry_admins(self):
        self.client.force_login(self.staff_user)
        response = self.client.get(reverse('admin:backups_backuprecord_run_full_backup'))
        self.assertEqual(response.status_code, 403)

    def test_run_full_backup_view_is_available_to_registry_admins(self):
        self.client.force_login(self.registry_admin)
        response = self.client.get(reverse('admin:backups_backuprecord_run_full_backup'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Run full system backup')

    @override_settings(BACKUP_TIME_ZONE='Asia/Colombo')
    def test_created_at_local_uses_backup_timezone(self):
        record = BackupRecord.objects.create(initiated_by=self.registry_admin, backup_type=BackupRecord.BackupType.DAILY_SQL, filesystem_path=str(self.daily_root / 'sample.sql'), status=BackupRecord.Status.SUCCESS)
        record.created_at = datetime(2026, 4, 14, 20, 30, 0, tzinfo=dt_timezone.utc)
        admin_instance = BackupRecordAdmin(BackupRecord, admin.site)
        rendered = admin_instance.created_at_local(record)
        self.assertEqual(rendered, '2026-04-15 02:00:00 AM')

    def test_registry_admin_changelist_shows_restore_action(self):
        backup_file = self.daily_root / 'medtracker_20260415_030405.sql'
        backup_file.write_text('sql', encoding='utf-8')
        BackupRecord.objects.create(initiated_by=self.registry_admin, backup_type=BackupRecord.BackupType.DAILY_SQL, filesystem_path=str(backup_file), status=BackupRecord.Status.SUCCESS)
        self.client.force_login(self.registry_admin)
        response = self.client.get(reverse('admin:backups_backuprecord_changelist'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Restore selected SQL backup')

    def test_registry_admin_can_register_existing_backup_from_admin(self):
        backup_dir = self.full_root / 'full_backup_20260415_010203'
        backup_dir.mkdir(parents=True, exist_ok=True)
        (backup_dir / 'database_20260415_010203.sql').write_text('select 1;\n', encoding='utf-8')
        (backup_dir / 'project_files_20260415_010203.tar.gz').write_text('archive', encoding='utf-8')
        self.client.force_login(self.registry_admin)
        response = self.client.post(
            reverse('admin:backups_backuprecord_register_existing'),
            {'backup_type': BackupRecord.BackupType.FULL_SYSTEM, 'filesystem_path': str(backup_dir)},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(BackupRecord.objects.filter(filesystem_path=str(backup_dir), backup_type=BackupRecord.BackupType.FULL_SYSTEM).exists())
        self.assertContains(response, 'Registered existing backup')

    def test_register_existing_backup_rejects_duplicate_path(self):
        backup_file = self.daily_root / 'medtracker_20260415_030405.sql'
        backup_file.write_text('select 1;\n', encoding='utf-8')
        BackupRecord.objects.create(initiated_by=self.registry_admin, backup_type=BackupRecord.BackupType.DAILY_SQL, filesystem_path=str(backup_file), status=BackupRecord.Status.SUCCESS)
        self.client.force_login(self.registry_admin)
        response = self.client.post(
            reverse('admin:backups_backuprecord_register_existing'),
            {'backup_type': BackupRecord.BackupType.DAILY_SQL, 'filesystem_path': str(backup_file)},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'already exists')

    def test_auditor_can_view_backup_module_but_not_run_backup(self):
        self.client.force_login(self.auditor)
        response = self.client.get(reverse('admin:backups_backuprecord_changelist'))
        self.assertEqual(response.status_code, 200)
        run_response = self.client.get(reverse('admin:backups_backuprecord_run_full_backup'))
        self.assertEqual(run_response.status_code, 403)

    def test_registry_admin_delete_confirmation_removes_record_and_directory(self):
        backup_dir = self.full_root / 'full_backup_20260415_010203'
        backup_dir.mkdir(parents=True, exist_ok=True)
        (backup_dir / 'database.sql').write_text('sql', encoding='utf-8')
        record = BackupRecord.objects.create(initiated_by=self.registry_admin, backup_type=BackupRecord.BackupType.FULL_SYSTEM, filesystem_path=str(backup_dir), status=BackupRecord.Status.SUCCESS)

        self.client.force_login(self.registry_admin)
        delete_url = reverse('admin:backups_backuprecord_delete', args=[record.pk])
        response = self.client.post(delete_url, {'post': 'yes'}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(BackupRecord.objects.filter(pk=record.pk).exists())
        self.assertFalse(backup_dir.exists())
        self.assertTrue(AuditEvent.objects.filter(action='backups.artifact.delete', outcome=AuditEvent.Outcome.SUCCESS).exists())


class DailySqlBackupCommandTests(BackupTestMixin, TestCase):
    def test_command_creates_timestamped_sql_backup_record_and_audit(self):
        timestamp = '20260415_030405'
        with patch('backups.services._backup_timestamp', return_value=timestamp), patch('backups.services._run_pg_dump', side_effect=self.fake_pg_dump):
            stdout = StringIO()
            call_command('daily_sql_backup', stdout=stdout)

        backup_file = self.daily_root / f'medtracker_{timestamp}.sql'
        record = BackupRecord.objects.get()
        self.assertTrue(backup_file.exists())
        self.assertEqual(record.backup_type, BackupRecord.BackupType.DAILY_SQL)
        self.assertEqual(len(record.checksum_sha256), 64)
        self.assertGreater(record.size_bytes, 0)
        self.assertIsNotNone(record.retention_expires_at)
        self.assertTrue(AuditEvent.objects.filter(action='backups.daily_sql.run', outcome=AuditEvent.Outcome.SUCCESS).exists())

    def test_command_raises_non_zero_failure_and_logs_record(self):
        with patch('backups.services._run_pg_dump', side_effect=BackupError('pg_dump failed')):
            with self.assertRaisesMessage(CommandError, 'pg_dump failed'):
                call_command('daily_sql_backup')

        record = BackupRecord.objects.get()
        self.assertEqual(record.backup_type, BackupRecord.BackupType.DAILY_SQL)
        self.assertEqual(record.status, BackupRecord.Status.FAILED)
        self.assertTrue(AuditEvent.objects.filter(action='backups.daily_sql.run', outcome=AuditEvent.Outcome.FAILED).exists())


