from audits.models import AuditEvent
from audits.roles import user_has_any_role
from audits.utils import record_audit_event


class RoleProtectedAdminMixin:
    view_roles = ()
    add_roles = ()
    change_roles = ()
    delete_roles = ()
    audit_channel = AuditEvent.Channel.ADMIN

    def _all_roles(self):
        return tuple({*self.view_roles, *self.add_roles, *self.change_roles, *self.delete_roles})

    def _has_role_access(self, request, roles):
        return user_has_any_role(request.user, roles)

    def has_module_permission(self, request):
        return self._has_role_access(request, self._all_roles())

    def has_view_permission(self, request, obj=None):
        return self._has_role_access(request, self._all_roles())

    def has_add_permission(self, request):
        return self._has_role_access(request, self.add_roles)

    def has_change_permission(self, request, obj=None):
        return self._has_role_access(request, self.change_roles)

    def has_delete_permission(self, request, obj=None):
        return self._has_role_access(request, self.delete_roles)


class AuditLoggedAdminMixin(RoleProtectedAdminMixin):
    def build_audit_metadata(self, request, obj, action):
        return {
            "channel": self.audit_channel,
            "admin_class": self.__class__.__name__,
            "operation": action,
        }

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        record_audit_event(
            action="admin.object.update" if change else "admin.object.create",
            actor=request.user if request.user.is_authenticated else None,
            instance=obj,
            request=request,
            channel=self.audit_channel,
            metadata=self.build_audit_metadata(request, obj, "update" if change else "create"),
        )

    def delete_model(self, request, obj):
        record_audit_event(
            action="admin.object.delete",
            actor=request.user if request.user.is_authenticated else None,
            instance=obj,
            request=request,
            channel=self.audit_channel,
            metadata=self.build_audit_metadata(request, obj, "delete"),
        )
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        for obj in queryset:
            record_audit_event(
                action="admin.object.bulk_delete",
                actor=request.user if request.user.is_authenticated else None,
                instance=obj,
                request=request,
                channel=self.audit_channel,
                metadata=self.build_audit_metadata(request, obj, "bulk_delete"),
            )
        super().delete_queryset(request, queryset)
