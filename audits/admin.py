from django.contrib import admin

from audits.admin_mixins import RoleProtectedAdminMixin
from audits.models import AuditEvent
from audits.roles import Role


@admin.register(AuditEvent)
class AuditEventAdmin(RoleProtectedAdminMixin, admin.ModelAdmin):
    view_roles = (Role.REGISTRY_ADMIN, Role.AUDITOR)
    list_display = ("occurred_at", "action", "channel", "outcome", "actor", "student", "exam", "target_model", "target_id")
    list_filter = ("action", "channel", "outcome", "sensitive", "actor", "student", "exam", "target_app", "target_model")
    search_fields = ("object_repr", "target_id", "actor__username", "student__registration_number", "exam__title", "exam__module__code")
    readonly_fields = ("occurred_at", "actor", "action", "channel", "outcome", "request_method", "request_path", "student", "exam", "target_app", "target_model", "target_id", "object_repr", "sensitive", "ip_address", "metadata")
    date_hierarchy = "occurred_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
