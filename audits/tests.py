import shutil
import tempfile
from decimal import Decimal
from io import BytesIO

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from openpyxl import Workbook

from audits.models import AuditEvent
from audits.roles import Role
from audits.utils import export_exam_results, record_audit_event
from imports.models import ResultUpload, publish_result_upload, stage_result_upload
from results.models import Exam
from students.admin import BatchAdmin
from students.models import Batch, Department, Module, Student


def assign_role(user, role_name):
    group, _ = Group.objects.get_or_create(name=role_name)
    user.groups.add(group)


class AuditLoggedAdminTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="adminuser", password="secure-pass-123", is_staff=True)
        assign_role(self.user, Role.REGISTRY_ADMIN)
        from django.test import RequestFactory

        self.factory = RequestFactory()
        self.admin = BatchAdmin(Batch, AdminSite())
        self.department = Department.objects.create(code="MED", name="Medicine")

    def build_request(self):
        request = self.factory.post("/admin/students/batch/")
        request.user = self.user
        request.META["REMOTE_ADDR"] = "127.0.0.1"
        return request

    def test_create_update_and_delete_are_audited(self):
        request = self.build_request()
        batch = Batch(code="MBBS-2025", display_name="MBBS 2025 Cohort", academic_start_year=2025)
        self.admin.save_model(request, batch, form=None, change=False)
        batch_id = str(batch.pk)
        create_event = AuditEvent.objects.get(action="admin.object.create")
        self.assertEqual(create_event.actor, self.user)
        self.assertEqual(create_event.target_model, "batch")
        self.assertEqual(create_event.target_id, batch_id)
        self.assertEqual(create_event.ip_address, "127.0.0.1")
        self.assertEqual(create_event.channel, AuditEvent.Channel.ADMIN)
        batch.display_name = "MBBS 2025 Cohort A"
        self.admin.save_model(request, batch, form=None, change=True)
        self.assertTrue(AuditEvent.objects.filter(action="admin.object.update", target_id=batch_id).exists())
        self.admin.delete_model(request, batch)
        self.assertTrue(AuditEvent.objects.filter(action="admin.object.delete", target_id=batch_id).exists())


class AuditEventModelTests(TestCase):
    def test_audit_events_are_append_only(self):
        event = record_audit_event(action="test.event", target_app="tests", target_model="case", object_repr="case")
        event.metadata = {"changed": True}
        with self.assertRaises(ValidationError):
            event.save()
        with self.assertRaises(ValidationError):
            event.delete()


