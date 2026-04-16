import csv
from io import StringIO

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Avg, Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify

from audits.models import AuditEvent
from audits.roles import Role, enforce_role_access
from audits.utils import record_audit_event
from imports.models import ResultUpload, publish_result_upload
from results.models import Exam, ExamResult
from students.models import Batch, Module, Student


STAFF_READ_ROLES = (Role.REGISTRY_ADMIN, Role.RESULTS_OFFICER, Role.VIEWER, Role.AUDITOR)
STAFF_MUTATION_ROLES = (Role.REGISTRY_ADMIN, Role.RESULTS_OFFICER)
STAFF_EXPORT_ROLES = (Role.REGISTRY_ADMIN, Role.RESULTS_OFFICER, Role.AUDITOR)
DASHBOARD_SEARCH_OPTIONS = (
    ('registration_number', 'Registration No'),
    ('stream', 'Stream'),
    ('subject', 'Subject'),
    ('batch', 'Batch'),
)
DASHBOARD_SEARCH_PLACEHOLDERS = {
    'registration_number': 'Search registration no',
    'stream': 'Search stream code',
    'subject': 'Search subject title',
    'batch': 'Search batch code or name',
}


def _result_label(result, exam=None):
    grade = (result.grade or '').strip()
    if grade.lower() in {'pass', 'fail'}:
        return grade.title()
    target_exam = exam or getattr(result, 'exam', None)
    if result.status == ExamResult.Status.RECORDED and result.raw_score is not None and target_exam is not None:
        return 'Pass' if result.raw_score >= target_exam.pass_mark else 'Fail'
    return '-'


def _filtered_results(exam, request):
    result_filter = request.GET.get('result_filter', 'all').strip().lower() or 'all'
    results = list(exam.results.select_related('student', 'upload').order_by('student__registration_number'))
    unpublished_students = Student.objects.filter(batch=exam.batch).exclude(exam_results__exam=exam).order_by('registration_number')
    if result_filter == 'absent':
        results = [result for result in results if result.status == ExamResult.Status.ABSENT]
    elif result_filter == 'withheld':
        results = [result for result in results if result.status == ExamResult.Status.WITHHELD]
    elif result_filter == 'failed':
        results = [result for result in results if result.status == ExamResult.Status.RECORDED and result.raw_score is not None and result.raw_score < exam.pass_mark]
    elif result_filter == 'unpublished':
        results = []
    for result in results:
        result.result_label = _result_label(result, exam=exam)
    return result_filter, results, unpublished_students


