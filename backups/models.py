from django.conf import settings
from django.db import models


class BackupRecord(models.Model):
    class BackupType(models.TextChoices):
        FULL_SYSTEM = 'full_system', 'Full system'
        DAILY_SQL = 'daily_sql', 'Daily SQL'

    class Status(models.TextChoices):
        SUCCESS = 'success', 'Success'
        FAILED = 'failed', 'Failed'

    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name='backup_records',
        blank=True,
        null=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    backup_type = models.CharField(max_length=32, choices=BackupType.choices)
    filesystem_path = models.CharField(max_length=500)
    status = models.CharField(max_length=16, choices=Status.choices)
    checksum_sha256 = models.CharField(max_length=64, blank=True)
    size_bytes = models.BigIntegerField(default=0)
    retention_expires_at = models.DateTimeField(blank=True, null=True)
    verified_at = models.DateTimeField(blank=True, null=True)
    restore_verified_at = models.DateTimeField(blank=True, null=True)
    last_verified_error = models.TextField(blank=True)
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['backup_type', 'created_at']),
            models.Index(fields=['status', 'created_at']),
            models.Index(fields=['retention_expires_at']),
        ]

    def __str__(self):
        timestamp = self.created_at.strftime('%Y-%m-%d %H:%M:%S') if self.created_at else 'pending'
        return f'{self.get_backup_type_display()} backup at {timestamp}'
