import csv
import re
from collections import Counter
from io import StringIO

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.text import slugify

from audits.models import AuditEvent
from audits.roles import Role, enforce_role_access
from audits.utils import record_audit_event
from imports.models import ResultUpload, StudentUpload, publish_student_upload, stage_result_upload, stage_student_upload
from results.models import Exam
from students.models import Batch, Module


STAFF_READ_ROLES = (Role.REGISTRY_ADMIN, Role.RESULTS_OFFICER, Role.VIEWER, Role.AUDITOR)
STAFF_MUTATION_ROLES = (Role.REGISTRY_ADMIN, Role.RESULTS_OFFICER)
STAFF_EXPORT_ROLES = (Role.REGISTRY_ADMIN, Role.RESULTS_OFFICER, Role.AUDITOR)
BATCH_NUMBER_PATTERN = re.compile(r'(\d+)')


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


def _batch_number_value(batch):
    for candidate in (batch.display_name, batch.code):
        match = BATCH_NUMBER_PATTERN.search(str(candidate or ''))
        if match:
            return int(match.group(1))
    return batch.pk


def _batch_choice_rows():
    batches = list(Batch.objects.order_by('academic_start_year', 'code', 'pk'))
    rows = [
        {
            'id': batch.id,
            'number': _batch_number_value(batch),
            'label': str(_batch_number_value(batch)),
            'display_name': batch.display_name,
        }
        for batch in batches
    ]
    return sorted(rows, key=lambda row: (row['number'], row['id']))


def _module_choice_rows(_selected_batch_id=''):
    modules = Module.objects.select_related('department').prefetch_related('batches').order_by('code')
    return [
        {
            'id': module.id,
            'label': f'{module.code} - {module.title}',
            'department': module.department.name,
            'batch_ids': list(module.batches.order_by('pk').values_list('pk', flat=True)),
        }
        for module in modules
    ]


def _exam_choice_rows(selected_module_id='', selected_batch_id=''):
    exams = Exam.objects.select_related('module', 'batch').order_by('-sat_on', 'title')
    if selected_module_id:
        exams = exams.filter(module_id=selected_module_id)
    if selected_batch_id:
        exams = exams.filter(batch_id=selected_batch_id)
    return [
        {
            'id': exam.id,
            'label': f'{exam.title} - {exam.sat_on}',
            'module_id': exam.module_id,
            'batch_id': exam.batch_id,
        }
        for exam in exams
    ]


def _resolve_selected_batch(selected_batch_id, new_batch_number):
    if new_batch_number:
        try:
            batch_number = int(new_batch_number)
        except (TypeError, ValueError):
            raise ValidationError('Batch number must be a whole number.')
        if batch_number < 1:
            raise ValidationError('Batch number must be at least 1.')
        for batch in Batch.objects.all():
            if _batch_number_value(batch) == batch_number:
                return batch, False
        return Batch.objects.create(
            code=f'BATCH-{batch_number}',
            display_name=f'Batch {batch_number}',
            academic_start_year=timezone.now().year,
        ), True
    if not selected_batch_id:
        return None, False
    return get_object_or_404(Batch, pk=selected_batch_id), False


def _resolve_selected_exam(selected_batch_id, selected_module_id, selected_exam_id, new_exam_title='', new_exam_sat_on=''):
    if not selected_batch_id:
        raise ValidationError('Choose a batch before uploading.')
    if not selected_module_id:
        raise ValidationError('Choose a stream / subject before uploading.')
    if selected_exam_id:
        exam = get_object_or_404(Exam.objects.select_related('module', 'batch', 'module__department'), pk=selected_exam_id)
        if str(exam.module_id) != str(selected_module_id):
            raise ValidationError('Selected exam does not match the selected stream / subject.')
        if str(exam.batch_id) != str(selected_batch_id):
            raise ValidationError('Selected exam does not match the selected batch.')
        return exam
    if not new_exam_title:
        raise ValidationError('Choose an exam or enter a new exam name before uploading.')
    if not new_exam_sat_on:
        raise ValidationError('Enter the sat on date for the new exam.')
    batch = get_object_or_404(Batch, pk=selected_batch_id)
    module = get_object_or_404(Module.objects.prefetch_related('batches'), pk=selected_module_id)
    if not module.batches.filter(pk=batch.pk).exists():
        raise ValidationError('Selected batch must be enrolled to the selected stream / subject.')
    sat_on = parse_date(new_exam_sat_on)
    if sat_on is None:
        raise ValidationError('Enter a valid sat on date for the new exam.')
    exam = Exam(
        batch=batch,
        module=module,
        title=new_exam_title,
        sat_on=sat_on,
    )
    exam.full_clean()
    exam.save()
    return exam


