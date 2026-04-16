import shutil
import tempfile
from decimal import Decimal
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from django.urls import reverse
from openpyxl import Workbook

from imports.models import ResultUpload, stage_result_upload
from results.models import Exam, ExamResult, ResultCorrection
from results.services import apply_result_correction, release_exam_results
from students.models import Batch, Department, Module, Student


class ExamResultModelTests(TestCase):
    def setUp(self):
        self.department = Department.objects.create(code="MED", name="Medicine")
        self.batch = Batch.objects.create(code="MBBS-2025", display_name="MBBS 2025 Cohort", academic_start_year=2025)
        self.module = Module.objects.create(department=self.department, code="ANA101", title="Anatomy I", semester="1")
        self.module.batches.set([self.batch])
        self.student = Student.objects.create(batch=self.batch, registration_number="2025MED0001", first_name="Nimal", last_name="Fernando")
        self.exam = Exam.objects.create(batch=self.batch, module=self.module, title="Mid-semester written", sat_on="2026-03-10", maximum_score=Decimal("100.00"), pass_mark=Decimal("50.00"))
        self.actor = get_user_model().objects.create_user(username="officer", password="safe-password-123")

    def test_percentage_is_calculated_on_save(self):
        result = ExamResult.objects.create(exam=self.exam, student=self.student, raw_score=Decimal("67.50"), grade="B")
        self.assertEqual(result.percentage, Decimal("67.50"))
        self.assertEqual(result.status, ExamResult.Status.RECORDED)
        self.assertEqual(self.exam.maximum_score, Decimal("100.00"))
        self.assertEqual(self.exam.pass_mark, Decimal("50.00"))
        self.assertEqual(self.exam.weight_percentage, Decimal("100.00"))
        self.assertEqual(str(result), "2025MED0001 - Mid-semester written")

    def test_absent_result_clears_scores(self):
        result = ExamResult.objects.create(exam=self.exam, student=self.student, status=ExamResult.Status.ABSENT)
        self.assertIsNone(result.raw_score)
        self.assertIsNone(result.percentage)
        self.assertEqual(result.grade, "")
        self.assertTrue(result.is_absent)

    def test_released_result_requires_correction_workflow(self):
        result = ExamResult.objects.create(exam=self.exam, student=self.student, raw_score=Decimal("70.00"), grade="B")
        release_exam_results(self.exam, actor=self.actor)
        result.raw_score = Decimal("80.00")
        with self.assertRaises(Exception):
            result.save()

    def test_result_correction_updates_released_result_and_exam_can_be_republished(self):
        result = ExamResult.objects.create(exam=self.exam, student=self.student, raw_score=Decimal("70.00"), grade="B")
        release_exam_results(self.exam, actor=self.actor)
        correction = ResultCorrection.objects.create(exam_result=result, requested_by=self.actor, reason="Score sheet transcription fix", new_status=ExamResult.Status.RECORDED, new_raw_score=Decimal("82.00"), new_grade="A-", new_remarks="Corrected after moderation")
        apply_result_correction(correction, actor=self.actor)
        result.refresh_from_db()
        correction.refresh_from_db()
        self.assertEqual(result.raw_score, Decimal("82.00"))
        self.assertEqual(result.grade, "A-")
        self.assertIsNotNone(correction.applied_at)
        release_exam_results(self.exam, actor=self.actor)
        self.exam.refresh_from_db()
        self.assertEqual(self.exam.release_state, Exam.ReleaseState.REPUBLISHED)
        self.assertEqual(self.exam.release_version, 2)


