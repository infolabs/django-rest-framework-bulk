from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from rest_framework import status
from rest_framework.mixins import CreateModelMixin
from rest_framework.response import Response

from ..transactions import bulk_atomic, check_instance_database, write_database

__all__ = ['BulkCreateModelMixin', 'BulkDestroyModelMixin', 'BulkUpdateModelMixin']


class BulkCreateModelMixin(CreateModelMixin):
    def create(self, request, *args, **kwargs):
        if not isinstance(request.data, list):
            return super().create(request, *args, **kwargs)
        serializer = self.get_serializer(data=request.data, many=True)
        queryset = self.get_queryset()
        alias = write_database(queryset, serializer)
        # Include custom perform_* hooks and materialization in the unit. A
        # serializer representation failure must not leave a committed write.
        with bulk_atomic(alias):
            require_policy = getattr(serializer, '_require_create_policy', None)
            if require_policy is not None:
                require_policy()  # Refuse legacy upsert before its validators.
            serializer.is_valid(raise_exception=True)
            self.perform_bulk_create(serializer)
            return Response(serializer.data, status=status.HTTP_201_CREATED)

    def perform_bulk_create(self, serializer):
        return self.perform_create(serializer)


class BulkUpdateModelMixin:
    def get_object(self):
        lookup = self.lookup_url_kwarg or self.lookup_field
        if lookup in self.kwargs:
            return super().get_object()
        # OPTIONS on the collection has no individual object. View-level
        # permission checks still run in APIView.initial().
        return None

    def bulk_update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        queryset = self.get_queryset()
        alias = write_database(queryset)
        with bulk_atomic(alias):
            scoped = self.filter_queryset(queryset.using(alias))
            if write_database(scoped) != alias:
                raise ImproperlyConfigured('Bulk filter changed the write database.')
            serializer = self.get_serializer(scoped, data=request.data, many=True, partial=partial)
            serializer.is_valid(raise_exception=True)
            self.perform_bulk_update(serializer)
            return Response(serializer.data, status=status.HTTP_200_OK)

    def partial_bulk_update(self, request, *args, **kwargs):
        kwargs['partial'] = True
        return self.bulk_update(request, *args, **kwargs)

    def perform_update(self, serializer):
        serializer.save()

    def perform_bulk_update(self, serializer):
        return self.perform_update(serializer)


class BulkDestroyModelMixin:
    def allow_bulk_destroy(self, qs, filtered):
        """Explicit consumer opt-in is required; queryset identity is not auth."""
        return False

    def bulk_destroy(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        alias = write_database(queryset)
        with bulk_atomic(alias):
            qs = queryset.using(alias)
            filtered = self.filter_queryset(qs)
            if write_database(filtered) != alias:
                raise ImproperlyConfigured('Bulk filter changed the write database.')
            if not self.allow_bulk_destroy(qs, filtered):
                transaction.set_rollback(True, using=alias)
                return Response(status=status.HTTP_400_BAD_REQUEST)
            # Check every target before any delete (including custom hooks).
            objects = []
            seen = set()
            for obj in filtered.select_for_update():
                check_instance_database(obj, alias)
                key = (obj._meta.concrete_model, obj.pk)
                if key not in seen:
                    seen.add(key)
                    objects.append(obj)
            for obj in objects:
                self.check_object_permissions(request, obj)
            self.perform_bulk_destroy(objects)
            return Response(status=status.HTTP_204_NO_CONTENT)

    def perform_destroy(self, instance):
        # Preserve consumer delete() overrides which may not accept using=.
        instance.delete()

    def perform_bulk_destroy(self, objects):
        for obj in objects:
            self.perform_destroy(obj)
