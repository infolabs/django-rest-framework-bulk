"""Isolated modern test settings: synthetic models and in-memory SQLite only."""

SECRET_KEY = "synthetic-bulk-regression-tests-only"
DEBUG = False
USE_TZ = True
ALLOWED_HOSTS = ["testserver"]
DEFAULT_AUTO_FIELD = "django.db.models.AutoField"

DATABASES = {
    alias: {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
        "TEST": {"NAME": ":memory:"},
    }
    for alias in ("default", "other")
}
DATABASE_ROUTERS = []

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "rest_framework",
    "tests.testapp.apps.BulkTestAppConfig",
    "rest_framework_bulk.tests.simple_app",
]
MIDDLEWARE = []
MIGRATION_MODULES = {"bulk_testapp": None, "simple_app": None}
ROOT_URLCONF = "tests.test_bulk_safety"
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "UNAUTHENTICATED_USER": None,
}
