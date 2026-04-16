from django.core.management.base import BaseCommand, CommandError

from backups.services import perform_daily_sql_backup


class Command(BaseCommand):
    help = "Create a timestamped daily SQL backup for the configured PostgreSQL database."

    def handle(self, *args, **options):
        result = perform_daily_sql_backup()
        if not result.succeeded:
            raise CommandError(result.record.error_message or "Daily SQL backup failed.")
        self.stdout.write(self.style.SUCCESS(f"Daily SQL backup created at {result.output_path}"))
