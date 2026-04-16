from collections import defaultdict

from django.contrib.auth.decorators import login_required
from django.db.models import Avg, Q
from django.shortcuts import get_object_or_404, render

from audits.models import AuditEvent
from audits.roles import Role, enforce_role_access
from audits.utils import record_audit_event
from results.models import ExamResult
from students.models import Student


STUDENT_READ_ROLES = (
    Role.REGISTRY_ADMIN,
    Role.RESULTS_OFFICER,
    Role.VIEWER,
    Role.AUDITOR,
)


def _result_label(result):
    grade = (result.grade or "").strip()
    if grade.lower() in {"pass", "fail"}:
        return grade.title()
    if result.status == ExamResult.Status.RECORDED and result.raw_score is not None:
        return "Pass" if result.raw_score >= result.exam.pass_mark else "Fail"
    return "-"


@login_required
def student_search(request):
    query = request.GET.get("q", "").strip()
    enforce_role_access(
        request,
        roles=STUDENT_READ_ROLES,
        action="students.search",
        channel=AuditEvent.Channel.WEB,
        target_app="students",
        target_model="student_search",
        object_repr=query or "empty query",
    )
    students = Student.objects.select_related("batch")
    if query:
        students = students.filter(
            Q(registration_number__icontains=query)
            | Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
            | Q(batch__code__icontains=query)
            | Q(batch__display_name__icontains=query)
        )
    else:
        students = students.none()

    record_audit_event(
        action="students.search",
        actor=request.user,
        request=request,
        channel=AuditEvent.Channel.WEB,
        target_app="students",
        target_model="student_search",
        object_repr=query or "empty query",
        metadata={"query": query, "result_count": students.count()},
    )
    return render(request, "students/search.html", {"query": query, "students": students[:50]})


@login_required
def student_profile(request, student_id):
    student = get_object_or_404(Student.objects.select_related("batch"), pk=student_id)
    enforce_role_access(
        request,
        roles=STUDENT_READ_ROLES,
        action="students.profile.view",
        instance=student,
        channel=AuditEvent.Channel.WEB,
    )
    results = list(
        ExamResult.objects.filter(student=student)
        .select_related("exam", "exam__module", "exam__module__department", "upload")
        .order_by("exam__module__code", "-exam__sat_on", "exam__title")
    )
    for result in results:
        result.result_label = _result_label(result)
    grouped_results = defaultdict(list)
    for result in results:
        grouped_results[result.exam.module].append(result)

    longitudinal_modules = []
    for module, module_results in grouped_results.items():
        percentages = [result.percentage for result in module_results if result.percentage is not None]
        longitudinal_modules.append(
            {
                "module": module,
                "results": module_results,
                "average_percentage": sum(percentages) / len(percentages) if percentages else None,
                "failed_count": sum(1 for result in module_results if result.result_label == "Fail"),
                "absent_count": sum(1 for result in module_results if result.status == ExamResult.Status.ABSENT),
                "withheld_count": sum(1 for result in module_results if result.status == ExamResult.Status.WITHHELD),
            }
        )

    overall_summary = ExamResult.objects.filter(student=student).aggregate(average_percentage=Avg("percentage"))
    record_audit_event(
        action="students.profile.view",
        actor=request.user,
        instance=student,
        request=request,
        channel=AuditEvent.Channel.WEB,
    )
    return render(
        request,
        "students/profile.html",
        {
            "student": student,
            "longitudinal_modules": longitudinal_modules,
            "overall_average": overall_summary["average_percentage"],
            "results_count": len(results),
        },
    )
