from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Department(models.Model):
    code = models.CharField(max_length=16, unique=True)
    name = models.CharField(max_length=128, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Batch(models.Model):
    code = models.CharField(max_length=32, unique=True)
    display_name = models.CharField(max_length=128)
    academic_start_year = models.PositiveSmallIntegerField(validators=[MinValueValidator(2000), MaxValueValidator(3000)])
    graduation_year = models.PositiveSmallIntegerField(blank=True, null=True, validators=[MinValueValidator(2000), MaxValueValidator(3000)])
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = 'Batches'
        ordering = ['-academic_start_year', 'code']
        constraints = [models.CheckConstraint(condition=models.Q(graduation_year__isnull=True) | models.Q(graduation_year__gte=models.F('academic_start_year')), name='students_batch_graduation_after_start')]

    def clean(self):
        super().clean()
        if self.graduation_year and self.graduation_year < self.academic_start_year:
            raise ValidationError({'graduation_year': 'Graduation year cannot be earlier than the start year.'})

    def __str__(self):
        return self.display_name


class Student(models.Model):
    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        LEAVE = 'leave', 'On leave'
        GRADUATED = 'graduated', 'Graduated'
        WITHDRAWN = 'withdrawn', 'Withdrawn'

    batch = models.ForeignKey(Batch, on_delete=models.PROTECT, related_name='students')
    registration_number = models.CharField(max_length=32, unique=True)
    first_name = models.CharField(max_length=64)
    last_name = models.CharField(max_length=64)
    university_email = models.EmailField(blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['registration_number']
        indexes = [models.Index(fields=['batch', 'registration_number'])]

    @property
    def full_name(self):
        return f'{self.first_name} {self.last_name}'.strip()

    def __str__(self):
        return f'{self.registration_number} - {self.full_name}'


class Module(models.Model):
    department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name='modules')
    batches = models.ManyToManyField(Batch, related_name='streams', blank=True)
    code = models.CharField(max_length=32, unique=True)
    title = models.CharField(max_length=255)
    semester = models.CharField(max_length=16)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Stream / Subject'
        verbose_name_plural = 'Streams / Subjects'
        ordering = ['code']

    def clean(self):
        super().clean()
        normalized = str(self.semester or '').replace(' ', '')
        if not normalized:
            raise ValidationError({'semester': 'Semester is required.'})
        if '-' in normalized:
            parts = normalized.split('-', 1)
            if len(parts) != 2 or not all(part.isdigit() for part in parts):
                raise ValidationError({'semester': 'Semester must be a single number or a range like 1-3.'})
            start, end = int(parts[0]), int(parts[1])
            if start < 1 or end > 4 or start > end:
                raise ValidationError({'semester': 'Semester range must stay within 1-4 and increase from left to right.'})
        else:
            if not normalized.isdigit():
                raise ValidationError({'semester': 'Semester must be a single number or a range like 1-3.'})
            value = int(normalized)
            if value < 1 or value > 4:
                raise ValidationError({'semester': 'Semester must be between 1 and 4.'})
        self.semester = normalized

    @property
    def enrolled_batches_summary(self):
        batches = list(self.batches.order_by('-academic_start_year', 'code').values_list('display_name', flat=True))
        return ', '.join(batches) if batches else 'No batches assigned'

    def __str__(self):
        return f'{self.code} - {self.title}'
