# Backup Setup

## Manual full backup from Django admin

1. Sign in to Django admin with a superuser account.
2. Open the `Backups` section.
3. Select `Backup records`.
4. Click `Run full system backup`.
5. Confirm the action on the admin page.

The manual full backup creates a timestamped folder under `/home/ydkolambage/projects/Backups` by default. The timestamp uses `BACKUP_TIME_ZONE`, which defaults to `DJANGO_TIME_ZONE` and falls back to `Asia/Colombo`.
Each folder contains:
- a PostgreSQL SQL dump of the configured backup database
- a `tar.gz` archive of the Django project files

Project archiving excludes common non-essential directories and files such as `.venv`, `__pycache__`, `.git`, `node_modules`, and temporary/log file suffixes defined in Django settings.

## Register existing backups

1. Open `Backups > Backup records` in Django admin.
2. Click `Register existing backup`.
3. Choose the backup type.
4. Enter the absolute server path to the existing backup folder or SQL file.
5. Submit the form to create the backup-history entry.

Browsers cannot open the server filesystem directly, so this feature validates a typed absolute path instead of showing a native filesystem picker.

## Restore from admin

1. Open `Backups > Backup records` in Django admin.
2. Select exactly one backup entry.
3. Choose `Restore selected SQL backup` from the action dropdown.
4. Confirm the restore on the confirmation page.

The restore action accepts:
- a daily SQL backup file ending in `.sql`
- a full-system backup directory containing exactly one `database_*.sql` file

The restore runs `psql` against the configured PostgreSQL database, resets the `public` schema, and then loads the selected SQL backup. This replaces the current database contents and stops on the first SQL error.

## Daily SQL backup command

Run the management command from the project root:

```bash
source .venv/bin/activate
python manage.py daily_sql_backup
```

By default, the command writes timestamped SQL files into `/home/ydkolambage/projects/med-student-tracker/Backups_daily_sql`.
The command creates the directory if it does not already exist.
If `pg_dump` fails, the command exits non-zero and records the failure in backup history.

## Environment variables

Optional environment variables:
- `BACKUP_FULL_ROOT`
- `BACKUP_DAILY_SQL_ROOT`
- `BACKUP_PROJECT_ROOT`
- `BACKUP_PG_DUMP_BINARY`
- `BACKUP_PSQL_BINARY`
- `BACKUP_TIME_ZONE`
- `BACKUP_POSTGRES_DB`
- `BACKUP_POSTGRES_USER`
- `BACKUP_POSTGRES_PASSWORD`
- `BACKUP_POSTGRES_HOST`
- `BACKUP_POSTGRES_PORT`

If the backup-specific PostgreSQL variables are not set, the project falls back to the main PostgreSQL settings where available.

## Sample cron entry

```cron
0 2 * * * cd /home/ydkolambage/projects/med-student-tracker && . .venv/bin/activate && DJANGO_SETTINGS_MODULE=config.settings python manage.py daily_sql_backup >> /home/ydkolambage/projects/med-student-tracker/Backups_daily_sql/cron.log 2>&1
```

Adjust the schedule, virtual environment path, and exported environment variables for your deployment environment.