@login_required
def imports_home(request):
    enforce_role_access(request, roles=STAFF_READ_ROLES, action='imports.home.view', channel=AuditEvent.Channel.WEB, target_app='imports', target_model='dashboard')
    recent_student_uploads = StudentUpload.objects.select_related('batch', 'uploaded_by').order_by('-started_at')[:5]
    recent_result_uploads = ResultUpload.objects.select_related('exam', 'exam__module', 'exam__batch', 'uploaded_by').order_by('-started_at')[:5]
    return render(request, 'imports/home.html', {'recent_student_uploads': recent_student_uploads, 'recent_result_uploads': recent_result_uploads})


@login_required
def stage_result_import(request, exam_id=None):
    enforce_role_access(request, roles=STAFF_MUTATION_ROLES, action='imports.results.create', channel=AuditEvent.Channel.WEB, target_app='imports', target_model='resultupload')
    locked_exam = get_object_or_404(Exam.objects.select_related('module', 'batch', 'module__department'), pk=exam_id) if exam_id else None
    if request.method == 'POST':
        selected_batch_id = str(locked_exam.batch_id) if locked_exam else request.POST.get('batch_id', '')
        selected_module_id = str(locked_exam.module_id) if locked_exam else request.POST.get('module_id', '')
        selected_exam_id = str(locked_exam.id) if locked_exam else request.POST.get('exam_id', '')
        new_exam_title = '' if locked_exam else request.POST.get('new_exam_title', '').strip()
        new_exam_sat_on = '' if locked_exam else request.POST.get('new_exam_sat_on', '').strip()
    else:
        selected_batch_id = request.GET.get('batch_id', str(locked_exam.batch_id) if locked_exam else '')
        selected_module_id = request.GET.get('module_id', str(locked_exam.module_id) if locked_exam else '')
        selected_exam_id = request.GET.get('exam_id', str(locked_exam.id) if locked_exam else '')
        new_exam_title = ''
        new_exam_sat_on = ''

    if request.method == 'POST':
        workbook = request.FILES.get('source_file')
        notes = request.POST.get('notes', '').strip()
        exam = None
        if workbook is None:
            messages.error(request, 'Choose an .xlsx workbook to upload.')
        else:
            try:
                exam = locked_exam or _resolve_selected_exam(selected_batch_id, selected_module_id, selected_exam_id, new_exam_title, new_exam_sat_on)
            except ValidationError as exc:
                messages.error(request, exc.message)
            else:
                if not selected_exam_id and not locked_exam:
                    selected_exam_id = str(exam.id)
                    messages.success(request, f'Created exam "{exam.title}" automatically.')
        if exam is not None:
            upload = ResultUpload(exam=exam, uploaded_by=request.user, source_filename=workbook.name, source_file=workbook, checksum_sha256='', notes=notes)
            upload.refresh_file_metadata()
            upload.save()
            try:
                stage_result_upload(upload, request=request, channel=AuditEvent.Channel.WEB)
            except ValidationError as exc:
                upload.status = ResultUpload.Status.FAILED
                upload.processing_error = exc.message
                upload.save(update_fields=['status', 'processing_error'])
                messages.error(request, exc.message)
            else:
                messages.success(request, f'Upload v{upload.version_number} staged for review.')
                return redirect('upload-review', upload_id=upload.id)

    recent_uploads = ResultUpload.objects.select_related('exam', 'exam__module', 'exam__batch', 'uploaded_by', 'published_by').order_by('-started_at')[:8]
    return render(
        request,
        'imports/upload_form.html',
        {
            'recent_uploads': recent_uploads,
            'batch_choices': _batch_choice_rows(),
            'module_choices': _module_choice_rows(selected_batch_id),
            'exam_choices': _exam_choice_rows(selected_module_id, selected_batch_id),
            'selected_batch_id': str(selected_batch_id or ''),
            'selected_module_id': str(selected_module_id or ''),
            'selected_exam_id': str(selected_exam_id or ''),
            'new_exam_title': new_exam_title,
            'new_exam_sat_on': new_exam_sat_on,
            'locked_exam': locked_exam,
        },
    )


