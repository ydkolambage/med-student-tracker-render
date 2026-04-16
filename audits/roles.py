from functools import wraps

from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.db.models.signals import post_migrate

from audits.models import AuditEvent


class Role:
    REGISTRY_ADMIN = "Registry Admin"
    RESULTS_OFFICER = "Results Officer"
    VIEWER = "Viewer"
    AUDITOR = "Auditor"


ROLE_NAMES = (
    Role.REGISTRY_ADMIN,
    Role.RESULTS_OFFICER,
    Role.VIEWER,
    Role.AUDITOR,
)


def ensure_role_groups(**kwargs):
    for role_name in ROLE_NAMES:
        Group.objects.get_or_create(name=role_name)


def connect_role_group_provisioning(sender):
    post_migrate.connect(ensure_role_groups, sender=sender, dispatch_uid="audits.ensure_role_groups")


def user_has_any_role(user, roles):
    if not user or not user.is_authenticated or not user.is_active:
        return False
    if user.is_superuser:
        return True
    if not roles:
        return False
    return user.groups.filter(name__in=tuple(roles)).exists()


def enforce_role_access(
    request,
    *,
    roles,
    action,
    instance=None,
    channel=AuditEvent.Channel.WEB,
    metadata=None,
    student=None,
    exam=None,
    target_app=None,
    target_model=None,
    target_id="",
    object_repr="",
    deny_message="You do not have permission to access this workflow.",
):
    if user_has_any_role(request.user, roles):
        return
    from audits.utils import record_audit_event

    record_audit_event(
        action=action,
        actor=request.user if request.user.is_authenticated else None,
        instance=instance,
        request=request,
        channel=channel,
        outcome=AuditEvent.Outcome.FORBIDDEN,
        metadata=metadata or {"reason": "role_denied"},
        student=student,
        exam=exam,
        target_app=target_app,
        target_model=target_model,
        target_id=target_id,
        object_repr=object_repr,
    )
    raise PermissionDenied(deny_message)


def role_protected_view(*roles, action, channel=AuditEvent.Channel.WEB):
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            enforce_role_access(request, roles=roles, action=action, channel=channel)
            return view_func(request, *args, **kwargs)

        return wrapped

    return decorator
