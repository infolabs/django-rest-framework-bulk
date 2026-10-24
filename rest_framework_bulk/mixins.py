"""Public DRF 3 bulk mixins; legacy DRF 2 is not a supported runtime."""
from .drf3.mixins import BulkCreateModelMixin, BulkDestroyModelMixin, BulkUpdateModelMixin

__all__ = ['BulkCreateModelMixin', 'BulkDestroyModelMixin', 'BulkUpdateModelMixin']
