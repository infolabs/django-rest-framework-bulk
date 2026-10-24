from django.apps import AppConfig


class BulkTestAppConfig(AppConfig):
    default_auto_field = "django.db.models.AutoField"
    name = "tests.testapp"
    label = "bulk_testapp"
