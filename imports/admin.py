from django.contrib import admin, messages

from audits.admin_mixins import AuditLoggedAdminMixin, RoleProtectedAdminMixin
from audits.models import AuditEvent
from audits.roles import Role, enforce_role_access, user_has_any_role
from imports.models import ResultUpload, ResultUploadRow, publish_result_upload, stage_result_upload


IMPORT_VIEW_ROLES = (Role.REGISTRY_ADMIN, Role.RESULTS_OFFICER, Role.AUDITOR)
IMPORT_MUTATION_ROLES = (Role.REGISTRY_ADMIN, Role.RESULTS_OFFICER)


class ResultUploadRowInline(admin.TabularInline):
    model = ResultUploadRow
    extra = 0
    can_delete = False
    fields = ("row_number", "registration_number", "student", "raw_score", "grade", "status", "is_absent", "is_valid", "is_duplicate", "duplicate_reason")
    readonly_fields = fields
    show_change_link = True


@admin.register(ResultUpload)
class ResultUploadAdmin(AuditLoggedAdminMixin, admin.ModelAdmin):
    view_roles = IMPORT_VIEW_ROLES
    add_roles = IMPORT_MUTATION_ROLES
    change_roles = IMPORT_MUTATION_ROLES
    delete_roles = (Role.REGISTRY_ADMIN,)
    list_display = ("source_filename", "exam", "status", "total_rows", "staged_rows", "imported_rows", "rejected_rows", "uploaded_by", "started_at")
    list_filter = ("status", "started_at")
    search_fields = ("source_filename", "checksum_sha256", "exam__title", "exam__module__code")
    autocomplete_fields = ("exam", "uploaded_by", "published_by")
    readonly_fields = ("checksum_sha256", "source_filename", "uploaded_by", "published_by", "started_at", "staged_at", "completed_at", "published_at", "total_rows", "staged_rows", "imported_rows", "rejected_rows", "processing_error")
    inlines = (ResultUploadRowInline,)
    actions = ("stage_selected_uploads", "publish_selected_uploads")

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not user_has_any_role(request.user, IMPORT_MUTATION_ROLES):
            actions.pop("stage_selected_uploads", None)
            actions.pop("publish_selected_uploads", None)
        return actions

    def save_model(self, request, obj, form, change):
        if not change and request.user.is_authenticated and obj.uploaded_by_id is None:
            obj.uploaded_by = request.user
        if obj.source_file:
            obj.refresh_file_metadata()
        super().save_model(request, obj, form, change)

    @admin.action(description="Stage selected Excel uploads")
    def stage_selected_uploads(self, request, queryset):
        for upload in queryset:
            enforce_role_access(request, roles=IMPORT_MUTATION_ROLES, action="imports.results.stage", instance=upload, channel=AuditEvent.Channel.ADMIN)
            try:
                stage_result_upload(upload, request=request, channel=AuditEvent.Channel.ADMIN)
            except Exception as exc:
                self.message_user(request, f"Failed to stage {upload}: {exc}", level=messages.ERROR)
            else:
                self.message_user(request, f"Staged {upload.total_rows} row(s) for {upload}.", level=messages.SUCCESS)

    @admin.action(description="Publish staged results for selected uploads")
    def publish_selected_uploads(self, request, queryset):
        for upload in queryset:
            enforce_role_access(request, roles=IMPORT_MUTATION_ROLES, action="imports.results.publish", instance=upload, channel=AuditEvent.Channel.ADMIN)
            try:
                published_count = publish_result_upload(upload, actor=request.user if request.user.is_authenticated else None, request=request, channel=AuditEvent.Channel.ADMIN)
            except Exception as exc:
                self.message_user(request, f"Failed to publish {upload}: {exc}", level=messages.ERROR)
            else:
                self.message_user(request, f"Published {published_count} staged row(s) for {upload}.", level=messages.SUCCESS)


@admin.register(ResultUploadRow)
class ResultUploadRowAdmin(RoleProtectedAdminMixin, admin.ModelAdmin):
    view_roles = IMPORT_VIEW_ROLES
    delete_roles = (Role.REGISTRY_ADMIN,)
    list_display = ("upload", "row_number", "registration_number", "student", "raw_score", "status", "is_valid", "is_duplicate")
    list_filter = ("status", "is_valid", "is_duplicate", "upload__status")
    search_fields = ("registration_number", "upload__source_filename", "student__registration_number")
    autocomplete_fields = ("upload", "student")
    readonly_fields = ("upload", "row_number", "registration_number", "student", "raw_score", "grade", "remarks", "status", "is_absent", "is_valid", "is_duplicate", "duplicate_reason", "validation_errors", "raw_payload", "created_at", "updated_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return super().has_delete_permission(request, obj)
