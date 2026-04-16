import shutil
import tempfile

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from audits.roles import Role


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class LoginRedirectTests(TestCase):
    def setUp(self):
        self.user_model = get_user_model()
        self.staff_user = self.user_model.objects.create_user(username='staffer', password='safe-password-123')
        self.superuser = self.user_model.objects.create_superuser(username='boss', email='boss@example.com', password='safe-password-123')
        self.registry_admin = self.user_model.objects.create_user(username='registry', email='registry@example.com', password='safe-password-123')
        registry_group, _ = Group.objects.get_or_create(name=Role.REGISTRY_ADMIN)
        self.registry_admin.groups.add(registry_group)

    def test_non_superuser_login_redirects_to_staff_dashboard(self):
        response = self.client.post(reverse('login'), {'username': 'staffer', 'password': 'safe-password-123'})
        self.assertRedirects(response, reverse('staff-dashboard'), fetch_redirect_response=False)

    def test_home_redirects_to_login(self):
        response = self.client.get(reverse('home'))
        self.assertRedirects(response, reverse('login'), fetch_redirect_response=False)

    def test_authenticated_home_redirects_to_login_entry_point(self):
        self.client.force_login(self.staff_user)
        response = self.client.get(reverse('home'))
        self.assertRedirects(response, reverse('login'), fetch_redirect_response=False)

    def test_superuser_login_redirects_to_admin(self):
        response = self.client.post(reverse('login'), {'username': 'boss', 'password': 'safe-password-123'})
        self.assertRedirects(response, reverse('admin:index'), fetch_redirect_response=False)

    def test_logout_post_logs_user_out_and_redirects_to_login(self):
        self.client.force_login(self.staff_user)
        response = self.client.post(reverse('logout'))
        self.assertRedirects(response, reverse('login'), fetch_redirect_response=False)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_admin_header_links_use_staff_view_site_and_root_logout(self):
        self.client.force_login(self.superuser)
        response = self.client.get(reverse('admin:index'))
        self.assertContains(response, 'href="/staff/"')
        self.assertContains(response, 'action="/logout/"')

    def test_request_access_from_home_redirects_to_login_and_sends_email(self):
        response = self.client.post(reverse('home'), {'action': 'request_access'})
        self.assertRedirects(response, reverse('login'), fetch_redirect_response=False)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(set(mail.outbox[0].to), {'boss@example.com', 'registry@example.com'})

    def test_login_page_shows_professional_copy_and_forgot_password_link(self):
        response = self.client.get(reverse('login'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Staff sign in')
        self.assertContains(response, 'Medical Student Tracker')
        self.assertContains(response, 'Forgot password?')
        self.assertContains(response, 'Access requests are available only to members of the Faculty of Medicine, Sabaragamuwa University of Sri Lanka.')
        self.assertContains(response, 'Request access')

    def test_password_reset_request_sends_reset_email(self):
        response = self.client.post(reverse('password_reset'), {'email': 'boss@example.com'})
        self.assertRedirects(response, reverse('password_reset_done'), fetch_redirect_response=False)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Medical Student Tracker password reset', mail.outbox[0].subject)
        self.assertIn('/reset/', mail.outbox[0].body)
        self.assertIn('boss@example.com', mail.outbox[0].body)


    def test_login_page_request_access_sends_email_to_superuser_and_registry_admins(self):
        response = self.client.post(reverse('login'), {'action': 'request_access'})
        self.assertRedirects(response, reverse('login'), fetch_redirect_response=False)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(set(mail.outbox[0].to), {'boss@example.com', 'registry@example.com'})
        self.assertIn('Faculty of Medicine, Sabaragamuwa University of Sri Lanka', mail.outbox[0].body)


    def test_sessions_expire_when_browser_closes(self):
        self.assertTrue(settings.SESSION_EXPIRE_AT_BROWSER_CLOSE)


class HealthCheckTests(TestCase):
    def setUp(self):
        self.temp_media = tempfile.mkdtemp()
        self.override = override_settings(MEDIA_ROOT=self.temp_media)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.addCleanup(lambda: shutil.rmtree(self.temp_media, ignore_errors=True))

    def test_live_health_endpoint_reports_ok(self):
        response = self.client.get(reverse('health-live'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'ok')

    def test_ready_health_endpoint_checks_database_and_media_storage(self):
        response = self.client.get(reverse('health-ready'))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['status'], 'ok')
        self.assertTrue(payload['checks']['database']['ok'])
        self.assertTrue(payload['checks']['media_storage']['ok'])
