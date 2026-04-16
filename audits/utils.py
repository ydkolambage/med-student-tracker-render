import logging

from django.http import HttpResponse
from django.utils.text import slugify

from audits.models import AuditEvent


logger = logging.getLogger('medtracker.audit')


def get_client_ip(request):
    forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded_for:
        return forwarded_for.split(',', 1)[0].strip()
    return request.META.get('REMOTE_ADDR')


def _infer_related_targets(instance=None, *, student=None, exam=None):
    if instance is not None:
        label = instance._meta.label_lower
        if label == 'students.student':
            student = student or instance
        elif label == 'results.exam':
            exam = exam or instance
        if getattr(instance, 'student_id', None):
            student = student or instance.student
        if getattr(instance, 'exam_id', None):
            exam = exam or instance.exam
    return student, exam


def audit_metadata(*, actor=None, channel, outcome, request=None, ip_address=None, target_app, target_model, target_id='', object_repr='', metadata=None):
    return {
        'request': {
            'channel': channel,
            'method': request.method if request is not None else '',
            'path': request.path if request is not None else '',
            'ip_address': ip_address or (get_client_ip(request) if request is not None else None),
        },
        'actor': {
            'id': actor.pk if actor else None,
            'username': actor.get_username() if actor else '',
        },
        'target': {
            'app': target_app,
            'model': target_model,
            'id': str(target_id or ''),
            'repr': object_repr,
        },
        'outcome': {
            'status': outcome,
        },
        'context': metadata or {},
    }


def _emit_structured_log(*, action, actor, request, channel, outcome, target_app, target_model, target_id, object_repr, metadata, ip_address):
    logger.info(
        action,
        extra={
            'structured': {
                'event': action,
                'channel': channel,
                'outcome': outcome,
                'actor_id': actor.pk if actor else None,
                'actor_username': actor.get_username() if actor else '',
                'request_method': request.method if request is not None else '',
                'request_path': request.path if request is not None else '',
                'ip_address': ip_address,
                'target_app': target_app,
                'target_model': target_model,
                'target_id': str(target_id or ''),
                'target_repr': object_repr,
                'metadata': metadata or {},
            }
        },
    )


def record_audit_event(
    *,
    action,
    actor=None,
    instance=None,
    request=None,
    channel=AuditEvent.Channel.SERVICE,
    outcome=AuditEvent.Outcome.SUCCESS,
    metadata=None,
    ip_address=None,
    sensitive=True,
    student=None,
    exam=None,
    target_app=None,
    target_model=None,
    target_id='',
    object_repr='',
):
    if instance is not None:
        opts = instance._meta
        target_app = opts.app_label
        target_model = opts.model_name
        target_id = str(instance.pk or '')
        object_repr = str(instance)
    student, exam = _infer_related_targets(instance, student=student, exam=exam)
    resolved_ip_address = ip_address or (get_client_ip(request) if request is not None else None)
    audit_payload = audit_metadata(
        actor=actor,
        channel=channel,
        outcome=outcome,
        request=request,
        ip_address=resolved_ip_address,
        target_app=target_app or '',
        target_model=target_model or '',
        target_id=target_id,
        object_repr=object_repr or '',
        metadata=metadata,
    )
    _emit_structured_log(
        action=action,
        actor=actor,
        request=request,
        channel=channel,
        outcome=outcome,
        target_app=target_app or '',
        target_model=target_model or '',
        target_id=target_id,
        object_repr=object_repr or '',
        metadata=metadata,
        ip_address=resolved_ip_address,
    )
    return AuditEvent.objects.create(
        actor=actor,
        action=action,
        target_app=target_app or '',
        target_model=target_model or '',
        target_id=str(target_id or ''),
        object_repr=object_repr or '',
        channel=channel,
        outcome=outcome,
        request_method=request.method if request is not None else '',
        request_path=request.path if request is not None else '',
        student=student,
        exam=exam,
        sensitive=sensitive,
        ip_address=resolved_ip_address,
        metadata=audit_payload,
    )


def export_exam_results(exam, *, actor=None, request=None, ip_address=None):
    rows = ['registration_number,student_name,raw_score,percentage,grade,status,is_absent,is_withheld']
    queryset = exam.results.select_related('student').order_by('student__registration_number')
    for result in queryset:
        rows.append(
            ','.join(
                [
                    result.student.registration_number,
                    f'"{result.student.full_name}"',
                    '' if result.raw_score is None else str(result.raw_score),
                    '' if result.percentage is None else str(result.percentage),
                    result.grade,
                    result.status,
                    'true' if result.is_absent else 'false',
                    'true' if result.is_withheld else 'false',
                ]
            )
        )
    record_audit_event(
        action='results.export',
        actor=actor,
        instance=exam,
        request=request,
        channel=AuditEvent.Channel.ADMIN if request is not None else AuditEvent.Channel.SERVICE,
        metadata={'exported_rows': queryset.count()},
        ip_address=ip_address,
    )
    filename = f'{slugify(exam.module.code)}-{slugify(exam.title)}-results.csv'
    response = HttpResponse('\n'.join(rows), content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response
