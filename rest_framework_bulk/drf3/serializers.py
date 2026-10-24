from collections.abc import Mapping
from copy import copy

from django.core.exceptions import ImproperlyConfigured, MultipleObjectsReturned, ObjectDoesNotExist
from django.db.models import Model, QuerySet
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import ValidationError
from rest_framework.fields import empty
from rest_framework.serializers import ListSerializer

from ..transactions import bulk_atomic, check_instance_database, write_database

__all__ = ['BulkListSerializer', 'BulkSerializerMixin']


def _lookup_fields(serializer):
    meta = getattr(serializer, 'Meta', None)
    return getattr(meta, 'lookup_fields', (getattr(meta, 'update_lookup_field', 'id'),))


def _lookup(serializer, data, names, *, validated=False):
    """Select the first supplied lookup, preserving the InfLabs ordered API."""
    for name in names:
        if name not in data or data[name] is empty:
            continue
        value = data[name]
        if value is None or isinstance(value, (bool, list, tuple, dict, set)):
            raise ValidationError(_('Invalid lookup value.'), code='invalid_lookup')
        field = serializer.fields.get(name)
        if field is None or field.source not in (None, name):
            raise ImproperlyConfigured('Bulk lookup fields must name direct serializer/model fields.')
        # Read-only normally short-circuits run_validation(). A temporary
        # shallow copy keeps custom run_validation/validators while enabling
        # typed input validation; never mutate the serializer's real field.
        if validated:
            # update() receives native validated_data, not wire input. Custom
            # field conversion need not be idempotent on its native result.
            pass
        elif field.read_only:
            writable = copy(field)
            writable.read_only = False
            value = writable.run_validation(value)
        else:
            # Unique validators need child.instance resolved first and run in
            # normal child validation, not while locating the target.
            value = field.to_internal_value(value)
        if value is None or value is empty or isinstance(value, bool):
            raise ValidationError(_('Invalid lookup value.'), code='invalid_lookup')
        try:
            hash(value)
        except TypeError:
            raise ValidationError(_('Invalid lookup value.'), code='invalid_lookup') from None
        return name, value
    raise ValidationError(_('Lookup field not found.'), code='missing_lookup')


def _target(queryset, name, value):
    try:
        return queryset.get(**{name: value})
    except (ObjectDoesNotExist, MultipleObjectsReturned, TypeError, ValueError, OverflowError):
        # Do not reveal whether a target exists outside the caller's queryset.
        raise ValidationError(_('Could not resolve a unique bulk target.'), code='invalid_target') from None


class BaseBulkSerializerMixin:
    @staticmethod
    def is_empty(value):
        return value is empty


class BulkSerializerMixin(BaseBulkSerializerMixin):
    def get_id_attrs(self):
        return _lookup_fields(self)

    def need_add_lookup_fields_request_methods(self):
        if getattr(self.Meta, 'need_add_lookup_fields_request_methods', False):
            return ('PUT', 'PATCH', 'POST')
        return ('PUT', 'PATCH')

    def to_internal_value(self, data):
        result = super().to_internal_value(data)
        view = self.context.get('view')
        request = getattr(view, 'request', None) or self.context.get('request')
        method = getattr(request, 'method', '')
        if isinstance(self.root, BulkListSerializer) and method in self.need_add_lookup_fields_request_methods():
            # POST remains a consumer-owned create/upsert policy; its lookup is
            # optional. PUT/PATCH must identify an existing scoped object.
            supplied = any(name in data for name in self.get_id_attrs())
            if method != 'POST' or supplied:
                name, value = _lookup(self, data, self.get_id_attrs())
                result[name] = value
        return result