@override_settings(LOGIN_REDIRECT_URL="/")
class AuditWorkflowTests(TestCase):
    def setUp(self):
        self.temp_media = tempfile.mkdtemp()
        self.media_override = override_settings(MEDIA_ROOT=self.temp_media)
        self.media_override.enable()
        self.addCleanup(self.media_override.disable)
        self.addCleanup(lambda: shutil.rmtree(self.temp_media, ignore_errors=True))
        self.user = get_user_model().objects.create_user(username="reviewer", email="reviewer@example.com", password="safe-password-123")
        assign_role(self.user, Role.RESULTS_OFFICER)
        self.auditor = get_user_model().objects.create_user(username="auditor", password="safe-password-123")
        assign_role(self.auditor, Role.AUDITOR)
        self.department = Department.objects.create(code="MED", name="Medicine")
        self.batch = Batch.objects.create(code="MBBS-2025", display_name="MBBS 2025 Cohort", academic_start_year=2025)
        self.module = Module.objects.create(department=self.department, code="ANA101", title="Anatomy I", semester="1")
        self.module.batches.set([self.batch])
        self.exam = Exam.objects.create(batch=self.batch, module=self.module, title="Final anatomy exam", sat_on="2026-05-11", maximum_score=Decimal("100.00"), pass_mark=Decimal("50.00"))
        self.student = Student.objects.create(batch=self.batch, registration_number="2025MED0001", first_name="Asha", last_name="Silva")

    def build_workbook_file(self, rows, filename="results.xlsx"):
        workbook = Workbook()
        sheet = workbook.active
        for row in rows:
            sheet.append(row)
        buffer = BytesIO()
        workbook.save(buffer)
        return SimpleUploadedFile(filename, buffer.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    def create_upload(self):
        return ResultUpload.objects.create(exam=self.exam, uploaded_by=self.user, source_filename="results.xlsx", source_file=self.build_workbook_file([["registration_number", "raw_score", "grade"], [self.student.registration_number, 91, "A"]]), checksum_sha256="0" * 64)

    def test_login_and_failed_login_create_audit_events(self):
        response = self.client.post(reverse("login"), {"username": self.user.username, "password": "safe-password-123"})
        self.assertEqual(response.status_code, 302)
        login_event = AuditEvent.objects.get(action="auth.login", outcome=AuditEvent.Outcome.SUCCESS)
        self.assertEqual(login_event.target_model, "user")
        self.client.logout()
        response = self.client.post(reverse("login"), {"username": self.user.username, "password": "wrong-password"})
        self.assertEqual(response.status_code, 200)
        failed_event = AuditEvent.objects.get(action="auth.login", outcome=AuditEvent.Outcome.FAILED)
        self.assertEqual(failed_event.metadata["context"]["username"], self.user.username)

    def test_student_search_and_profile_view_create_audit_events(self):
        assign_role(self.user, Role.VIEWER)
        self.client.force_login(self.user)
        response = self.client.get(reverse("student-search"), {"q": self.student.registration_number})
        self.assertEqual(response.status_code, 200)
        search_event = AuditEvent.objects.get(action="students.search", outcome=AuditEvent.Outcome.SUCCESS)
        self.assertEqual(search_event.actor, self.user)
        self.assertEqual(search_event.metadata["context"]["query"], self.student.registration_number)
        self.assertEqual(search_event.metadata["context"]["result_count"], 1)
        response = self.client.get(reverse("student-profile", args=[self.student.id]))
        self.assertEqual(response.status_code, 200)
        profile_event = AuditEvent.objects.get(action="students.profile.view", outcome=AuditEvent.Outcome.SUCCESS)
        self.assertEqual(profile_event.actor, self.user)
        self.assertEqual(profile_event.target_id, str(self.student.id))
        self.assertEqual(profile_event.student, self.student)

    def test_student_search_without_role_is_forbidden_and_audited(self):
        plain_user = get_user_model().objects.create_user(username="plain", password="safe-password-123")
        self.client.force_login(plain_user)
        response = self.client.get(reverse("student-search"), {"q": self.student.registration_number})
        self.assertEqual(response.status_code, 403)
        denied_event = AuditEvent.objects.get(action="students.search", outcome=AuditEvent.Outcome.FORBIDDEN)
        self.assertEqual(denied_event.actor, plain_user)

    def test_result_import_publish_export_and_audit_report_create_events(self):
        upload = self.create_upload()
        stage_result_upload(upload)
        import_event = AuditEvent.objects.get(action="imports.results.stage")
        self.assertEqual(import_event.actor, self.user)
        published_count = publish_result_upload(upload, actor=self.user)
        self.assertEqual(published_count, 1)
        publish_event = AuditEvent.objects.get(action="imports.results.publish")
        self.assertEqual(publish_event.actor, self.user)
        self.assertEqual(publish_event.metadata["context"]["published_rows"], 1)
        response = export_exam_results(self.exam, actor=self.user)
        self.assertEqual(response.status_code, 200)
        export_event = AuditEvent.objects.get(action="results.export")
        self.assertEqual(export_event.actor, self.user)
        self.assertEqual(export_event.exam, self.exam)
        self.client.force_login(self.auditor)
        report_response = self.client.get(reverse("audit-report"), {"action": "results"})
        self.assertEqual(report_response.status_code, 200)
        self.assertTrue(AuditEvent.objects.filter(action="audits.report.view", actor=self.auditor).exists())

