"""Bulk API exports, lazy so packaging never loads Django application code."""
from importlib import import_module

from ._version import __author__, __version__

_EXPORTS = {
    'generics': (
        'BulkCreateAPIView', 'BulkDestroyAPIView', 'BulkModelViewSet', 'BulkUpdateAPIView',
        'ListBulkCreateAPIView', 'ListBulkCreateDestroyAPIView', 'ListBulkCreateUpdateAPIView',
        'ListBulkCreateUpdateDestroyAPIView', 'ListCreateBulkUpdateAPIView',
        'ListCreateBulkUpdateDestroyAPIView',
    ),
    'mixins': ('BulkCreateModelMixin', 'BulkDestroyModelMixin', 'BulkUpdateModelMixin'),
    'serializers': ('BulkListSerializer', 'BulkSerializerMixin'),
}
__all__ = [name for names in _EXPORTS.values() for name in names]


def __getattr__(name):
    for module, names in _EXPORTS.items():
        if name in names:
            value = getattr(import_module('.' + module, __name__), name)
            globals()[name] = value
            return value
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')


def __dir__():
    return sorted(set(globals()) | set(__all__))
