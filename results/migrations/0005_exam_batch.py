from django.db import migrations, models
import django.db.models.deletion


def copy_exam_batch(apps, schema_editor):
    Exam = apps.get_model('results', 'Exam')
    for exam in Exam.objects.select_related('module').iterator():
        if exam.module_id and getattr(exam.module, 'batch_id', None):
            exam.batch_id = exam.module.batch_id
            exam.save(update_fields=['batch'])


class Migration(migrations.Migration):

    dependencies = [
        ('students', '0005_remove_student_preferred_name'),
        ('results', '0004_remove_exam_exam_type_alter_exam_maximum_score_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='exam',
            name='batch',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='exams', to='students.batch'),
        ),
        migrations.RunPython(copy_exam_batch, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name='exam',
            name='results_exam_unique_per_module_date',
        ),
        migrations.AlterModelOptions(
            name='exam',
            options={'ordering': ['-sat_on', 'batch__academic_start_year', 'module__code', 'title']},
        ),
        migrations.AlterField(
            model_name='exam',
            name='batch',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='exams', to='students.batch'),
        ),
        migrations.AddConstraint(
            model_name='exam',
            constraint=models.UniqueConstraint(fields=('batch', 'module', 'title', 'sat_on'), name='results_exam_unique_per_batch_module_date'),
        ),
    ]
