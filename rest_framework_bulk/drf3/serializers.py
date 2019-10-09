from __future__ import print_function, unicode_literals

from django.core.exceptions import ObjectDoesNotExist, MultipleObjectsReturned
from django.utils.translation import ugettext_lazy as _
from rest_framework.exceptions import ValidationError
from rest_framework.fields import empty
from rest_framework.serializers import ListSerializer
from rest_framework.settings import api_settings
from rest_framework.utils import html

__all__ = [
    'BulkListSerializer',
    'BulkSerializerMixin',
]


# import inspect
class BaseBulkSerializerMixin(object):

    @staticmethod
    def is_empty(value):
        # return not(bool(value) and not inspect.isclass(value) or value == 0)
        return value == empty


class BulkSerializerMixin(BaseBulkSerializerMixin):

    def get_id_attrs(self):
        return getattr(self.Meta, 'lookup_fields', ('id',))

    def need_add_lookup_fields_request_methods(self):
        # POST used for 'create or update' technique
        return ('PUT', 'PATCH', 'POST') if getattr(
            self.Meta, 'need_add_lookup_fields_request_methods', False
        ) else ('PUT', 'PATCH')

    def to_internal_value(self, data):
        result = super(BulkSerializerMixin, self).to_internal_value(data)
        id_attrs = self.get_id_attrs()
        request_method = getattr(getattr(self.context.get('view'), 'request'), 'method', '')

        # add lookup_field field back to validated data
        # since super by default strips out read-only fields
        # hence id will no longer be present in validated_data
        if isinstance(self.root, BulkListSerializer) and request_method in self.need_add_lookup_fields_request_methods():
            for id_attr in id_attrs:
                id_field = self.fields[id_attr]
                id_value = id_field.get_value(data)
                if not self.is_empty(id_value):
                    result[id_attr] = id_value
                    break
        return result


class BulkListSerializer(BaseBulkSerializerMixin, ListSerializer):

    def get_id_attrs(self):
        return getattr(self.child.Meta, 'lookup_fields', ('id',))

    def update(self, queryset, all_validated_data):
        id_attrs = self.get_id_attrs()
        y = len(id_attrs)
        all_validated_data_by_ids = {}
        for x, id_attr in enumerate(id_attrs):
            validated_data_by_id = all_validated_data_by_ids[id_attr] = {}
            data = all_validated_data[:]
            j = 0
            for i, obj in enumerate(all_validated_data):
                value = obj.pop(id_attr, empty)
                if not self.is_empty(value):
                    for k in [id_attrs[z] for z in range(x + 1, y)]:
                        if k in obj and self.is_empty(obj[k]):
                            del obj[k]
                    validated_data_by_id[value] = obj
                    del data[i + j]
                    j -= 1
            all_validated_data = data
        if all_validated_data:
            raise ValidationError(_('Lookup field not found.'))

        # since this method is given a queryset which can have many
        # model instances, first find all objects to update
        # and only then update the models
        objects_to_update_by_ids = {}
        all_objects_to_update = []
        for id_attr in id_attrs:

            validated_data = all_validated_data_by_ids[id_attr]

            validated_data_by_id_keys = validated_data.keys()
            if validated_data_by_id_keys:
                objects_to_update = queryset.filter(**{
                    '{}__in'.format(id_attr): validated_data_by_id_keys,
                })
                if len(validated_data_by_id_keys) != objects_to_update.count():
                    raise ValidationError(_('Could not find all objects to update.'))
                objects_to_update = list(objects_to_update)
                all_objects_to_update.extend(objects_to_update)
            else:
                objects_to_update = []
            for obj in objects_to_update:
                obj_id = getattr(obj, id_attr)
                if validated_data.get(obj_id, empty) == empty:
                    raise ValidationError(
                        _("Wrong type of lookup field `{}`, expected '{}'.").format(id_attr, type(obj_id).__name__))
            objects_to_update_by_ids[id_attr] = objects_to_update

        self.validate_bulk_update(all_objects_to_update)

        updated_objects = []
        for id_attr in id_attrs:
            validated_data = all_validated_data_by_ids[id_attr]
            for obj in objects_to_update_by_ids[id_attr]:
                obj_id = getattr(obj, id_attr)
                obj_validated_data = validated_data.get(obj_id)
                # use model serializer to actually update the model
                # in case that method is overwritten
                updated_objects.append(self.child.update(obj, obj_validated_data))

        return updated_objects

    def validate_bulk_update(self, objects):
        """
        Hook to ensure that the bulk update should be allowed.
        """
        pass

    def to_internal_value(self, data):
        """
        List of dicts of native values <- List of dicts of primitive datatypes.
        """
        if html.is_html_input(data):
            data = html.parse_html_list(data)

        if not isinstance(data, list):
            message = self.error_messages['not_a_list'].format(
                input_type=type(data).__name__
            )
            raise ValidationError({
                api_settings.NON_FIELD_ERRORS_KEY: [message]
            })

        if not self.allow_empty and len(data) == 0:
            message = self.error_messages['empty']
            raise ValidationError({
                api_settings.NON_FIELD_ERRORS_KEY: [message]
            })

        result = []
        errors = []
        id_attrs = self.get_id_attrs()
        for item in data:
            error = {}
            for id_attr in id_attrs:
                if id_attr in item:
                    try:
                        self.child.instance = self.instance.get(**{id_attr: item[id_attr]}) if self.instance else None
                    except (ObjectDoesNotExist, MultipleObjectsReturned) as e:
                        error.update({id_attr: [str(e)]})
                    break
            if not error:
                try:
                    # Do validation
                    validated = self.child.run_validation(item)
                except ValidationError as exc:
                    error = exc.detail
                else:
                    result.append(validated)
            errors.append(error)

        if any(errors):
            del self.initial_data
            raise ValidationError(errors)
        return result
