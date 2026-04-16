from django import forms

from backups.models import BackupRecord
from backups.services import validate_existing_backup_artifact


class ExistingBackupRegistrationForm(forms.Form):
    backup_type = forms.ChoiceField(choices=BackupRecord.BackupType.choices)
    filesystem_path = forms.CharField(max_length=500, help_text="Enter the absolute server path to the existing backup folder or SQL file.")

    def clean(self):
        cleaned_data = super().clean()
        backup_type = cleaned_data.get("backup_type")
        filesystem_path = cleaned_data.get("filesystem_path")
        if backup_type and filesystem_path:
            cleaned_data["resolved_path"] = validate_existing_backup_artifact(filesystem_path, backup_type)
        return cleaned_data
