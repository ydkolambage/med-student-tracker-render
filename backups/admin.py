from zoneinfo import ZoneInfo

from django.contrib import admin, messages
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.db import transaction
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.utils import timezone
from django.urls import path, reverse

from audits.admin_mixins import RoleProtectedAdminMixin
from audits.models import AuditEvent
from audits.roles import Role, enforce_role_access, user_has_any_role
from audits.utils import record_audit_event
from backups.forms import ExistingBackupRegistrationForm
from backups.models import BackupRecord
from backups.services import BackupError, delete_backup_artifact, perform_full_system_backup, register_existing_backup, restore_database_backup


@admin.register(BackupRecord)
class BackupRecordAdmin(RoleProtectedAdminMixin, admin.ModelAdmin):
    view_roles = (Role.REGISTRY_ADMIN, Role.AUDITOR)
    delete_roles = (Role.REGISTRY_ADMIN,)
    change_list_template = "admin/backups/backuprecord/change_list.html"
    list_display = ("created_at_local", "backup_type", "status", "initiated_by", "filesystem_path")
    list_filter = ("backup_type", "status", "created_at")
    search_fields = ("filesystem_path", "error_message", "initiated_by__username")
    readonly_fields = ("initiated_by", "created_at", "backup_type", "filesystem_path", "status", "error_message")
    date_hierarchy = "created_at"
    actions = ("restore_selected_sql_backup", "delete_selected")

    @admin.display(description="Created at", ordering="created_at")
    def created_at_local(self, obj):
        backup_timezone = ZoneInfo(self._backup_time_zone())
        return timezone.localtime(obj.created_at, backup_timezone).strftime("%Y-%m-%d %I:%M:%S %p")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return user_has_any_role(request.user, (Role.REGISTRY_ADMIN,))

    def get_urls(self):
        return [
            path("run-full-backup/", self.admin_site.admin_view(self.run_full_backup_view), name="backups_backuprecord_run_full_backup"),
            path("register-existing/", self.admin_site.admin_view(self.register_existing_backup_view), name="backups_backuprecord_register_existing"),
        ] + super().get_urls()

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context["can_run_full_backup"] = user_has_any_role(request.user, (Role.REGISTRY_ADMIN,))
        extra_context["run_full_backup_url"] = reverse("admin:backups_backuprecord_run_full_backup")
        extra_context["can_register_existing_backup"] = user_has_any_role(request.user, (Role.REGISTRY_ADMIN,))
        extra_context["register_existing_backup_url"] = reverse("admin:backups_backuprecord_register_existing")
        return super().changelist_view(request, extra_context=extra_context)

    def run_full_backup_view(self, request):
        enforce_role_access(request, roles=(Role.REGISTRY_ADMIN,), action="backups.full.run", channel=AuditEvent.Channel.ADMIN, target_app="backups", target_model="backuprecord", object_repr="Run full backup")

        if request.method == "POST":
            result = perform_full_system_backup(initiated_by=request.user, request=request, channel=AuditEvent.Channel.ADMIN)
            if result.succeeded:
                self.message_user(request, f"Full backup created at {result.output_path}.", level=messages.SUCCESS)
            else:
                self.message_user(request, f"Full backup failed: {result.record.error_message}", level=messages.ERROR)
            return redirect("admin:backups_backuprecord_changelist")

        context = {**self.admin_site.each_context(request), "opts": self.model._meta, "title": "Run full system backup", "subtitle": None, "run_full_backup_url": reverse("admin:backups_backuprecord_run_full_backup"), "changelist_url": reverse("admin:backups_backuprecord_changelist")}
        return TemplateResponse(request, "admin/backups/backuprecord/run_full_backup.html", context)

    def register_existing_backup_view(self, request):
        enforce_role_access(request, roles=(Role.REGISTRY_ADMIN,), action="backups.record.register", channel=AuditEvent.Channel.ADMIN, target_app="backups", target_model="backuprecord", object_repr="Register existing backup")

        form = ExistingBackupRegistrationForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            try:
                record = register_existing_backup(
                    backup_type=form.cleaned_data["backup_type"],
                    filesystem_path=form.cleaned_data["filesystem_path"],
                    initiated_by=request.user if request.user.is_authenticated else None,
                    request=request,
                    channel=AuditEvent.Channel.ADMIN,
                )
            except BackupError as exc:
                form.add_error("filesystem_path", str(exc))
            else:
                self.message_user(request, f"Registered existing backup at {record.filesystem_path}.", level=messages.SUCCESS)
                return redirect("admin:backups_backuprecord_changelist")

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": "Register existing backup",
            "subtitle": None,
            "form": form,
            "changelist_url": reverse("admin:backups_backuprecord_changelist"),
            "full_backup_root": str(self._full_backup_root()),
            "daily_sql_root": str(self._daily_sql_root()),
        }
        return TemplateResponse(request, "admin/backups/backuprecord/register_existing_backup.html", context)

    @admin.action(description="Restore selected SQL backup")
    def restore_selected_sql_backup(self, request, queryset):
        enforce_role_access(request, roles=(Role.REGISTRY_ADMIN,), action="backups.sql.restore", channel=AuditEvent.Channel.ADMIN, target_app="backups", target_model="backuprecord", object_repr="Restore SQL backup")
        selected_records = list(queryset)
        if len(selected_records) != 1:
            self.message_user(request, "Select exactly one backup entry to restore.", level=messages.ERROR)
            return None

        record = selected_records[0]
        if "apply" in request.POST:
            try:
                restored_path = restore_database_backup(record, actor=request.user if request.user.is_authenticated else None, request=request, channel=AuditEvent.Channel.ADMIN)
            except BackupError as exc:
                self.message_user(request, f"SQL restore failed: {exc}", level=messages.ERROR)
            else:
                self.message_user(request, f"Database restored from {restored_path}.", level=messages.SUCCESS)
            return None

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": "Confirm SQL restore",
            "queryset": [record],
            "action_checkbox_name": ACTION_CHECKBOX_NAME,
            "action_name": "restore_selected_sql_backup",
            "record": record,
        }
        return TemplateResponse(request, "admin/backups/backuprecord/restore_confirmation.html", context)

    def _delete_backup_record(self, request, record):
        delete_backup_artifact(record)
        record_audit_event(action="backups.artifact.delete", actor=request.user if request.user.is_authenticated else None, instance=record, request=request, channel=AuditEvent.Channel.ADMIN, metadata={"filesystem_path": record.filesystem_path, "backup_type": record.backup_type})
        record.delete()

    def delete_model(self, request, obj):
        self._delete_backup_record(request, obj)

    def delete_queryset(self, request, queryset):
        records = list(queryset)
        with transaction.atomic():
            for record in records:
                self._delete_backup_record(request, record)

    def response_delete(self, request, obj_display, obj_id):
        self.message_user(request, f"Backup entry and stored files for {obj_display} were deleted.", level=messages.SUCCESS)
        return super().response_delete(request, obj_display, obj_id)

    def delete_view(self, request, object_id, extra_context=None):
        try:
            return super().delete_view(request, object_id, extra_context=extra_context)
        except BackupError as exc:
            record_audit_event(action="backups.artifact.delete", actor=request.user if request.user.is_authenticated else None, request=request, channel=AuditEvent.Channel.ADMIN, outcome=AuditEvent.Outcome.FAILED, target_app="backups", target_model="backuprecord", target_id=object_id, object_repr="Backup delete", metadata={"error": str(exc)})
            self.message_user(request, f"Backup delete failed: {exc}", level=messages.ERROR)
            return redirect("admin:backups_backuprecord_changelist")

    def _backup_time_zone(self):
        from django.conf import settings
        return settings.BACKUP_TIME_ZONE

    def _full_backup_root(self):
        from django.conf import settings
        return settings.BACKUP_FULL_ROOT

    def _daily_sql_root(self):
        from django.conf import settings
        return settings.BACKUP_DAILY_SQL_ROOT

    def response_action(self, request, queryset):
        try:
            return super().response_action(request, queryset)
        except BackupError as exc:
            self.message_user(request, f"Backup action failed: {exc}", level=messages.ERROR)
            return redirect(request.get_full_path())
