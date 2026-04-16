from django.apps import AppConfig


class AuditsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "audits"
    verbose_name = "Audit Trail"

    def ready(self):
        from audits.roles import connect_role_group_provisioning

        connect_role_group_provisioning(self)
