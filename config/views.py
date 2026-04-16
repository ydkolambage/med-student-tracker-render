from django.conf import settings
from django.contrib import messages
from django.contrib.auth.models import Group
from django.contrib.auth.views import LoginView
from django.core.mail import send_mail
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET

from audits.models import AuditEvent
from audits.roles import Role
from audits.utils import record_audit_event
from config.health import database_health, media_storage_health


def _handle_access_request(request, *, success_redirect):
    recipients = _access_request_recipients()
    if not recipients:
        messages.error(request, 'No access approvers are configured with email addresses yet.')
        return redirect(success_redirect)

    actor = request.user if request.user.is_authenticated else None
    display_name = request.user.get_username() if actor else 'Anonymous visitor'
    authenticated_text = 'Yes' if actor else 'No'
    ip_address = request.META.get('REMOTE_ADDR', 'unknown')
    subject = 'Medical Student Tracker access request'
    body = (
        'A new access request was submitted for Medical Student Tracker.\n\n'
        'This workflow is intended only for members of the Faculty of Medicine, ' 
        'Sabaragamuwa University of Sri Lanka.\n\n'
        f'Requester: {display_name}\n'
        f'Authenticated: {authenticated_text}\n'
        f'Page: {request.path}\n'
        f'IP address: {ip_address}\n'
    )
    send_mail(
        subject,
        body,
        getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@example.com'),
        recipients,
        fail_silently=False,
    )
    record_audit_event(
        action='auth.request_access',
        actor=actor,
        request=request,
        channel=AuditEvent.Channel.WEB,
        target_app='auth',
        target_model='user',
        object_repr=display_name,
        metadata={'recipient_count': len(recipients), 'environment': settings.ENVIRONMENT},
    )
    messages.success(
        request,
        'Your access request has been sent for review. Access is limited to members of the Faculty of Medicine, Sabaragamuwa University of Sri Lanka.'
    )
    return redirect(success_redirect)


class AuditedLoginView(LoginView):
    template_name = 'registration/login.html'
    redirect_authenticated_user = True

    def post(self, request, *args, **kwargs):
        if request.POST.get('action') == 'request_access':
            return _handle_access_request(request, success_redirect='login')
        return super().post(request, *args, **kwargs)

    def get_success_url(self):
        redirect_to = self.get_redirect_url()
        if redirect_to:
            return redirect_to
        user = self.request.user
        if user.is_authenticated and not user.is_superuser:
            return reverse('staff-dashboard')
        if user.is_authenticated and user.is_superuser:
            return reverse('admin:index')
        return super().get_success_url()

    def form_valid(self, form):
        response = super().form_valid(form)
        record_audit_event(
            action='auth.login',
            actor=self.request.user,
            request=self.request,
            channel=AuditEvent.Channel.WEB,
            target_app='auth',
            target_model='user',
            target_id=self.request.user.pk,
            object_repr=self.request.user.get_username(),
            metadata={'environment': settings.ENVIRONMENT},
        )
        return response

    def form_invalid(self, form):
        username = form.data.get('username', '')
        record_audit_event(
            action='auth.login',
            actor=None,
            request=self.request,
            channel=AuditEvent.Channel.WEB,
            outcome=AuditEvent.Outcome.FAILED,
            target_app='auth',
            target_model='user',
            object_repr=username or 'anonymous',
            metadata={'username': username, 'environment': settings.ENVIRONMENT},
        )
        return super().form_invalid(form)


def _access_request_recipients():
    from django.contrib.auth import get_user_model

    user_model = get_user_model()
    recipients = set(
        user_model.objects.filter(is_superuser=True, is_active=True)
        .exclude(email='')
        .values_list('email', flat=True)
    )
    registry_group = Group.objects.filter(name=Role.REGISTRY_ADMIN).first()
    if registry_group is not None:
        recipients.update(
            registry_group.user_set.filter(is_active=True)
            .exclude(email='')
            .values_list('email', flat=True)
        )
    return sorted(recipients)


def home(request):
    if request.method == 'POST' and request.POST.get('action') == 'request_access':
        return _handle_access_request(request, success_redirect='login')
    return redirect('login')


@require_GET
def health_live(request):
    return JsonResponse({'status': 'ok', 'environment': settings.ENVIRONMENT})


@require_GET
def health_ready(request):
    checks = {}
    errors = []
    for name, check in (('database', database_health), ('media_storage', media_storage_health)):
        try:
            checks[name] = check()
        except Exception as exc:
            checks[name] = {'ok': False, 'error': str(exc)}
            errors.append(name)
    status = 200 if not errors else 503
    return JsonResponse({'status': 'ok' if not errors else 'degraded', 'checks': checks}, status=status)