@login_required
def stage_exam_upload(request, exam_id):
    return stage_result_import(request, exam_id=exam_id)


@login_required
def upload_review(request, upload_id):
    upload = get_object_or_404(ResultUpload.objects.select_related('exam', 'exam__module', 'exam__batch', 'uploaded_by', 'published_by'), pk=upload_id)
    enforce_role_access(request, roles=STAFF_READ_ROLES, action='imports.results.review', instance=upload, channel=AuditEvent.Channel.WEB)
    staged_rows = upload.staged_rows_set.select_related('student').order_by('row_number')
    accepted_rows = list(staged_rows.filter(review_bucket='accepted'))
    duplicate_rows = list(staged_rows.filter(review_bucket='duplicate'))
    rejected_rows = list(staged_rows.filter(review_bucket='rejected'))
    reason_counter = Counter()
    for row in duplicate_rows + rejected_rows:
        for error in row.validation_errors:
            reason_counter[error] += 1
    compare_uploads = upload.exam.uploads.exclude(pk=upload.pk).order_by('-version_number', '-started_at')[:5]
    record_audit_event(action='imports.results.review', actor=request.user, instance=upload, request=request, channel=AuditEvent.Channel.WEB, metadata={'accepted_rows': len(accepted_rows), 'duplicate_rows': len(duplicate_rows), 'rejected_rows': len(rejected_rows)})
    return render(request, 'imports/review.html', {'upload': upload, 'accepted_rows': accepted_rows, 'duplicate_rows': duplicate_rows, 'rejected_rows': rejected_rows, 'reason_counts': reason_counter.most_common(), 'compare_uploads': compare_uploads})


@login_required
def upload_review_export(request, upload_id):
    upload = get_object_or_404(ResultUpload.objects.select_related('exam', 'exam__module', 'exam__batch'), pk=upload_id)
    enforce_role_access(request, roles=STAFF_EXPORT_ROLES, action='imports.results.export', instance=upload, channel=AuditEvent.Channel.WEB)
    bucket = request.GET.get('bucket', 'all').strip().lower() or 'all'
    rows = upload.staged_rows_set.select_related('student').order_by('row_number')
    if bucket in {'accepted', 'duplicate', 'rejected'}:
        rows = rows.filter(review_bucket=bucket)
    csv_rows = [['row_number', 'registration_number', 'student_name', 'raw_score', 'grade', 'status', 'review_bucket', 'reasons']]
    for row in rows:
        csv_rows.append([row.row_number, row.registration_number, row.student.full_name if row.student_id else '', '' if row.raw_score is None else str(row.raw_score), row.grade, row.status, row.review_bucket, ' | '.join(row.validation_errors)])
    record_audit_event(action='imports.results.export', actor=request.user, instance=upload, request=request, channel=AuditEvent.Channel.WEB, metadata={'bucket': bucket, 'exported_rows': len(csv_rows) - 1})
    return _csv_response(csv_rows, f'{slugify(upload.exam.module.code)}-upload-v{upload.version_number}-{bucket}.csv')


@login_required
def stage_student_import(request):
    enforce_role_access(request, roles=STAFF_MUTATION_ROLES, action='imports.students.create', channel=AuditEvent.Channel.WEB, target_app='imports', target_model='studentupload')
    selected_batch_id = request.POST.get('batch_id') if request.method == 'POST' else request.GET.get('batch_id', '')
    new_batch_number = request.POST.get('new_batch_number', '').strip() if request.method == 'POST' else ''
    if request.method == 'POST':
        workbook = request.FILES.get('source_file')
        notes = request.POST.get('notes', '').strip()
        try:
            batch, created_batch = _resolve_selected_batch(selected_batch_id, new_batch_number)
        except ValidationError as exc:
            batch = None
            created_batch = False
            messages.error(request, exc.message)
        else:
            if batch and created_batch:
                messages.success(request, f'Created Batch {_batch_number_value(batch)}.')
                selected_batch_id = str(batch.id)
        if batch is None and not new_batch_number and not selected_batch_id:
            messages.error(request, 'Choose a batch before uploading.')
        elif workbook is None:
            messages.error(request, 'Choose an .xlsx workbook to upload.')
        elif batch is not None:
            upload = StudentUpload(batch=batch, uploaded_by=request.user, source_filename=workbook.name, source_file=workbook, checksum_sha256='', notes=notes)
            upload.refresh_file_metadata()
            upload.save()
            try:
                stage_student_upload(upload, request=request, channel=AuditEvent.Channel.WEB)
            except ValidationError as exc:
                upload.status = StudentUpload.Status.FAILED
                upload.processing_error = exc.message
                upload.save(update_fields=['status', 'processing_error'])
                messages.error(request, exc.message)
            else:
                messages.success(request, 'Student upload staged for review.')
                return redirect('student-upload-review', upload_id=upload.id)
    recent_uploads = StudentUpload.objects.select_related('batch', 'uploaded_by', 'published_by').order_by('-started_at')[:8]
    return render(request, 'imports/student_upload_form.html', {'recent_uploads': recent_uploads, 'batch_choices': _batch_choice_rows(), 'selected_batch_id': str(selected_batch_id or ''), 'new_batch_number': new_batch_number})


