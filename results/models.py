from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Exam(models.Model):
    class ReleaseState(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        RELEASED = 'released', 'Released'
        REPUBLISHED = 'republished', 'Republished'

    batch = models.ForeignKey('students.Batch', on_delete=models.PROTECT, related_name='exams')
    module = models.ForeignKey('students.Module', verbose_name='stream / subject', on_delete=models.PROTECT, related_name='exams')
    title = models.CharField(max_length=128)
    sat_on = models.DateField()
    maximum_score = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal('100.00'), validators=[MinValueValidator(Decimal('0.01'))])
    pass_mark = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal('50.00'), validators=[MinValueValidator(Decimal('0.00'))])
    weight_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('100.00'), editable=False, validators=[MinValueValidator(Decimal('100.00')), MaxValueValidator(Decimal('100.00'))])
    release_state = models.CharField(max_length=16, choices=ReleaseState.choices, default=ReleaseState.DRAFT)
    release_version = models.PositiveIntegerField(default=0)
    results_released_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-sat_on', 'batch__academic_start_year', 'module__code', 'title']
        constraints = [
            models.UniqueConstraint(fields=['batch', 'module', 'title', 'sat_on'], name='results_exam_unique_per_batch_module_date'),
            models.CheckConstraint(condition=models.Q(pass_mark__lte=models.F('maximum_score')), name='results_exam_pass_mark_not_over_maximum'),
        ]

    @property
    def is_released(self):
        return self.release_state != self.ReleaseState.DRAFT

    def clean(self):
        super().clean()
        self.weight_percentage = Decimal('100.00')
        if self.pass_mark > self.maximum_score:
            raise ValidationError({'pass_mark': 'Pass mark cannot exceed the maximum score.'})
        if self.release_state == self.ReleaseState.DRAFT and self.release_version and self.results_released_at is None:
            raise ValidationError({'release_state': 'Draft exams cannot have a release version without a release timestamp.'})
        if self.release_state != self.ReleaseState.DRAFT and self.release_version == 0:
            raise ValidationError({'release_version': 'Released exams must have a release version.'})
        if self.module_id and self.batch_id and not self.module.batches.filter(pk=self.batch_id).exists():
            raise ValidationError({'batch': 'Selected batch must be enrolled to the selected stream / subject.'})

    def save(self, *args, **kwargs):
        self.weight_percentage = Decimal('100.00')
        return super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.module.code} - {self.title}'


class ExamResult(models.Model):
    class Status(models.TextChoices):
        RECORDED = 'recorded', 'Recorded'
        ABSENT = 'absent', 'Absent'
        WITHHELD = 'withheld', 'Withheld'

    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name='results')
    student = models.ForeignKey('students.Student', on_delete=models.PROTECT, related_name='exam_results')
    upload = models.ForeignKey('imports.ResultUpload', on_delete=models.SET_NULL, related_name='exam_results', blank=True, null=True)
    source_row = models.OneToOneField('imports.ResultUploadRow', on_delete=models.SET_NULL, related_name='published_result', blank=True, null=True)
    raw_score = models.DecimalField(max_digits=6, decimal_places=2, blank=True, null=True)
    percentage = models.DecimalField(max_digits=5, decimal_places=2, blank=True, null=True, validators=[MinValueValidator(Decimal('0.00')), MaxValueValidator(Decimal('100.00'))])
    grade = models.CharField(max_length=16, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.RECORDED)
    is_absent = models.BooleanField(default=False)
    is_withheld = models.BooleanField(default=False)
    remarks = models.TextField(blank=True)
    recorded_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['exam__sat_on', 'student__registration_number']
        constraints = [models.UniqueConstraint(fields=['exam', 'student'], name='results_exam_result_unique_student_exam')]

    def _synchronize_status_flags(self):
        if self.status == self.Status.RECORDED:
            if self.is_absent:
                self.status = self.Status.ABSENT
            elif self.is_withheld:
                self.status = self.Status.WITHHELD
        self.is_absent = self.status == self.Status.ABSENT
        self.is_withheld = self.status == self.Status.WITHHELD

    def clean(self):
        super().clean()
        self._synchronize_status_flags()
        if self.status == self.Status.ABSENT and (self.raw_score is not None or self.percentage is not None):
            raise ValidationError('Absent results cannot include a score or percentage.')
        if self.raw_score is not None and self.exam_id and self.raw_score > self.exam.maximum_score:
            raise ValidationError({'raw_score': 'Raw score cannot exceed the exam maximum score.'})
        if self.student_id and self.exam_id and self.student.batch_id != self.exam.batch_id:
            raise ValidationError({'student': 'Student batch does not match the exam batch.'})
        if self.pk:
            persisted = type(self).objects.select_related('exam').get(pk=self.pk)
            if persisted.exam.is_released and not getattr(self, '_allow_published_mutation', False):
                protected_fields = ('raw_score', 'grade', 'status', 'remarks', 'student_id', 'exam_id')
                if any(getattr(persisted, field) != getattr(self, field) for field in protected_fields):
                    raise ValidationError('Released results must be changed through the correction workflow.')
        elif self.exam_id and self.exam.is_released and not getattr(self, '_allow_published_mutation', False):
            raise ValidationError('Released exams cannot receive new published results outside the correction workflow.')

    def save(self, *args, **kwargs):
        allow_published_mutation = kwargs.pop('allow_published_mutation', False)
        if allow_published_mutation:
            self._allow_published_mutation = True
        self._synchronize_status_flags()
        if self.status == self.Status.ABSENT:
            self.raw_score = None
            self.percentage = None
            self.grade = ''
        elif self.raw_score is not None and self.exam_id:
            self.percentage = ((self.raw_score / self.exam.maximum_score) * Decimal('100')).quantize(Decimal('0.01'))
        else:
            self.percentage = None
        self.full_clean()
        result = super().save(*args, **kwargs)
        if hasattr(self, '_allow_published_mutation'):
            delattr(self, '_allow_published_mutation')
        return result

    def __str__(self):
        return f'{self.student.registration_number} - {self.exam.title}'


class ResultCorrection(models.Model):
    exam_result = models.ForeignKey(ExamResult, on_delete=models.CASCADE, related_name='corrections')
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, related_name='requested_result_corrections', blank=True, null=True)
    applied_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, related_name='applied_result_corrections', blank=True, null=True)
    reason = models.TextField()
    new_status = models.CharField(max_length=16, choices=ExamResult.Status.choices, default=ExamResult.Status.RECORDED)
    new_raw_score = models.DecimalField(max_digits=6, decimal_places=2, blank=True, null=True)
    new_grade = models.CharField(max_length=16, blank=True)
    new_remarks = models.TextField(blank=True)
    previous_values = models.JSONField(default=dict, blank=True)
    applied_values = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    applied_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ['-created_at']

    def clean(self):
        super().clean()
        if not self.reason.strip():
            raise ValidationError({'reason': 'A correction reason is required.'})
        if self.exam_result_id and not self.exam_result.exam.is_released:
            raise ValidationError({'exam_result': 'Corrections are only available once exam results have been released.'})
        if self.new_status == ExamResult.Status.ABSENT and self.new_raw_score is not None:
            raise ValidationError({'new_raw_score': 'Absent corrections cannot carry a score.'})

    def __str__(self):
        return f'Correction for {self.exam_result}'