class StaffWorkflowViewTests(TestCase):
    def setUp(self):
        self.temp_media = tempfile.mkdtemp()
        self.override = override_settings(MEDIA_ROOT=self.temp_media)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.addCleanup(lambda: shutil.rmtree(self.temp_media, ignore_errors=True))
        self.department = Department.objects.create(code="MED", name="Medicine")
        self.batch = Batch.objects.create(code="MBBS-2025", display_name="MBBS 2025 Cohort", academic_start_year=2025)
        self.module = Module.objects.create(department=self.department, code="ANA101", title="Anatomy I", semester="1")
        self.module.batches.set([self.batch])
        self.exam = Exam.objects.create(batch=self.batch, module=self.module, title="Professional exam", sat_on="2026-06-20", maximum_score=Decimal("100.00"), pass_mark=Decimal("50.00"))
        self.student = Student.objects.create(batch=self.batch, registration_number="2025MED0001", first_name="Asha", last_name="Silva")
        self.unpublished_student = Student.objects.create(batch=self.batch, registration_number="2025MED0002", first_name="Bimal", last_name="Fernando")
        self.other_batch = Batch.objects.create(code="MBBS-2024", display_name="MBBS 2024 Cohort", academic_start_year=2024)
        self.other_module = Module.objects.create(department=self.department, code="PHY101", title="Physiology I", semester="1")
        self.other_module.batches.set([self.other_batch])
        self.other_exam = Exam.objects.create(batch=self.other_batch, module=self.other_module, title="Foundations exam", sat_on="2026-01-20", maximum_score=Decimal("100.00"), pass_mark=Decimal("50.00"))
        self.other_student = Student.objects.create(batch=self.other_batch, registration_number="2024MED0003", first_name="Chami", last_name="Perera")
        self.user = get_user_model().objects.create_superuser(username="admin", email="admin@example.com", password="safe-password-123")
        self.client.force_login(self.user)

    def build_workbook(self, rows, filename):
        workbook = Workbook()
        sheet = workbook.active
        for row in rows:
            sheet.append(row)
        buffer = BytesIO()
        workbook.save(buffer)
        return SimpleUploadedFile(filename, buffer.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    def test_staff_dashboard_uses_staff_friendly_copy(self):
        response = self.client.get(reverse("staff-dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Track and review student performance records")
        self.assertContains(response, '<option value="registration_number" data-placeholder="Search registration no" selected>Registration No</option>', html=True)
        self.assertContains(response, 'placeholder="Search registration no"')
        self.assertContains(response, "Student search")
        self.assertContains(response, "Cohort review")
        self.assertContains(response, "Administration")

    def test_staff_dashboard_batch_search_filters_students_and_exams(self):
        response = self.client.get(reverse("staff-dashboard"), {"search_by": "batch", "q": "2025"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<option value="batch" data-placeholder="Search batch code or name" selected>Batch</option>', html=True)
        self.assertContains(response, self.student.registration_number)
        self.assertNotContains(response, self.other_student.registration_number)
        self.assertContains(response, self.exam.title)
        self.assertNotContains(response, self.other_exam.title)

    def test_exam_detail_shows_unpublished_students_filter(self):
        ExamResult.objects.create(exam=self.exam, student=self.student, raw_score=Decimal("80.00"), grade="A")
        response = self.client.get(reverse("exam-detail", args=[self.exam.id]), {"result_filter": "unpublished"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.unpublished_student.registration_number)
        self.assertNotContains(response, self.student.registration_number)

    def test_publish_summary_publishes_staged_rows(self):
        upload = ResultUpload.objects.create(exam=self.exam, uploaded_by=self.user, source_filename="publish.xlsx", source_file=self.build_workbook([["registration_number", "raw_score", "grade"], [self.student.registration_number, 92, "A"]], "publish.xlsx"), checksum_sha256="0" * 64)
        stage_result_upload(upload)
        response = self.client.post(reverse("upload-publish-summary", args=[upload.id]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ExamResult.objects.filter(exam=self.exam, student=self.student).count(), 1)
        upload.refresh_from_db()
        self.assertEqual(upload.status, ResultUpload.Status.PUBLISHED)

    def test_upload_review_export_honors_bucket(self):
        upload = ResultUpload.objects.create(exam=self.exam, uploaded_by=self.user, source_filename="review.xlsx", source_file=self.build_workbook([["registration_number", "raw_score"], [self.student.registration_number, 80], [self.student.registration_number, 81]], "review.xlsx"), checksum_sha256="0" * 64)
        stage_result_upload(upload)
        response = self.client.get(reverse("upload-review-export", args=[upload.id]), {"bucket": "duplicate"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("Duplicate registration_number found in workbook.", response.content.decode())


    def test_admin_can_delete_exam_after_confirmation(self):
        deletable_exam = Exam.objects.create(
            batch=self.batch,
            module=self.module,
            title="Delete me",
            sat_on="2026-07-01",
            maximum_score=Decimal("100.00"),
            pass_mark=Decimal("50.00"),
        )
        deletable_result = ExamResult.objects.create(
            exam=deletable_exam,
            student=self.student,
            raw_score=Decimal("77.00"),
            grade="B+",
        )
        release_exam_results(deletable_exam, actor=self.user)
        correction = ResultCorrection.objects.create(
            exam_result=deletable_result,
            requested_by=self.user,
            reason="Cleanup",
            new_status=ExamResult.Status.RECORDED,
            new_raw_score=Decimal("77.00"),
            new_grade="B+",
        )
        upload = ResultUpload.objects.create(
            exam=deletable_exam,
            uploaded_by=self.user,
            source_filename="delete.xlsx",
            source_file=self.build_workbook([["registration_number", "raw_score"], [self.student.registration_number, 77]], "delete.xlsx"),
            checksum_sha256="0" * 64,
        )
        delete_url = reverse("admin:results_exam_delete", args=[deletable_exam.id])
        response = self.client.get(delete_url)
        self.assertEqual(response.status_code, 200)
        response = self.client.post(delete_url, {"post": "yes"}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Exam.objects.filter(pk=deletable_exam.pk).exists())
        self.assertFalse(ExamResult.objects.filter(pk=deletable_result.pk).exists())
        self.assertFalse(ResultCorrection.objects.filter(pk=correction.pk).exists())
        self.assertFalse(ResultUpload.objects.filter(pk=upload.pk).exists())