class BulkListSerializer(BaseBulkSerializerMixin, ListSerializer):
    def get_id_attrs(self):
        return _lookup_fields(self.child)

    def _queryset(self, instance):
        if not isinstance(instance, QuerySet):
            raise ImproperlyConfigured('Bulk update requires a scoped Django QuerySet.')
        model = getattr(getattr(self.child, 'Meta', None), 'model', None)
        if isinstance(model, type) and issubclass(model, Model):
            qs_model = instance.model
            if not (issubclass(qs_model, model) or qs_model._meta.concrete_model is model._meta.concrete_model):
                raise ImproperlyConfigured('Bulk queryset model does not match the child serializer model.')
        alias = write_database(instance, self)
        return instance.using(alias), alias

    def to_internal_value(self, data):
        self._validated_targets = {}
        return super().to_internal_value(data)

    def run_child_validation(self, data):
        old_instance = getattr(self.child, 'instance', None)
        old_initial = getattr(self.child, 'initial_data', empty)
        try:
            self.child.instance = None
            self.child.initial_data = data
            if self.instance is not None and isinstance(data, Mapping):
                queryset, alias = self._queryset(self.instance)
                name, value = _lookup(self.child, data, self.get_id_attrs())
                with bulk_atomic(alias):
                    # Collection views hold an outer bulk transaction across
                    # validation+save. Lock before validators observe the row.
                    obj = _target(queryset.select_for_update(), name, value)
                    check_instance_database(obj, alias)
                    view = self.context.get('view')
                    if view is not None:
                        # Like DRF get_object(), authorize before state-aware
                        # validators can disclose details about this object.
                        view.check_object_permissions(view.request, obj)
                    self.child.instance = obj
                    validated = super().run_child_validation(data)
                    self._validated_targets[(name, value)] = (alias, obj._meta.concrete_model, obj.pk)
                    return validated
            return super().run_child_validation(data)
        finally:
            self.child.instance = old_instance
            if old_initial is empty:
                self.child.__dict__.pop('initial_data', None)
            else:
                self.child.initial_data = old_initial

    def _require_create_policy(self):
        if (getattr(getattr(self.child, 'Meta', None), 'need_add_lookup_fields_request_methods', False)
                and type(self).validate_bulk_create is BulkListSerializer.validate_bulk_create):
            raise ImproperlyConfigured('POST lookup retention requires an explicit validate_bulk_create policy.')

    def create(self, validated_data):
        self._require_create_policy()
        alias = write_database(serializer=self)
        # ListSerializer's default loop is not atomic. Preserve child.create
        # and opt-in consumer upsert hooks, never silently invent an upsert.
        with bulk_atomic(alias):
            self.validate_bulk_create(validated_data)
            created = []
            old_instance = getattr(self.child, 'instance', None)
            try:
                for attrs in validated_data:
                    self.child.instance = None
                    created.append(self.child.create(attrs))
            finally:
                self.child.instance = old_instance
            return created

    def validate_bulk_create(self, validated_data):
        """Preflight for consumer-owned POST upsert, before the first write.

        Lookup retention does not authorize a global child.create() upsert.
        Consumers enabling it must explicitly override this hook with their
        scoped lookup, object-permission and new-object admission policy.
        """
        if getattr(getattr(self.child, 'Meta', None), 'need_add_lookup_fields_request_methods', False):
            raise ImproperlyConfigured('POST lookup retention requires an explicit validate_bulk_create policy.')

    def update(self, queryset, all_validated_data):
        queryset, alias = self._queryset(queryset)
        with bulk_atomic(alias):
            # Lock the write-side rows where the backend supports row locks.
            queryset = queryset.select_for_update()
            targets = []
            seen = set()
            for attrs in all_validated_data:
                payload = dict(attrs)  # Do not mutate serializer.validated_data.
                name, value = _lookup(self.child, payload, self.get_id_attrs(), validated=True)
                obj = _target(queryset, name, value)
                check_instance_database(obj, alias)
                key = (obj._meta.concrete_model, obj.pk)
                identity = (alias, *key)
                validated_key = getattr(self, '_validated_targets', {}).get((name, value), identity)
                if validated_key != identity:
                    raise ValidationError(_('Bulk target changed after validation.'), code='changed_target')
                if key in seen:
                    raise ValidationError(_('Duplicate bulk target.'), code='duplicate_target')
                seen.add(key)
                payload.pop(name)
                targets.append((obj, payload))

            objects = [obj for obj, _ in targets]
            # EDW overrides validate_bulk_update without super(). View-level
            # object checks must not depend on that override calling us back.
            view = self.context.get('view')
            if view is not None:
                for obj in objects:
                    view.check_object_permissions(view.request, obj)
            self.validate_bulk_update(objects)
            updated = []
            old_instance = getattr(self.child, 'instance', None)
            try:
                for obj, attrs in targets:
                    self.child.instance = obj
                    updated.append(self.child.update(obj, attrs))
            finally:
                self.child.instance = old_instance
            return updated

    def validate_bulk_update(self, objects):
        """Consumer hook, called for all targets before the first update."""
