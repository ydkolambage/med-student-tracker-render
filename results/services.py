from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from audits.models import AuditEvent
from audits.utils import record_audit_event
from results.models import Exam, ExamResult


def _serialize_result(result):
    return {
        "raw_score": None if result.raw_score is None else str(result.raw_score),
        "percentage": None if result.percentage is None else str(result.percentage),
        "grade": result.grade,
        "status": result.status,
        "remarks": result.remarks,
        "student_id": result.student_id,
        "exam_id": result.exam_id,
    }


def release_exam_results(exam, *, actor=None, request=None, channel=AuditEvent.Channel.SERVICE, ip_address=None):
    if not exam.results.exists():
        raise ValidationError("Cannot release an exam without any recorded results.")
    if not exam.results.exclude(status=ExamResult.Status.WITHHELD).exists():
        raise ValidationError("Cannot release an exam when every result is withheld.")

    exam.release_version += 1
    exam.results_released_at = timezone.now()
    exam.release_state = Exam.ReleaseState.RELEASED if exam.release_version == 1 else Exam.ReleaseState.REPUBLISHED
    exam.save(update_fields=["release_version", "results_released_at", "release_state", "updated_at"])
    record_audit_event(
        action="results.exam.release",
        actor=actor,
        instance=exam,
        request=request,
        channel=channel,
        ip_address=ip_address,
        metadata={
            "release_version": exam.release_version,
            "published_results": exam.results.exclude(status=ExamResult.Status.WITHHELD).count(),
            "withheld_results": exam.results.filter(status=ExamResult.Status.WITHHELD).count(),
        },
    )
    return exam


def apply_result_correction(correction, *, actor=None, request=None, channel=AuditEvent.Channel.SERVICE, ip_address=None):
    result = correction.exam_result
    if not result.exam.is_released:
        raise ValidationError("Result corrections are only available for released exams.")

    previous_values = _serialize_result(result)
    with transaction.atomic():
        result.status = correction.new_status
        result.raw_score = correction.new_raw_score if correction.new_status != ExamResult.Status.ABSENT else None
        result.grade = correction.new_grade
        result.remarks = correction.new_remarks
        result.save(allow_published_mutation=True)

        correction.previous_values = previous_values
        correction.applied_values = _serialize_result(result)
        correction.applied_by = actor
        correction.applied_at = timezone.now()
        if correction.requested_by_id is None:
            correction.requested_by = actor
        correction.save(update_fields=["requested_by", "applied_by", "previous_values", "applied_values", "applied_at"])

    record_audit_event(
        action="results.correction.apply",
        actor=actor,
        instance=result,
        request=request,
        channel=channel,
        ip_address=ip_address,
        metadata={
            "correction_id": correction.pk,
            "reason": correction.reason,
            "previous_values": previous_values,
            "applied_values": correction.applied_values,
        },
    )
    return result
