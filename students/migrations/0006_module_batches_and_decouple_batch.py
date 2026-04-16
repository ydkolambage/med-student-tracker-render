from django.db import migrations, models


def copy_module_batch_enrollments(apps, schema_editor):
    Module = apps.get_model('students', 'Module')
    through_model = Module.batches.through
    rows = []
    for module in Module.objects.exclude(batch_id__isnull=True).iterator():
        rows.append(through_model(module_id=module.pk, batch_id=module.batch_id))
    if rows:
        through_model.objects.bulk_create(rows, ignore_conflicts=True)


class Migration(migrations.Migration):

    dependencies = [
        ('students', '0005_remove_student_preferred_name'),
        ('results', '0005_exam_batch'),
    ]

    operations = [
        migrations.AddField(
            model_name='module',
            name='batches',
            field=models.ManyToManyField(blank=True, related_name='streams', to='students.batch'),
        ),
        migrations.RunPython(copy_module_batch_enrollments, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name='module',
            name='students_module_code_per_batch_unique',
        ),
        migrations.RemoveField(
            model_name='module',
            name='batch',
        ),
        migrations.AlterModelOptions(
            name='module',
            options={'ordering': ['code'], 'verbose_name': 'Stream / Subject', 'verbose_name_plural': 'Streams / Subjects'},
        ),
        migrations.AlterField(
            model_name='module',
            name='code',
            field=models.CharField(max_length=32, unique=True),
        ),
    ]

