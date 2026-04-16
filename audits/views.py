from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import render
from django.utils.dateparse import parse_date

from audits.models import AuditEvent
from audits.roles import Role, enforce_role_access
from audits.utils import record_audit_event


@login_required
def audit_report(request):
    filters = {
        "actor": request.GET.get("actor", "").strip(),
        "student": request.GET.get("student", "").strip(),
        "exam": request.GET.get("exam", "").strip(),
        "action": request.GET.get("action", "").strip(),
        "date_from": request.GET.get("date_from", "").strip(),
        "date_to": request.GET.get("date_to", "").strip(),
    }
    enforce_role_access(
        request,
        roles=(Role.REGISTRY_ADMIN, Role.AUDITOR),
        action="audits.report.view",
        channel=AuditEvent.Channel.WEB,
        target_app="audits",
        target_model="audit_report",
        object_repr="Audit report",
    )

    events = AuditEvent.objects.select_related("actor", "student", "exam", "exam__module")
    if filters["actor"]:
        events = events.filter(actor__username__icontains=filters["actor"])
    if filters["student"]:
        events = events.filter(student__registration_number__icontains=filters["student"])
    if filters["exam"]:
        events = events.filter(Q(exam__title__icontains=filters["exam"]) | Q(exam__module__code__icontains=filters["exam"]))
    if filters["action"]:
        events = events.filter(action__icontains=filters["action"])
    date_from = parse_date(filters["date_from"]) if filters["date_from"] else None
    date_to = parse_date(filters["date_to"]) if filters["date_to"] else None
    if date_from:
        events = events.filter(occurred_at__date__gte=date_from)
    if date_to:
        events = events.filter(occurred_at__date__lte=date_to)
    events = list(events[:200])

    record_audit_event(
        action="audits.report.view",
        actor=request.user,
        request=request,
        channel=AuditEvent.Channel.WEB,
        target_app="audits",
        target_model="audit_report",
        object_repr="Audit report",
        metadata={"filters": filters, "result_count": len(events)},
    )
    return render(request, "audits/report.html", {"filters": filters, "events": events})