def _csv_response(rows, filename):
    buffer = StringIO()
    writer = csv.writer(buffer)
    for row in rows:
        writer.writerow(row)
    response = HttpResponse(buffer.getvalue(), content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    response['Cache-Control'] = 'private, no-store'
    response['Pragma'] = 'no-cache'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


@login_required
def staff_dashboard(request):
    enforce_role_access(request, roles=STAFF_READ_ROLES, action='staff.dashboard.view', channel=AuditEvent.Channel.WEB, target_app='config', target_model='dashboard')
    query = request.GET.get('q', '').strip()
    search_by = request.GET.get('search_by', 'registration_number').strip() or 'registration_number'
    valid_search_options = dict(DASHBOARD_SEARCH_OPTIONS)
    if search_by not in valid_search_options:
        search_by = 'registration_number'

    exams = Exam.objects.select_related('batch', 'module', 'module__department').annotate(result_count=Count('results'), upload_count=Count('uploads', distinct=True)).order_by('-sat_on', 'module__code')
    students = Student.objects.select_related('batch').order_by('registration_number')
    if query:
        if search_by == 'stream':
            exams = exams.filter(module__code__icontains=query)
            students = students.filter(batch__streams__code__icontains=query).distinct()
        elif search_by == 'subject':
            exams = exams.filter(module__title__icontains=query)
            students = students.filter(batch__streams__title__icontains=query).distinct()
        elif search_by == 'batch':
            exams = exams.filter(Q(batch__code__icontains=query) | Q(batch__display_name__icontains=query)).distinct()
            students = students.filter(Q(batch__code__icontains=query) | Q(batch__display_name__icontains=query)).distinct()
        else:
            exams = exams.none()
            students = students.filter(registration_number__icontains=query)
    else:
        students = students[:8]
        exams = exams[:10]

    pending_uploads = ResultUpload.objects.select_related('exam', 'exam__module', 'exam__batch', 'uploaded_by').exclude(status=ResultUpload.Status.PUBLISHED).order_by('-started_at')[:8]
    batches = Batch.objects.order_by('-academic_start_year', 'code')[:8]
    streams = Module.objects.select_related('department').order_by('code')[:8]
    return render(request, 'staff/dashboard.html', {'query': query, 'search_by': search_by, 'search_options': DASHBOARD_SEARCH_OPTIONS, 'search_placeholder': DASHBOARD_SEARCH_PLACEHOLDERS[search_by], 'exams': exams, 'students': students, 'pending_uploads': pending_uploads, 'batches': batches, 'streams': streams})


@login_required
def exam_detail(request, exam_id):
    exam = get_object_or_404(Exam.objects.select_related('batch', 'module', 'module__department'), pk=exam_id)
    enforce_role_access(request, roles=STAFF_READ_ROLES, action='results.exam.detail', instance=exam, channel=AuditEvent.Channel.WEB)
    result_filter, results, unpublished_students = _filtered_results(exam, request)
    uploads = list(exam.uploads.select_related('uploaded_by', 'published_by').order_by('-version_number', '-started_at'))
    latest_upload = uploads[0] if uploads else None
    previous_upload = uploads[1] if len(uploads) > 1 else None
    summary = {
        'published_results': exam.results.count(),
        'absent_results': exam.results.filter(status=ExamResult.Status.ABSENT).count(),
        'withheld_results': exam.results.filter(status=ExamResult.Status.WITHHELD).count(),
        'failed_results': exam.results.filter(status=ExamResult.Status.RECORDED, raw_score__lt=exam.pass_mark).count(),
        'unpublished_results': unpublished_students.count(),
    }
    cohort_stats = exam.results.aggregate(average_percentage=Avg('percentage'))
    record_audit_event(action='results.exam.detail', actor=request.user, instance=exam, request=request, channel=AuditEvent.Channel.WEB, metadata={'result_filter': result_filter})
    return render(request, 'results/exam_detail.html', {'exam': exam, 'results': results, 'result_filter': result_filter, 'summary': summary, 'unpublished_students': unpublished_students, 'uploads': uploads, 'latest_upload': latest_upload, 'previous_upload': previous_upload, 'cohort_average': cohort_stats['average_percentage']})


@login_required
def publish_upload_summary(request, upload_id):
    upload = get_object_or_404(ResultUpload.objects.select_related('exam', 'exam__module', 'exam__batch', 'uploaded_by'), pk=upload_id)
    enforce_role_access(request, roles=STAFF_MUTATION_ROLES, action='imports.results.publish.summary', instance=upload, channel=AuditEvent.Channel.WEB)
    previous_upload = upload.exam.uploads.exclude(pk=upload.pk).order_by('-version_number', '-started_at').first()
    if request.method == 'POST':
        published_count = publish_result_upload(upload, actor=request.user, request=request, channel=AuditEvent.Channel.WEB)
        messages.success(request, f'Published {published_count} result row(s) from upload v{upload.version_number}.')
        return redirect('exam-detail', exam_id=upload.exam_id)
    summary = upload.summary
    record_audit_event(action='imports.results.publish.summary', actor=request.user, instance=upload, request=request, channel=AuditEvent.Channel.WEB, metadata=summary)
    return render(request, 'results/publish_summary.html', {'upload': upload, 'summary': summary, 'previous_upload': previous_upload})


@login_required
def exam_results_export(request, exam_id):
    exam = get_object_or_404(Exam.objects.select_related('batch', 'module', 'module__department'), pk=exam_id)
    enforce_role_access(request, roles=STAFF_EXPORT_ROLES, action='results.export', instance=exam, channel=AuditEvent.Channel.WEB)
    result_filter, results, unpublished_students = _filtered_results(exam, request)
    if result_filter == 'unpublished':
        rows = [['registration_number', 'student_name', 'batch', 'reason']]
        for student in unpublished_students:
            rows.append([student.registration_number, student.full_name, student.batch.display_name, 'No published result for this exam'])
    else:
        rows = [['registration_number', 'student_name', 'percentage', 'result']]
        for result in results:
            rows.append([result.student.registration_number, result.student.full_name, '' if result.percentage is None else str(result.percentage), result.result_label])
    record_audit_event(action='results.export', actor=request.user, instance=exam, request=request, channel=AuditEvent.Channel.WEB, metadata={'result_filter': result_filter, 'exported_rows': len(rows) - 1})
    return _csv_response(rows, f'{slugify(exam.module.code)}-{slugify(exam.title)}-{result_filter}.csv')


@login_required
def cohort_overview(request):
    enforce_role_access(request, roles=STAFF_READ_ROLES, action='results.cohort.view', channel=AuditEvent.Channel.WEB, target_app='results', target_model='cohort')
    batches = Batch.objects.order_by('-academic_start_year', 'code')
    streams = Module.objects.select_related('department').order_by('code')
    batch = get_object_or_404(Batch, pk=request.GET.get('batch')) if request.GET.get('batch') else None
    module = get_object_or_404(Module.objects.select_related('department'), pk=request.GET.get('module')) if request.GET.get('module') else None
    result_filter = request.GET.get('result_filter', 'all').strip().lower() or 'all'
    students = Student.objects.select_related('batch').order_by('registration_number')
    relevant_results_qs = ExamResult.objects.select_related('exam', 'student', 'exam__module', 'exam__batch')
    headline = 'Select a batch or stream / subject'
    if module and batch:
        students = students.filter(batch=batch, batch__in=module.batches.all())
        relevant_results = list(relevant_results_qs.filter(student__in=students, exam__module=module, exam__batch=batch))
        headline = f'Batch cohort for {module.code}: {batch.display_name}'
    elif module:
        students = students.filter(batch__in=module.batches.all())
        relevant_results = list(relevant_results_qs.filter(student__in=students, exam__module=module))
        headline = f'Stream / subject cohort: {module.code}'
    elif batch:
        students = students.filter(batch=batch)
        relevant_results = list(relevant_results_qs.filter(student__in=students))
        headline = f'Batch cohort: {batch.display_name}'
    else:
        relevant_results = []

    filtered_students = []
    for student in students:
        student_results = [result for result in relevant_results if result.student_id == student.id]
        has_absent = any(result.status == ExamResult.Status.ABSENT for result in student_results)
        has_withheld = any(result.status == ExamResult.Status.WITHHELD for result in student_results)
        has_failed = any(result.status == ExamResult.Status.RECORDED and result.raw_score is not None and result.raw_score < result.exam.pass_mark for result in student_results)
        is_unpublished = not student_results
        if result_filter == 'absent' and not has_absent:
            continue
        if result_filter == 'withheld' and not has_withheld:
            continue
        if result_filter == 'failed' and not has_failed:
            continue
        if result_filter == 'unpublished' and not is_unpublished:
            continue
        percentages = [result.percentage for result in student_results if result.percentage is not None]
        filtered_students.append({'student': student, 'results_count': len(student_results), 'average_percentage': sum(percentages) / len(percentages) if percentages else None, 'has_absent': has_absent, 'has_withheld': has_withheld, 'has_failed': has_failed, 'is_unpublished': is_unpublished})

    return render(request, 'results/cohort_overview.html', {'batches': batches, 'streams': streams, 'selected_batch': batch, 'selected_module': module, 'headline': headline, 'student_rows': filtered_students, 'result_filter': result_filter})


@login_required
def cohort_export(request):
    enforce_role_access(request, roles=STAFF_EXPORT_ROLES, action='results.cohort.export', channel=AuditEvent.Channel.WEB, target_app='results', target_model='cohort')
    batch = get_object_or_404(Batch, pk=request.GET.get('batch')) if request.GET.get('batch') else None
    module = get_object_or_404(Module.objects.select_related('department'), pk=request.GET.get('module')) if request.GET.get('module') else None
    result_filter = request.GET.get('result_filter', 'all').strip().lower() or 'all'
    students = Student.objects.select_related('batch')
    if module and batch:
        students = students.filter(batch=batch, batch__in=module.batches.all())
        results = list(ExamResult.objects.filter(student__in=students, exam__module=module, exam__batch=batch).select_related('exam', 'student', 'exam__module', 'exam__batch'))
        filename = f'{slugify(module.code)}-{slugify(batch.code)}-cohort-{result_filter}.csv'
    elif module:
        students = students.filter(batch__in=module.batches.all())
        results = list(ExamResult.objects.filter(student__in=students, exam__module=module).select_related('exam', 'student', 'exam__module', 'exam__batch'))
        filename = f'{slugify(module.code)}-cohort-{result_filter}.csv'
    elif batch:
        students = students.filter(batch=batch)
        results = list(ExamResult.objects.filter(student__in=students).select_related('exam', 'student', 'exam__module', 'exam__batch'))
        filename = f'{slugify(batch.code)}-cohort-{result_filter}.csv'
    else:
        students = students.none()
        results = []
        filename = 'cohort-export.csv'

    rows = [['registration_number', 'student_name', 'results_count', 'average_percentage', 'has_absent', 'has_withheld', 'has_failed', 'is_unpublished']]
    for student in students.order_by('registration_number'):
        student_results = [result for result in results if result.student_id == student.id]
        has_absent = any(result.status == ExamResult.Status.ABSENT for result in student_results)
        has_withheld = any(result.status == ExamResult.Status.WITHHELD for result in student_results)
        has_failed = any(result.status == ExamResult.Status.RECORDED and result.raw_score is not None and result.raw_score < result.exam.pass_mark for result in student_results)
        is_unpublished = not student_results
        if result_filter == 'absent' and not has_absent:
            continue
        if result_filter == 'withheld' and not has_withheld:
            continue
        if result_filter == 'failed' and not has_failed:
            continue
        if result_filter == 'unpublished' and not is_unpublished:
            continue
        percentages = [result.percentage for result in student_results if result.percentage is not None]
        rows.append([student.registration_number, student.full_name, len(student_results), '' if not percentages else str(sum(percentages) / len(percentages)), has_absent, has_withheld, has_failed, is_unpublished])

    record_audit_event(action='results.cohort.export', actor=request.user, request=request, channel=AuditEvent.Channel.WEB, target_app='results', target_model='cohort', object_repr=module.code if module else batch.code if batch else 'empty', metadata={'result_filter': result_filter, 'exported_rows': len(rows) - 1})
    return _csv_response(rows, filename)
