"""Public API regressions independent of the synthetic bulk model tests."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


def test_version_import_does_not_configure_django():
    root = Path(__file__).resolve().parents[1]
    env = {key: value for key, value in os.environ.items() if key not in {'DJANGO_SETTINGS_MODULE', 'PYTHONPATH'}}
    result = subprocess.run(
        [sys.executable, '-I', '-B', '-c',
         'import sys; sys.path.insert(0, sys.argv[1]); '
         'import rest_framework_bulk; '
         'assert rest_framework_bulk.__version__ == "0.3.0a1"; '
         'assert "django.conf" not in sys.modules; '
         'assert "rest_framework_bulk.generics" not in sys.modules', str(root)],
        env=env, check=False, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


def test_public_lazy_exports_and_modern_template_filter():
    import rest_framework_bulk
    from rest_framework_bulk.generics import BulkModelViewSet
    from rest_framework_bulk.serializers import BulkListSerializer, BulkSerializerMixin
    from rest_framework_bulk.templatetags.rest_framework_bulk_tags import is_list
    assert rest_framework_bulk.BulkListSerializer is BulkListSerializer
    assert rest_framework_bulk.BulkSerializerMixin is BulkSerializerMixin
    assert rest_framework_bulk.BulkModelViewSet is BulkModelViewSet
    assert is_list([1, 2])
    assert not is_list(4)
    with pytest.raises(AttributeError):
        getattr(rest_framework_bulk, 'missing_export')


def test_bulk_router_does_not_mutate_drf_global_routes():
    from rest_framework.routers import SimpleRouter
    from rest_framework_bulk.routes import BulkRouter, BulkRouterMixin
    assert 'put' not in SimpleRouter.routes[0].mapping
    assert BulkRouterMixin.routes[0].mapping['put'] == 'bulk_update'
    assert isinstance(BulkRouter(), SimpleRouter)
