from django.contrib import admin

from audits.admin_mixins import AuditLoggedAdminMixin
from audits.roles import Role
from students.models import Batch, Department, Module, Student


STUDENT_VIEW_ROLES = (Role.REGISTRY_ADMIN, Role.RESULTS_OFFICER, Role.VIEWER, Role.AUDITOR)
STUDENT_MUTATION_ROLES = (Role.REGISTRY_ADMIN,)


@admin.register(Department)
class DepartmentAdmin(AuditLoggedAdminMixin, admin.ModelAdmin):
    view_roles = STUDENT_VIEW_ROLES
    add_roles = STUDENT_MUTATION_ROLES
    change_roles = STUDENT_MUTATION_ROLES
    delete_roles = STUDENT_MUTATION_ROLES
    list_display = ('code', 'name', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('code', 'name')


@admin.register(Batch)
class BatchAdmin(AuditLoggedAdminMixin, admin.ModelAdmin):
    view_roles = STUDENT_VIEW_ROLES
    add_roles = STUDENT_MUTATION_ROLES
    change_roles = STUDENT_MUTATION_ROLES
    delete_roles = STUDENT_MUTATION_ROLES
    list_display = ('code', 'display_name', 'academic_start_year', 'graduation_year', 'is_active')
    list_filter = ('is_active', 'academic_start_year')
    search_fields = ('code', 'display_name')


@admin.register(Student)
class StudentAdmin(AuditLoggedAdminMixin, admin.ModelAdmin):
    view_roles = STUDENT_VIEW_ROLES
    add_roles = STUDENT_MUTATION_ROLES
    change_roles = STUDENT_MUTATION_ROLES
    delete_roles = STUDENT_MUTATION_ROLES
    list_display = ('registration_number', 'full_name', 'batch', 'status', 'university_email')
    list_filter = ('status', 'batch')
    search_fields = ('registration_number', 'first_name', 'last_name', 'university_email')
    autocomplete_fields = ('batch',)


@admin.register(Module)
class ModuleAdmin(AuditLoggedAdminMixin, admin.ModelAdmin):
    view_roles = STUDENT_VIEW_ROLES
    add_roles = STUDENT_MUTATION_ROLES
    change_roles = STUDENT_MUTATION_ROLES
    delete_roles = STUDENT_MUTATION_ROLES
    list_display = ('code', 'title', 'department', 'enrolled_batches_summary', 'semester', 'is_active')
    list_filter = ('department', 'semester', 'is_active', 'batches')
    search_fields = ('code', 'title', 'department__code', 'department__name', 'batches__code', 'batches__display_name')
    autocomplete_fields = ('department', 'batches')
