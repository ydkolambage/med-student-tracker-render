from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError

from audits.admin_mixins import AuditLoggedAdminMixin, RoleProtectedAdminMixin
from audits.models import AuditEvent
from audits.roles import Role, enforce_role_access, user_has_any_role
from audits.utils import export_exam_results, record_audit_event
from results.models import Exam, ExamResult, ResultCorrection
from results.services import apply_result_correction, release_exam_results


RESULTS_VIEW_ROLES = (Role.REGISTRY_ADMIN, Role.RESULTS_OFFICER, Role.VIEWER, Role.AUDITOR)
RESULTS_MUTATION_ROLES = (Role.REGISTRY_ADMIN, Role.RESULTS_OFFICER)
RESULTS_EXPORT_ROLES = (Role.REGISTRY_ADMIN, Role.RESULTS_OFFICER, Role.AUDITOR)


@admin.register(Exam)
class ExamAdmin(AuditLoggedAdminMixin, admin.ModelAdmin):
    view_roles = RESULTS_VIEW_ROLES
    add_roles = RESULTS_MUTATION_ROLES
    change_roles = RESULTS_MUTATION_ROLES
    delete_roles = (Role.REGISTRY_ADMIN,)
    list_display = ('title', 'module', 'batch', 'sat_on', 'maximum_score', 'pass_mark', 'release_state', 'release_version')
    list_filter = ('batch', 'sat_on', 'release_state')
    search_fields = ('title', 'module__code', 'module__title', 'batch__code', 'batch__display_name')
    autocomplete_fields = ('module', 'batch')
    date_hierarchy = 'sat_on'
    readonly_fields = ('weight_percentage', 'release_state', 'release_version', 'results_released_at')
    actions = ('release_selected_exams',)

    def has_change_permission(self, request, obj=None):
        if obj is not None and obj.is_released:
            return self.has_view_permission(request, obj)
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return super().has_delete_permission(request, obj)

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not user_has_any_role(request.user, RESULTS_MUTATION_ROLES):
            actions.pop('release_selected_exams', None)
        return actions

    @admin.action(description='Release selected exam results')
    def release_selected_exams(self, request, queryset):
        for exam in queryset:
            enforce_role_access(request, roles=RESULTS_MUTATION_ROLES, action='results.exam.release', instance=exam, channel=AuditEvent.Channel.ADMIN)
            try:
                release_exam_results(exam, actor=request.user if request.user.is_authenticated else None, request=request, channel=AuditEvent.Channel.ADMIN)
            except ValidationError as exc:
                self.message_user(request, f'Failed to release {exam}: {exc}', level=messages.ERROR)
                record_audit_event(action='results.exam.release', actor=request.user if request.user.is_authenticated else None, instance=exam, request=request, channel=AuditEvent.Channel.ADMIN, outcome=AuditEvent.Outcome.FAILED, metadata={'error': exc.message})
            else:
                self.message_user(request, f'Released results for {exam}.', level=messages.SUCCESS)


@admin.register(ExamResult)
class ExamResultAdmin(AuditLoggedAdminMixin, admin.ModelAdmin):
    view_roles = RESULTS_VIEW_ROLES
    add_roles = RESULTS_MUTATION_ROLES
    change_roles = RESULTS_MUTATION_ROLES
    delete_roles = (Role.REGISTRY_ADMIN,)
    list_display = ('student', 'exam', 'raw_score', 'percentage', 'grade', 'status', 'is_absent', 'is_withheld')
    list_filter = ('status', 'exam__batch')
    search_fields = ('student__registration_number', 'student__first_name', 'student__last_name', 'exam__title')
    autocomplete_fields = ('exam', 'student', 'upload')
    readonly_fields = ('percentage',)
    actions = ('export_selected_results',)

    def has_change_permission(self, request, obj=None):
        if obj is not None and obj.exam.is_released:
            return self.has_view_permission(request, obj)
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return super().has_delete_permission(request, obj)

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not user_has_any_role(request.user, RESULTS_EXPORT_ROLES):
            actions.pop('export_selected_results', None)
        return actions

    @admin.action(description='Export selected results as CSV')
    def export_selected_results(self, request, queryset):
        exam_ids = list(queryset.values_list('exam_id', flat=True).distinct())
        if len(exam_ids) != 1:
            record_audit_event(action='results.export', actor=request.user if request.user.is_authenticated else None, request=request, channel=AuditEvent.Channel.ADMIN, outcome=AuditEvent.Outcome.FAILED, target_app='results', target_model='examresult', object_repr='multi-exam export', metadata={'reason': 'multiple_exams_selected'})
            self.message_user(request, 'Select results for exactly one exam to export.', level='error')
            return None
        exam = Exam.objects.get(pk=exam_ids[0])
        enforce_role_access(request, roles=RESULTS_EXPORT_ROLES, action='results.export', instance=exam, channel=AuditEvent.Channel.ADMIN)
        return export_exam_results(exam, actor=request.user if request.user.is_authenticated else None, request=request)


@admin.register(ResultCorrection)
class ResultCorrectionAdmin(RoleProtectedAdminMixin, admin.ModelAdmin):
    view_roles = (Role.REGISTRY_ADMIN, Role.RESULTS_OFFICER, Role.AUDITOR)
    add_roles = RESULTS_MUTATION_ROLES
    delete_roles = (Role.REGISTRY_ADMIN,)
    list_display = ('exam_result', 'new_status', 'requested_by', 'applied_by', 'created_at', 'applied_at')
    list_filter = ('new_status', 'created_at', 'applied_at')
    search_fields = ('exam_result__student__registration_number', 'exam_result__exam__title', 'reason')
    autocomplete_fields = ('exam_result', 'requested_by', 'applied_by')
    readonly_fields = ('requested_by', 'applied_by', 'previous_values', 'applied_values', 'created_at', 'applied_at')

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return super().has_delete_permission(request, obj)

    def save_model(self, request, obj, form, change):
        if change:
            raise PermissionDenied
        obj.requested_by = request.user if request.user.is_authenticated else None
        super().save_model(request, obj, form, change)
        apply_result_correction(obj, actor=request.user if request.user.is_authenticated else None, request=request, channel=AuditEvent.Channel.ADMIN)
