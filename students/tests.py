from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from students.models import Batch, Department, Module, Student


class HomePageTests(TestCase):
    def test_home_page_loads(self):
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Medical Student Tracker')
        self.assertContains(response, 'Request access')


class BatchModelTests(TestCase):
    def test_graduation_year_cannot_precede_start_year(self):
        batch = Batch(code='MBBS-2025', display_name='MBBS 2025 Cohort', academic_start_year=2025, graduation_year=2024)
        with self.assertRaises(ValidationError):
            batch.full_clean()


class StudentModelTests(TestCase):
    def setUp(self):
        self.batch = Batch.objects.create(code='MBBS-2025', display_name='MBBS 2025 Cohort', academic_start_year=2025)

    def test_full_name_uses_first_and_last_name(self):
        student = Student.objects.create(batch=self.batch, registration_number='2025MED0001', first_name='Kamal', last_name='Perera')
        self.assertEqual(student.full_name, 'Kamal Perera')
        self.assertEqual(str(student), '2025MED0001 - Kamal Perera')


class ModuleModelTests(TestCase):
    def setUp(self):
        self.department = Department.objects.create(code='MED', name='Medicine')
        self.other_department = Department.objects.create(code='DEN', name='Dentistry')
        self.batch = Batch.objects.create(code='MBBS-2025', display_name='MBBS 2025 Cohort', academic_start_year=2025)
        self.other_batch = Batch.objects.create(code='MBBS-2024', display_name='MBBS 2024 Cohort', academic_start_year=2024)

    def test_module_code_is_unique_globally(self):
        module = Module.objects.create(department=self.department, code='ANA101', title='Anatomy I', semester='1')
        module.batches.set([self.batch])
        duplicate = Module(department=self.department, code='ANA101', title='Repeat Anatomy', semester='2')
        with self.assertRaises(ValidationError):
            duplicate.full_clean()

    def test_stream_or_subject_department_is_independent_of_batch(self):
        module = Module.objects.create(department=self.other_department, code='ANA102', title='Applied Anatomy', semester='1-3')
        module.batches.set([self.batch, self.other_batch])
        module.full_clean()
        self.assertEqual(module.batches.count(), 2)

    def test_semester_accepts_single_value_or_range(self):
        single = Module(department=self.department, code='ANA103', title='Single Semester', semester='2')
        single.full_clean()
        semester_range = Module(department=self.department, code='ANA104', title='Range Semester', semester='1-3')
        semester_range.full_clean()

    def test_semester_rejects_invalid_range(self):
        invalid = Module(department=self.department, code='ANA105', title='Invalid Semester', semester='3-1')
        with self.assertRaises(ValidationError):
            invalid.full_clean()

