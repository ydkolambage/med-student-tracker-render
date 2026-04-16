from django.contrib import admin
from django.contrib.auth.views import LogoutView, PasswordResetCompleteView, PasswordResetConfirmView, PasswordResetDoneView, PasswordResetView
from django.urls import path

from audits.views import audit_report
from config.views import AuditedLoginView, health_live, health_ready, home
from imports.views import (
    imports_home,
    publish_student_upload_summary,
    stage_exam_upload,
    stage_result_import,
    stage_student_import,
    student_upload_review,
    student_upload_review_export,
    upload_review,
    upload_review_export,
)
from results.views import cohort_export, cohort_overview, exam_detail, exam_results_export, publish_upload_summary, staff_dashboard
from students.views import student_profile, student_search

admin.site.site_url = '/staff/'

urlpatterns = [
    path('', home, name='home'),
    path('health/live/', health_live, name='health-live'),
    path('health/ready/', health_ready, name='health-ready'),
    path('login/', AuditedLoginView.as_view(), name='login'),
    path('logout/', LogoutView.as_view(next_page='login'), name='logout'),
    path(
        'password-reset/',
        PasswordResetView.as_view(
            template_name='registration/password_reset_form.html',
            email_template_name='registration/password_reset_email.html',
            subject_template_name='registration/password_reset_subject.txt',
            success_url='/password-reset/done/',
        ),
        name='password_reset',
    ),
    path(
        'password-reset/done/',
        PasswordResetDoneView.as_view(template_name='registration/password_reset_done.html'),
        name='password_reset_done',
    ),
    path(
        'reset/<uidb64>/<token>/',
        PasswordResetConfirmView.as_view(template_name='registration/password_reset_confirm.html', success_url='/reset/complete/'),
        name='password_reset_confirm',
    ),
    path(
        'reset/complete/',
        PasswordResetCompleteView.as_view(template_name='registration/password_reset_complete.html'),
        name='password_reset_complete',
    ),
    path('staff/', staff_dashboard, name='staff-dashboard'),
    path('staff/imports/', imports_home, name='imports-home'),
    path('staff/imports/students/', stage_student_import, name='student-import-new'),
    path('staff/imports/results/', stage_result_import, name='result-import-new'),
    path('staff/students/search/', student_search, name='student-search'),
    path('staff/students/import/', stage_student_import),
    path('staff/students/imports/<int:upload_id>/', student_upload_review, name='student-upload-review'),
    path('staff/students/imports/<int:upload_id>/export/', student_upload_review_export, name='student-upload-review-export'),
    path('staff/students/imports/<int:upload_id>/publish/', publish_student_upload_summary, name='student-upload-publish-summary'),
    path('staff/students/<int:student_id>/', student_profile, name='student-profile'),
    path('staff/exams/<int:exam_id>/', exam_detail, name='exam-detail'),
    path('staff/exams/<int:exam_id>/upload/', stage_exam_upload, name='exam-upload-new'),
    path('staff/exams/<int:exam_id>/export/', exam_results_export, name='exam-results-export'),
    path('staff/uploads/<int:upload_id>/', upload_review, name='upload-review'),
    path('staff/uploads/<int:upload_id>/export/', upload_review_export, name='upload-review-export'),
    path('staff/uploads/<int:upload_id>/publish/', publish_upload_summary, name='upload-publish-summary'),
    path('staff/cohorts/', cohort_overview, name='cohort-overview'),
    path('staff/cohorts/export/', cohort_export, name='cohort-export'),
    path('audits/report/', audit_report, name='audit-report'),
    path('admin/', admin.site.urls),
]