@login_required
def student_upload_review(request, upload_id):
    upload = get_object_or_404(StudentUpload.objects.select_related('batch', 'uploaded_by', 'published_by'), pk=upload_id)
    enforce_role_access(request, roles=STAFF_READ_ROLES, action='imports.students.review', instance=upload, channel=AuditEvent.Channel.WEB)
    staged_rows = upload.staged_rows_set.select_related('batch', 'matched_student', 'created_student').order_by('row_number')
    accepted_rows = list(staged_rows.filter(review_bucket='accepted'))
    duplicate_rows = list(staged_rows.filter(review_bucket='duplicate'))
    rejected_rows = list(staged_rows.filter(review_bucket='rejected'))
    reason_counter = Counter()
    for row in duplicate_rows + rejected_rows:
        for error in row.validation_errors:
            reason_counter[error] += 1
    record_audit_event(action='imports.students.review', actor=request.user, instance=upload, request=request, channel=AuditEvent.Channel.WEB, metadata={'accepted_rows': len(accepted_rows), 'duplicate_rows': len(duplicate_rows), 'rejected_rows': len(rejected_rows), 'batch_id': upload.batch_id})
    return render(request, 'imports/student_review.html', {'upload': upload, 'accepted_rows': accepted_rows, 'duplicate_rows': duplicate_rows, 'rejected_rows': rejected_rows, 'reason_counts': reason_counter.most_common()})


@login_required
def student_upload_review_export(request, upload_id):
    upload = get_object_or_404(StudentUpload.objects.select_related('batch'), pk=upload_id)
    enforce_role_access(request, roles=STAFF_EXPORT_ROLES, action='imports.students.export', instance=upload, channel=AuditEvent.Channel.WEB)
    bucket = request.GET.get('bucket', 'all').strip().lower() or 'all'
    rows = upload.staged_rows_set.select_related('batch', 'matched_student').order_by('row_number')
    if bucket in {'accepted', 'duplicate', 'rejected'}:
        rows = rows.filter(review_bucket=bucket)
    csv_rows = [['row_number', 'registration_number', 'first_name', 'last_name', 'batch', 'review_bucket', 'reasons']]
    for row in rows:
        csv_rows.append([row.row_number, row.registration_number, row.first_name, row.last_name, row.batch.display_name if row.batch_id else '', row.review_bucket, ' | '.join(row.validation_errors)])
    record_audit_event(action='imports.students.export', actor=request.user, instance=upload, request=request, channel=AuditEvent.Channel.WEB, metadata={'bucket': bucket, 'exported_rows': len(csv_rows) - 1, 'batch_id': upload.batch_id})
    return _csv_response(csv_rows, f'student-upload-{upload.pk}-{bucket}.csv')


@login_required
def publish_student_upload_summary(request, upload_id):
    upload = get_object_or_404(StudentUpload.objects.select_related('batch', 'uploaded_by'), pk=upload_id)
    enforce_role_access(request, roles=STAFF_MUTATION_ROLES, action='imports.students.publish.summary', instance=upload, channel=AuditEvent.Channel.WEB)
    if request.method == 'POST':
        published_count = publish_student_upload(upload, actor=request.user, request=request, channel=AuditEvent.Channel.WEB)
        messages.success(request, f'Published {published_count} student row(s).')
        return redirect('student-upload-review', upload_id=upload.id)
    summary = upload.summary
    record_audit_event(action='imports.students.publish.summary', actor=request.user, instance=upload, request=request, channel=AuditEvent.Channel.WEB, metadata={**summary, 'batch_id': upload.batch_id})
    return render(request, 'imports/student_publish_summary.html', {'upload': upload, 'summary': summary})
