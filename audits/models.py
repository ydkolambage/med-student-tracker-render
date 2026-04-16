from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class AuditEvent(models.Model):
    class Channel(models.TextChoices):
        WEB = "web", "Web"
        ADMIN = "admin", "Admin"
        SERVICE = "service", "Service"
        MANAGEMENT_COMMAND = "management_command", "Management command"

    class Outcome(models.TextChoices):
        SUCCESS = "success", "Success"
        FORBIDDEN = "forbidden", "Forbidden"
        FAILED = "failed", "Failed"

    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, related_name="audit_events", blank=True, null=True)
    action = models.CharField(max_length=64)
    target_app = models.CharField(max_length=64)
    target_model = models.CharField(max_length=64)
    target_id = models.CharField(max_length=64, blank=True)
    object_repr = models.CharField(max_length=255)
    channel = models.CharField(max_length=32, choices=Channel.choices, default=Channel.SERVICE)
    outcome = models.CharField(max_length=16, choices=Outcome.choices, default=Outcome.SUCCESS)
    request_method = models.CharField(max_length=16, blank=True)
    request_path = models.CharField(max_length=255, blank=True)
    student = models.ForeignKey("students.Student", on_delete=models.SET_NULL, related_name="audit_events", blank=True, null=True)
    exam = models.ForeignKey("results.Exam", on_delete=models.SET_NULL, related_name="audit_events", blank=True, null=True)
    sensitive = models.BooleanField(default=True)
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    metadata = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-occurred_at"]
        indexes = [
            models.Index(fields=["action", "occurred_at"]),
            models.Index(fields=["target_app", "target_model", "target_id"]),
            models.Index(fields=["actor", "occurred_at"]),
            models.Index(fields=["student", "occurred_at"]),
            models.Index(fields=["exam", "occurred_at"]),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Audit events are append-only and cannot be modified.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Audit events are append-only and cannot be deleted.")

    def __str__(self):
        return f"{self.action} {self.target_model}:{self.target_id}"
