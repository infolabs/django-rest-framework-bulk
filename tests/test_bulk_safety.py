"""Regression contract for modern DRF bulk operations, using real ORM rows.

All databases are disposable in-memory SQLite aliases from tests.settings. These
are deliberately independent of the historical example app and its old tests.
"""

from copy import deepcopy
from uuid import uuid4

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError
from django.urls import include, path, resolve, reverse
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.fields import empty
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory
from rest_framework_nested.routers import NestedSimpleRouter

from rest_framework_bulk.generics import BulkModelViewSet
from rest_framework_bulk.routes import BulkRouter, BulkRouterMixin
from rest_framework_bulk.serializers import BulkListSerializer, BulkSerializerMixin

from tests.testapp.models import AuditEntry, Parent, Widget


pytestmark = pytest.mark.django_db(databases=["default", "other"])


class WidgetSerializer(BulkSerializerMixin, serializers.ModelSerializer):
    id = serializers.IntegerField(read_only=True, min_value=1)
    slug = serializers.SlugField(read_only=True, max_length=50)

    class Meta:
        model = Widget
        fields = ("id", "slug", "code", "name", "value", "visible", "parent")
        list_serializer_class = BulkListSerializer


class MultiLookupSerializer(WidgetSerializer):
    class Meta(WidgetSerializer.Meta):
        lookup_fields = ("id", "slug")


class SlugFallbackSerializer(WidgetSerializer):
    class Meta(WidgetSerializer.Meta):
        update_lookup_field = "slug"


class ExplicitLookupSerializer(SlugFallbackSerializer):
    class Meta(SlugFallbackSerializer.Meta):
        lookup_fields = ("id",)


class PostLookupSerializer(MultiLookupSerializer):
    class Meta(MultiLookupSerializer.Meta):
        need_add_lookup_fields_request_methods = True


class WidgetViewSet(BulkModelViewSet):
    queryset = Widget.objects.all()
    serializer_class = WidgetSerializer
    authentication_classes = []
    permission_classes = []
    db_alias = "default"

    def get_queryset(self):
        queryset = Widget.objects.all()
        return queryset if self.db_alias is None else queryset.using(self.db_alias)


class ScopedWidgetViewSet(WidgetViewSet):
    serializer_class = MultiLookupSerializer

    def filter_queryset(self, queryset):
        return super().filter_queryset(queryset).filter(visible=True)


class AllowedDestroyViewSet(ScopedWidgetViewSet):
    def allow_bulk_destroy(self, qs, filtered):
        return True


class ParentSerializer(BulkSerializerMixin, serializers.ModelSerializer):
    class Meta:
        model = Parent
        fields = ("id", "name")
        list_serializer_class = BulkListSerializer


class ParentViewSet(BulkModelViewSet):
    queryset = Parent.objects.all()
    serializer_class = ParentSerializer
    authentication_classes = []
    permission_classes = []


class NestedWidgetViewSet(AllowedDestroyViewSet):
    def get_queryset(self):
        return super().get_queryset().filter(parent_id=self.kwargs["parent_pk"])


class NestedBulkRouter(BulkRouterMixin, NestedSimpleRouter):
    """Exercise the documented mixin composition, not a second bulk router."""


router = BulkRouter()
router.register("widgets", WidgetViewSet, basename="widget")
router.register("parents", ParentViewSet, basename="parent")
nested_router = NestedBulkRouter(router, "parents", lookup="parent")
nested_router.register("widgets", NestedWidgetViewSet, basename="parent-widget")
urlpatterns = [path("", include(router.urls)), path("", include(nested_router.urls))]


class OtherWriteRouter:
    def db_for_read(self, model, **hints):
        if model._meta.app_label == "bulk_testapp":
            return "other"
        return None

    def db_for_write(self, model, **hints):
        if model._meta.app_label == "bulk_testapp":
            return "other"
        return None


class ReadDefaultWriteOtherRouter(OtherWriteRouter):
    def db_for_read(self, model, **hints):
        if model._meta.app_label == "bulk_testapp":
            return "default"
        return None


@pytest.fixture
def factory():
    return APIRequestFactory()


@pytest.fixture(params=("default", "other"))
def write_alias(request, settings):
    alias = request.param
    settings.DATABASE_ROUTERS = [OtherWriteRouter()] if alias == "other" else []
    return alias


def widget(alias="default", **kwargs):
    values = {"slug": uuid4().hex, "code": uuid4().hex, "name": "before"}
    values.update(kwargs)
    return Widget.objects.using(alias).create(**values)


def snapshot(alias="default"):
    return (
        list(
            Widget.objects.using(alias)
            .order_by("pk")
            .values_list("id", "slug", "code", "name", "value", "visible", "parent_id")
        ),
        list(AuditEntry.objects.using(alias).order_by("pk").values_list("message", flat=True)),
    )


def all_snapshots():
    return {alias: snapshot(alias) for alias in ("default", "other")}


def view_type(alias="default", serializer=WidgetSerializer, base=WidgetViewSet, **overrides):
    attrs = {"db_alias": alias, "serializer_class": serializer, **overrides}
    return type("TestWidgetViewSet", (base,), attrs)


def collection(factory, method, payload=None, view=WidgetViewSet):
    actions = {
        "get": "list",
        "post": "create",
        "put": "bulk_update",
        "patch": "partial_bulk_update",
        "delete": "bulk_destroy",
    }
    request = getattr(factory, method)("/widgets/", data=payload, format="json")
    return view.as_view(actions)(request)


def detail(factory, method, pk, payload=None, view=WidgetViewSet):
    actions = {"get": "retrieve", "put": "update", "patch": "partial_update", "delete": "destroy"}
    request = getattr(factory, method)(f"/widgets/{pk}/", data=payload, format="json")
    return view.as_view(actions)(request, pk=pk)


def serializer_context(factory, method="patch", alias="default", view=None):
    view = view or view_type(alias)()
    view.request = Request(getattr(factory, method)("/widgets/", [], format="json"))
    view.args = ()
    view.kwargs = {}
    view.format_kwarg = None
    return view.get_serializer_context()


def payload_for(obj, *, lookup="id", full=False, name="after"):
    data = {lookup: obj.pk if lookup == "id" else obj.slug, "name": name}
    if full:
        data["code"] = obj.code
    return data


def test_actual_bulk_serializer_types_are_used(factory):
    serializer = WidgetSerializer(data=[], many=True, context=serializer_context(factory, "post"))
    assert isinstance(serializer, BulkListSerializer)
    assert isinstance(serializer.child, BulkSerializerMixin)
    assert isinstance(WidgetViewSet(), BulkModelViewSet)


@pytest.mark.parametrize(
    ("serializer_class", "lookup"),
    [
        (WidgetSerializer, "id"),
        (SlugFallbackSerializer, "slug"),
        (ExplicitLookupSerializer, "id"),
        (MultiLookupSerializer, "slug"),
    ],
)
@pytest.mark.parametrize("method", ["put", "patch"])
def test_lookup_fields_precedence_and_update_lookup_field_fallback(
    factory, serializer_class, lookup, method
):
    obj = widget()
    response = collection(
        factory,
        method,
        [payload_for(obj, lookup=lookup, full=method == "put")],
        view_type(serializer=serializer_class),
    )
    assert response.status_code == 200, response.data
    obj.refresh_from_db()
    assert obj.name == "after"
    assert Widget.objects.count() == 1


def test_read_only_id_restored_as_validated_integer_without_mutating_input(factory):
    obj = widget()
    data = [{"id": str(obj.pk), "name": "after"}]
    original = deepcopy(data)
    serializer = WidgetSerializer(
        Widget.objects.all(), data=data, many=True, partial=True,
        context=serializer_context(factory),
    )
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data[0]["id"] == obj.pk
    assert type(serializer.validated_data[0]["id"]) is int
    validated_before_save = deepcopy(serializer.validated_data)
    serializer.save()
    assert serializer.validated_data == validated_before_save
    assert data == original
    assert serializer.child.fields["id"].read_only is True


def test_read_only_slug_restored_and_custom_field_run_validation_is_obeyed(factory):
    obj = widget(slug="blocked-slug")
    calls = []

    class PolicySlugField(serializers.SlugField):
        def run_validation(self, data=empty):
            calls.append(data)
            if data == "blocked-slug":
                raise ValidationError("Owner lookup policy refused.", code="lookup_policy")
            return super().run_validation(data)

    class PolicySerializer(SlugFallbackSerializer):
        slug = PolicySlugField(read_only=True)

    serializer = PolicySerializer(
        Widget.objects.all(), data=[{"slug": obj.slug, "name": "after"}],
        many=True, partial=True, context=serializer_context(factory),
    )
    assert not serializer.is_valid()
    assert calls == ["blocked-slug"]
    assert "Owner lookup policy refused." in str(serializer.errors)
    assert serializer.child.fields["slug"].read_only is True
    obj.refresh_from_db()
    assert obj.name == "before"


def test_read_only_lookup_field_validators_are_not_skipped(factory):
    obj = widget()

    def refuse(value):
        raise ValidationError("Read-only lookup validator executed.")

    class PolicySerializer(WidgetSerializer):
        id = serializers.IntegerField(read_only=True, validators=[refuse])

    serializer = PolicySerializer(
        Widget.objects.all(), data=[{"id": obj.pk}], many=True, partial=True,
        context=serializer_context(factory),
    )
    assert not serializer.is_valid()
    assert "Read-only lookup validator executed." in str(serializer.errors)


@pytest.mark.parametrize("lookup", ["id", "slug"])
@pytest.mark.parametrize("bad", [{"nested": 1}, [1], True, False, None, ""])
def test_untrusted_lookup_shapes_are_400_and_never_write(factory, lookup, bad):
    obj = widget()
    before = all_snapshots()
    response = collection(
        factory, "patch", [{"id": obj.pk, "name": "first"}, {lookup: bad, "name": "second"}],
        view_type(serializer=MultiLookupSerializer),
    )
    assert response.status_code == 400, response.data
    assert all_snapshots() == before


@pytest.mark.parametrize("bad", ["not-an-int", "1.5", 1.5, -1, 0, 10**100])
def test_invalid_integer_ids_are_client_errors_not_server_errors(factory, bad):
    widget()
    before = all_snapshots()
    response = collection(factory, "patch", [{"id": bad, "name": "after"}])
    assert response.status_code == 400, response.data
    assert all_snapshots() == before


@pytest.mark.parametrize("method", ["put", "patch"])
@pytest.mark.parametrize("lookup", ["id", "slug"])
def test_targets_are_resolved_only_in_filtered_queryset_with_generic_errors(factory, method, lookup):
    visible = widget(visible=True)
    hidden = widget(visible=False)
    before = all_snapshots()
    errors = []
    for target in (hidden.pk if lookup == "id" else hidden.slug, 999999 if lookup == "id" else "unknown"):
        second = {lookup: target, "name": "must-not-write"}
        if method == "put":
            second["code"] = uuid4().hex
        response = collection(
            factory, method, [payload_for(visible, full=method == "put"), second], ScopedWidgetViewSet,
        )
        assert response.status_code == 400, response.data
        errors.append(response.data)
        assert all_snapshots() == before
    assert errors[0] == errors[1]
    assert "DoesNotExist" not in str(errors)
    assert "matching query" not in str(errors)


@pytest.mark.parametrize("method", ["put", "patch"])
def test_missing_lookup_on_a_late_row_rejects_entire_update(factory, method):
    obj = widget()
    before = all_snapshots()
    payload = [payload_for(obj, full=method == "put"), {"name": "missing", "code": "new-code"}]
    response = collection(factory, method, payload)
    assert response.status_code == 400
    assert all_snapshots() == before


@pytest.mark.parametrize("method", ["put", "patch"])
@pytest.mark.parametrize("duplicate", ["id", "coerced-id", "slug", "id-and-slug"])
def test_duplicate_lookup_or_cross_lookup_same_object_is_rejected_before_child_update(
    factory, monkeypatch, method, duplicate
):
    obj = widget()
    calls = []
    original = MultiLookupSerializer.update

    def track(self, instance, data):
        calls.append(instance.pk)
        return original(self, instance, data)

    monkeypatch.setattr(MultiLookupSerializer, "update", track)
    if duplicate == "slug":
        first, second = {"slug": obj.slug}, {"slug": obj.slug}
    else:
        first = {"id": obj.pk}
        second = {
            "id": {"id": obj.pk, "coerced-id": str(obj.pk)}.get(duplicate, obj.pk)
        } if duplicate != "id-and-slug" else {"slug": obj.slug}
    for index, row in enumerate((first, second)):
        row["name"] = f"change-{index}"
        if method == "put":
            row["code"] = obj.code
    before = all_snapshots()
    response = collection(
        factory, method, [first, second], view_type(serializer=MultiLookupSerializer),
    )
    assert response.status_code == 400, response.data
    assert calls == []
    assert all_snapshots() == before


def test_child_instance_is_scoped_before_unique_validators_and_restored(factory):
    first, second = widget(), widget()
    observed = []

    class ObservingSerializer(WidgetSerializer):
        def validate(self, attrs):
            observed.append((self.instance.pk, self.initial_data["id"]))
            return super().validate(attrs)

    serializer = ObservingSerializer(
        Widget.objects.all(), data=[payload_for(first, full=True), payload_for(second, full=True)],
        many=True, context=serializer_context(factory, "put"),
    )
    sentinel_instance = Widget(pk=123456, code="sentinel", name="sentinel")
    sentinel_initial = {"sentinel": True}
    serializer.child.instance = sentinel_instance
    serializer.child.initial_data = sentinel_initial
    # code has a real ModelSerializer UniqueValidator; the correct instance
    # must already be attached or submitting each row's own code would fail.
    assert serializer.is_valid(), serializer.errors
    assert observed == [(first.pk, first.pk), (second.pk, second.pk)]
    assert serializer.child.instance is sentinel_instance
    assert serializer.child.initial_data is sentinel_initial
    serializer.save()
    assert list(Widget.objects.values_list("name", flat=True)) == ["after", "after"]


def test_child_instance_restored_when_later_validation_raises(factory):
    first, second = widget(), widget()
    seen = []

    class RejectSecondSerializer(WidgetSerializer):
        def validate(self, attrs):
            seen.append(self.instance.pk)
            if self.instance.pk == second.pk:
                raise ValidationError("late validator")
            return attrs

    serializer = RejectSecondSerializer(
        Widget.objects.all(), data=[payload_for(first), payload_for(second)],
        many=True, partial=True, context=serializer_context(factory),
    )
    sentinel = Widget(pk=9999)
    serializer.child.instance = sentinel
    original_initial_data = serializer.child.initial_data
    before = all_snapshots()
    assert not serializer.is_valid()
    assert seen == [first.pk, second.pk]
    assert serializer.child.instance is sentinel
    assert serializer.child.initial_data is original_initial_data
    assert all_snapshots() == before
    assert serializer.is_valid() is False  # DRF retains initial_data on failures.


def test_create_rows_never_inherit_stale_child_instance(factory):
    stale = widget()
    seen = []

    class MutatingValidatorSerializer(WidgetSerializer):
        def validate(self, attrs):
            seen.append(self.instance)
            self.instance = stale
            return attrs

    serializer = MutatingValidatorSerializer(
        data=[{"name": "one", "code": "one"}, {"name": "two", "code": "two"}],
        many=True, context=serializer_context(factory, "post"),
    )
    serializer.child.instance = stale
    assert serializer.is_valid(), serializer.errors
    assert seen == [None, None]
    assert serializer.child.instance is stale


def test_child_create_resets_instance_for_each_row_and_restores_original_instance(factory):
    sentinel = widget()
    seen = []

    class InstanceMutatingCreateSerializer(WidgetSerializer):
        def create(self, validated_data):
            seen.append(self.instance)
            result = super().create(validated_data)
            self.instance = result
            return result

    serializer = InstanceMutatingCreateSerializer(
        data=[{"name": "one", "code": "one"}, {"name": "two", "code": "two"}],
        many=True, context=serializer_context(factory, "post"),
    )
    serializer.child.instance = sentinel
    assert serializer.is_valid(), serializer.errors
    created = serializer.save()
    assert seen == [None, None]
    assert serializer.child.instance is sentinel
    assert [obj.name for obj in created] == ["one", "two"]
    assert all(obj.pk != sentinel.pk for obj in created)
    sentinel.refresh_from_db()
    assert sentinel.name == "before"


class ShapeSerializer(serializers.Serializer):
    name = serializers.CharField()


class BulkShapeSerializer(BulkSerializerMixin, ShapeSerializer):
    class Meta:
        list_serializer_class = BulkListSerializer


@pytest.mark.parametrize(
    "payload", [None, {}, "text", 42, True, [], [None], ["row"], [[]], [{}], [{"name": "ok"}]]
)
@pytest.mark.parametrize(
    "options", [{}, {"allow_empty": False}, {"min_length": 2}, {"max_length": 0}]
)
def test_list_shape_empty_min_max_and_child_type_errors_match_standard_drf(factory, payload, options):
    control = ShapeSerializer(data=deepcopy(payload), many=True, **options)
    bulk = BulkShapeSerializer(
        data=deepcopy(payload), many=True, context=serializer_context(factory, "post"), **options,
    )
    assert bulk.is_valid() == control.is_valid()
    assert bulk.errors == control.errors
    assert hasattr(bulk, "initial_data")


@pytest.mark.parametrize("operation", ["create", "update"])
@pytest.mark.parametrize("entry", ["direct", "view"])
@pytest.mark.parametrize("failure", ["child", "model-save"])
def test_late_create_update_and_model_save_failure_rolls_back_all_rows(
    factory, monkeypatch, write_alias, operation, entry, failure
):
    alias = write_alias
    first, second = widget(alias), widget(alias)
    attempts = []
    before = all_snapshots()

    class LateChildSerializer(WidgetSerializer):
        def create(self, validated_data):
            result = super().create(validated_data)
            if failure == "child":
                attempts.append(validated_data["name"])
                if validated_data["name"] == "explode":
                    raise RuntimeError("late child create")
            return result

        def update(self, instance, validated_data):
            result = super().update(instance, validated_data)
            if failure == "child":
                attempts.append(validated_data["name"])
                if validated_data["name"] == "explode":
                    raise RuntimeError("late child update")
            return result

    if failure == "model-save":
        original_save = Widget.save

        def save_then_fail(instance, *args, **kwargs):
            result = original_save(instance, *args, **kwargs)
            attempts.append(instance.name)
            if instance.name == "explode":
                raise RuntimeError("late model save")
            return result

        monkeypatch.setattr(Widget, "save", save_then_fail)

    if operation == "create":
        method = "post"
        payload = [{"name": "first-write", "code": "new-one"}, {"name": "explode", "code": "new-two"}]
    else:
        method = "patch"
        payload = [payload_for(first, name="first-write"), payload_for(second, name="explode")]
    with pytest.raises(RuntimeError, match="late"):
        if entry == "view":
            collection(factory, method, payload, view_type(alias, LateChildSerializer))
        else:
            args = [Widget.objects.using(alias).all()] if operation == "update" else []
            serializer = LateChildSerializer(
                *args, data=payload, many=True, partial=operation == "update",
                context=serializer_context(factory, method, alias),
            )
            assert serializer.is_valid(), serializer.errors
            serializer.save()
    assert attempts == ["first-write", "explode"]
    assert all_snapshots() == before


@pytest.mark.parametrize("operation", ["create", "update"])
@pytest.mark.parametrize("entry", ["direct", "view"])
def test_late_real_unique_constraint_error_rolls_back_complete_unit(factory, write_alias, operation, entry):
    first, second = widget(write_alias), widget(write_alias)
    before = all_snapshots()
    payload = [
        {"code": "same-new-unique-code", "name": "first"},
        {"code": "same-new-unique-code", "name": "second"},
    ]
    if operation == "update":
        payload[0]["id"] = first.pk
        payload[1]["id"] = second.pk
    method = "post" if operation == "create" else "patch"
    # The real row-wise UniqueValidator sees no committed conflicting code
    # during prevalidation. The second SQL INSERT/UPDATE must fail, after an
    # earlier successful write, and the complete unit must still roll back.
    with pytest.raises(IntegrityError):
        if entry == "view":
            collection(factory, method, payload, view_type(write_alias))
        else:
            args = [Widget.objects.using(write_alias)] if operation == "update" else []
            serializer = WidgetSerializer(
                *args, data=payload, many=True, partial=operation == "update",
                context=serializer_context(factory, method, write_alias),
            )
            assert serializer.is_valid(), serializer.errors
            serializer.save()
    assert all_snapshots() == before


@pytest.mark.parametrize(
    ("method", "hook"),
    [
        ("post", "perform_create"),
        ("post", "perform_bulk_create"),
        ("patch", "perform_update"),
        ("patch", "perform_bulk_update"),
        ("delete", "perform_destroy"),
        ("delete", "perform_bulk_destroy"),
    ],
)
def test_late_view_hooks_are_inside_the_selected_database_transaction(factory, write_alias, method, hook):
    alias = write_alias
    first = widget(alias, name="first-write")
    second = widget(alias, name="explode")
    before = all_snapshots()
    calls = []
    original_hook = getattr(AllowedDestroyViewSet, hook)

    def fail_after_hook(self, argument):
        result = original_hook(self, argument)
        calls.append(hook)
        AuditEntry.objects.create(message=f"after {hook}")
        if hook != "perform_destroy" or argument.name == "explode":
            raise RuntimeError("late view hook")
        return result

    view = view_type(alias, base=AllowedDestroyViewSet, **{hook: fail_after_hook})
    if method == "post":
        payload = [{"name": "new-first", "code": "fresh-one"}, {"name": "new-second", "code": "fresh-two"}]
    elif method == "patch":
        payload = [payload_for(first), payload_for(second)]
    else:
        payload = None
    with pytest.raises(RuntimeError, match="late view hook"):
        collection(factory, method, payload, view)
    assert len(calls) == (2 if hook == "perform_destroy" else 1)
    assert all_snapshots() == before


@pytest.mark.parametrize("method", ["post", "patch"])
def test_late_overridden_list_save_failure_rolls_back_view_unit(factory, write_alias, method):
    alias = write_alias
    obj = widget(alias)
    before = all_snapshots()

    class LateSaveList(BulkListSerializer):
        def save(self, **kwargs):
            super().save(**kwargs)
            AuditEntry.objects.create(message="save completed then failed")
            raise RuntimeError("late list save")

    class LateSaveSerializer(WidgetSerializer):
        class Meta(WidgetSerializer.Meta):
            list_serializer_class = LateSaveList

    payload = [{"name": "new", "code": "fresh"}] if method == "post" else [payload_for(obj)]
    with pytest.raises(RuntimeError, match="late list save"):
        collection(factory, method, payload, view_type(alias, LateSaveSerializer))
    assert all_snapshots() == before


@pytest.mark.parametrize("method", ["post", "patch"])
def test_response_representation_failure_also_rolls_back_view_unit(factory, write_alias, method):
    alias = write_alias
    obj = widget(alias)
    before = all_snapshots()

    class LateRepresentationSerializer(WidgetSerializer):
        def to_representation(self, instance):
            super().to_representation(instance)
            raise RuntimeError("late representation")

    payload = [{"name": "new", "code": "fresh"}] if method == "post" else [payload_for(obj)]
    with pytest.raises(RuntimeError, match="late representation"):
        collection(factory, method, payload, view_type(alias, LateRepresentationSerializer))
    assert all_snapshots() == before


def test_edw_validate_bulk_update_hook_precedes_child_update_without_requiring_super(factory):
    first, second = widget(), widget()
    events = []

    class EDWListSerializer(BulkListSerializer):
        def validate_bulk_update(self, objects):
            # EDW-style override intentionally does not call super().
            events.append(("bulk-hook", [obj.pk for obj in objects]))
            assert list(Widget.objects.values_list("name", flat=True)) == ["before", "before"]

    class EDWSerializer(WidgetSerializer):
        class Meta(WidgetSerializer.Meta):
            list_serializer_class = EDWListSerializer

        def update(self, instance, data):
            events.append(("update", instance.pk))
            return super().update(instance, data)

    def permission(self, request, obj):
        events.append(("permission", obj.pk))
        return super(type(self), self).check_object_permissions(request, obj)

    response = collection(
        factory, "patch", [payload_for(first), payload_for(second)],
        view_type(serializer=EDWSerializer, check_object_permissions=permission),
    )
    assert response.status_code == 200, response.data
    assert events == [
        ("permission", first.pk), ("permission", second.pk),  # before row validators
        ("permission", first.pk), ("permission", second.pk),  # before writes
        ("bulk-hook", [first.pk, second.pk]), ("update", first.pk), ("update", second.pk),
    ]


def test_failing_edw_hook_rolls_back_hook_writes_and_calls_no_child_update(factory, write_alias):
    first, second = widget(write_alias), widget(write_alias)
    updates = []
    before = all_snapshots()

    class RefusingList(BulkListSerializer):
        def validate_bulk_update(self, objects):
            assert [obj.pk for obj in objects] == [first.pk, second.pk]
            AuditEntry.objects.create(message="EDW hook write")
            raise ValidationError("EDW bulk policy")

    class RefusingSerializer(WidgetSerializer):
        class Meta(WidgetSerializer.Meta):
            list_serializer_class = RefusingList

        def update(self, instance, data):
            updates.append(instance.pk)
            return super().update(instance, data)

    response = collection(
        factory, "patch", [payload_for(first), payload_for(second)],
        view_type(write_alias, RefusingSerializer),
    )
    assert response.status_code == 400
    assert updates == []
    assert all_snapshots() == before


@pytest.mark.parametrize("method", ["put", "patch", "delete"])
def test_every_existing_object_permission_is_checked_before_first_write_even_with_edw_hook(
    factory, method, write_alias
):
    first, second = widget(write_alias), widget(write_alias)
    events = []
    before = all_snapshots()

    class EDWList(BulkListSerializer):
        def validate_bulk_update(self, objects):
            events.append(("hook", None))

    class TrackingSerializer(WidgetSerializer):
        class Meta(WidgetSerializer.Meta):
            list_serializer_class = EDWList

        def update(self, instance, data):
            events.append(("update", instance.pk))
            return super().update(instance, data)

    class ProtectedView(AllowedDestroyViewSet):
        serializer_class = TrackingSerializer
        db_alias = write_alias

        def check_object_permissions(self, request, obj):
            events.append(("permission", obj.pk))
            if obj.pk == second.pk:
                raise PermissionDenied("second object denied")

        def perform_destroy(self, instance):
            events.append(("delete", instance.pk))
            return super().perform_destroy(instance)

    payload = [payload_for(first, full=method == "put"), payload_for(second, full=method == "put")]
    response = collection(factory, method, payload if method != "delete" else None, ProtectedView)
    assert response.status_code == 403, response.data
    assert events == [("permission", first.pk), ("permission", second.pk)]
    assert all_snapshots() == before


@pytest.mark.parametrize("filter_style", ["identity", "clone", "filtered"])
def test_bulk_delete_requires_explicit_opt_in_not_queryset_identity(factory, filter_style):
    widget(visible=True)
    widget(visible=False)
    before = all_snapshots()

    def filter_queryset(self, queryset):
        if filter_style == "clone":
            return queryset.all()
        if filter_style == "filtered":
            return queryset.filter(visible=True)
        return queryset

    response = collection(factory, "delete", view=view_type(filter_queryset=filter_queryset))
    assert response.status_code == 400
    assert all_snapshots() == before


def test_explicit_delete_override_deletes_only_filtered_rows_and_checks_each_permission(factory):
    first, second = widget(), widget()
    hidden = widget(visible=False)
    checks = []

    class CheckingDeleteView(AllowedDestroyViewSet):
        def check_object_permissions(self, request, obj):
            checks.append(obj.pk)
            assert Widget.objects.filter(pk__in=[first.pk, second.pk]).count() == 2

    response = collection(factory, "delete", view=CheckingDeleteView)
    assert response.status_code == 204
    assert checks == [first.pk, second.pk]
    assert list(Widget.objects.values_list("pk", flat=True)) == [hidden.pk]


def test_late_model_delete_failure_restores_all_deleted_objects(factory, monkeypatch, write_alias):
    widget(write_alias, name="first-write")
    widget(write_alias, name="explode")
    before = all_snapshots()
    deletes = []
    original = Widget.delete

    def delete_then_fail(instance, *args, **kwargs):
        name = instance.name
        result = original(instance, *args, **kwargs)
        deletes.append(name)
        if name == "explode":
            raise RuntimeError("late model delete")
        return result

    monkeypatch.setattr(Widget, "delete", delete_then_fail)
    with pytest.raises(RuntimeError, match="late model delete"):
        collection(factory, "delete", view=view_type(write_alias, base=AllowedDestroyViewSet))
    assert deletes == ["first-write", "explode"]
    assert all_snapshots() == before


def test_post_opt_in_retains_typed_lookup_values_but_the_lookup_is_optional(factory):
    obj = widget()
    serializer = PostLookupSerializer(
        data=[
            {"id": str(obj.pk), "code": "new-one", "name": "one"},
            {"slug": obj.slug, "code": "new-two", "name": "two"},
            {"code": "new-three", "name": "three"},
        ],
        many=True, context=serializer_context(factory, "post"),
    )
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data[0]["id"] == obj.pk
    assert type(serializer.validated_data[0]["id"]) is int
    assert serializer.validated_data[1]["slug"] == obj.slug
    assert "id" not in serializer.validated_data[2]
    assert "slug" not in serializer.validated_data[2]


def test_post_without_opt_in_strips_lookup_and_creates_instead_of_upserting(factory):
    existing = widget()
    response = collection(
        factory, "post", [{"id": existing.pk, "slug": existing.slug, "name": "created", "code": "fresh"}],
    )
    assert response.status_code == 201, response.data
    assert response.data[0]["id"] != existing.pk
    existing.refresh_from_db()
    assert existing.name == "before"
    assert Widget.objects.count() == 2


@pytest.mark.parametrize("entry", ["direct", "view"])
def test_post_opt_in_without_explicit_policy_refuses_before_any_child_create(
    factory, write_alias, monkeypatch, entry
):
    existing = widget(write_alias)
    before = all_snapshots()
    calls = []
    original_create = PostLookupSerializer.create

    def create(self, data):
        calls.append(dict(data))
        return original_create(self, data)

    monkeypatch.setattr(PostLookupSerializer, "create", create)
    payload = [
        {"name": "would-create", "code": "first-new"},
        {"id": existing.pk, "name": "not-an-upsert", "code": "second-new"},
    ]
    # Lookup retention is not blanket permission for a child-owned upsert.
    with pytest.raises(ImproperlyConfigured):
        if entry == "view":
            collection(factory, "post", payload, view_type(write_alias, PostLookupSerializer))
        else:
            serializer = PostLookupSerializer(
                data=payload, many=True, context=serializer_context(factory, "post", write_alias),
            )
            assert serializer.is_valid(), serializer.errors
            serializer.save()
    assert calls == []
    assert all_snapshots() == before


def test_post_opt_in_child_create_owns_explicit_scoped_consumer_upsert_policy(factory, write_alias):
    existing = widget(write_alias)
    events = []
    create_calls = []

    class OwnerPolicyList(BulkListSerializer):
        def validate_bulk_create(self, validated_data):
            # A synthetic consumer policy, not automatic package upsert. Resolve
            # every existing target and authorize it before the first create.
            events.append("preflight")
            view = self.context["view"]
            queryset = view.filter_queryset(view.get_queryset())
            self.owner_targets = {}
            for attrs in validated_data:
                lookup = attrs.get("id")
                if lookup is not None:
                    try:
                        instance = queryset.get(pk=lookup)
                    except Widget.DoesNotExist:
                        raise ValidationError("Consumer target unavailable.") from None
                    if lookup in self.owner_targets:
                        raise ValidationError("Consumer duplicate target.")
                    view.check_object_permissions(view.request, instance)
                    self.owner_targets[lookup] = instance

    class OwnerPolicySerializer(PostLookupSerializer):
        class Meta(PostLookupSerializer.Meta):
            list_serializer_class = OwnerPolicyList

        def create(self, validated_data):
            events.append("create")
            create_calls.append((self.instance, dict(validated_data)))
            lookup = validated_data.pop("id", None)
            if lookup is None:
                return super().create(validated_data)
            instance = self.parent.owner_targets[lookup]
            return super().update(instance, validated_data)

    class OwnerView(ScopedWidgetViewSet):
        serializer_class = OwnerPolicySerializer
        db_alias = write_alias

        def check_object_permissions(self, request, obj):
            events.append(("permission", obj.pk))

    # No implicit POST instance resolution: the real UniqueValidator still
    # treats an existing code as create, not as an automatic update exception.
    response = collection(
        factory, "post", [{"id": existing.pk, "name": "owner-updated", "code": existing.code}], OwnerView,
    )
    assert response.status_code == 400
    assert create_calls == []
    response = collection(
        factory, "post", [
            {"id": existing.pk, "name": "owner-updated", "code": "owner-fresh"},
            {"name": "owner-created", "code": "owner-new"},
        ], OwnerView,
    )
    assert response.status_code == 201, response.data
    assert len(create_calls) == 2
    assert all(instance is None for instance, data in create_calls)
    assert events == ["preflight", ("permission", existing.pk), "create", "create"]
    existing.refresh_from_db(using=write_alias)
    assert existing.name == "owner-updated"
    assert Widget.objects.using(write_alias).count() == 2

    # A late out-of-scope upsert is rejected in consumer preflight; even an
    # earlier pure-create row must not have been written.
    hidden = widget(write_alias, visible=False)
    before = all_snapshots()
    create_calls.clear()
    response = collection(
        factory, "post", [
            {"name": "not-created", "code": "late-new"},
            {"id": hidden.pk, "name": "not-updated", "code": "late-hidden"},
        ], OwnerView,
    )
    assert response.status_code == 400
    assert create_calls == []
    assert all_snapshots() == before


@pytest.mark.parametrize("method", ["post", "patch", "delete"])
def test_explicit_queryset_alias_mismatching_write_router_fails_before_writes(factory, method):
    obj = widget("other")
    before = all_snapshots()
    payload = [{"name": "new", "code": "fresh"}] if method == "post" else [payload_for(obj)]
    with pytest.raises(ImproperlyConfigured):
        collection(
            factory, method, payload if method != "delete" else None,
            view_type("other", base=AllowedDestroyViewSet),
        )
    assert all_snapshots() == before


def test_direct_serializer_alias_mismatch_refuses_before_validation_or_save(factory):
    obj = widget("other")
    before = all_snapshots()
    serializer = WidgetSerializer(
        Widget.objects.using("other"), data=[payload_for(obj)], many=True, partial=True,
        context=serializer_context(factory),
    )
    with pytest.raises(ImproperlyConfigured):
        serializer.is_valid(raise_exception=True)
        serializer.save()
    assert all_snapshots() == before


@pytest.mark.django_db(transaction=True, databases=["default", "other"])
@pytest.mark.parametrize("method", ["post", "patch"])
@pytest.mark.parametrize("swallow", [False, True])
def test_custom_child_cross_alias_query_is_refused_even_when_hook_swallows_it(
    factory, write_alias, method, swallow
):
    alias = write_alias
    wrong_alias = "other" if alias == "default" else "default"
    obj = widget(alias)
    before = all_snapshots()

    class WrongDatabaseSerializer(WidgetSerializer):
        def attempt_cross_database(self):
            try:
                AuditEntry.objects.using(wrong_alias).create(message="must not escape")
            except ImproperlyConfigured:
                if not swallow:
                    raise

        def create(self, data):
            result = super().create(data)
            self.attempt_cross_database()
            return result

        def update(self, instance, data):
            result = super().update(instance, data)
            self.attempt_cross_database()
            return result

    payload = [{"name": "new", "code": "fresh"}] if method == "post" else [payload_for(obj)]
    with pytest.raises(ImproperlyConfigured):
        collection(factory, method, payload, view_type(alias, WrongDatabaseSerializer))
    assert all_snapshots() == before


@pytest.mark.parametrize("method", ["patch", "delete"])
def test_no_explicit_queryset_alias_uses_write_database_not_read_replica(factory, settings, method):
    settings.DATABASE_ROUTERS = [ReadDefaultWriteOtherRouter()]
    replica = widget("default", pk=100, name="replica")
    target = widget("other", pk=100, name="write-target")
    default_before = snapshot("default")
    view = view_type(None, base=AllowedDestroyViewSet)
    payload = [{"name": "new", "code": "fresh"}] if method == "post" else [payload_for(target)]
    response = collection(factory, method, payload if method != "delete" else None, view)
    assert response.status_code == {"post": 201, "patch": 200, "delete": 204}[method], response.data
    assert snapshot("default") == default_before
    replica.refresh_from_db(using="default")
    assert replica.name == "replica"
    if method == "patch":
        target.refresh_from_db(using="other")
        assert target.name == "after"
    elif method == "delete":
        assert not Widget.objects.using("other").filter(pk=target.pk).exists()
    else:
        assert Widget.objects.using("other").count() == 2


def test_unrelated_queryset_model_is_rejected_before_child_validation(factory):
    parent = Parent.objects.create(name="not-a-widget")
    child_calls = []

    class ModelGuardSerializer(WidgetSerializer):
        def validate(self, attrs):
            child_calls.append(dict(attrs))
            return attrs

    serializer = ModelGuardSerializer(
        Parent.objects.all(), data=[{"id": parent.pk, "name": "must-not-write"}],
        many=True, partial=True, context=serializer_context(factory),
    )
    before = all_snapshots()
    with pytest.raises(ImproperlyConfigured):
        serializer.is_valid(raise_exception=True)
        serializer.save()
    assert child_calls == []
    assert Parent.objects.get(pk=parent.pk).name == "not-a-widget"
    assert all_snapshots() == before


def test_post_missing_explicit_lookup_policy_rejected_before_view_child_validators(factory):
    calls = []

    class UnsafePostSerializer(PostLookupSerializer):
        def validate(self, attrs):
            calls.append(dict(attrs))
            raise ValidationError("A validator must not hide missing owner policy.")

    before = all_snapshots()
    with pytest.raises(ImproperlyConfigured):
        collection(
            factory, "post", [{"id": 1234, "name": "new", "code": "fresh"}],
            view_type(serializer=UnsafePostSerializer),
        )
    assert calls == []
    assert all_snapshots() == before


@pytest.mark.parametrize("method", ["post", "put", "patch"])
def test_late_view_validator_error_rolls_back_validation_side_effects(factory, write_alias, method):
    first, second = widget(write_alias), widget(write_alias)
    before = all_snapshots()
    calls = []

    class WritingValidatorSerializer(WidgetSerializer):
        def validate(self, attrs):
            calls.append(attrs["name"])
            AuditEntry.objects.create(message=attrs["name"])
            if attrs["name"] == "refuse":
                raise ValidationError("late validator policy")
            return attrs

    if method == "post":
        payload = [{"name": "first", "code": "fresh-one"}, {"name": "refuse", "code": "fresh-two"}]
    else:
        payload = [
            payload_for(first, full=method == "put", name="first"),
            payload_for(second, full=method == "put", name="refuse"),
        ]
    response = collection(factory, method, payload, view_type(write_alias, WritingValidatorSerializer))
    assert response.status_code == 400
    assert calls == ["first", "refuse"]
    assert all_snapshots() == before


def test_lookup_fields_uses_first_supplied_key_in_declared_order(factory):
    by_id = widget()
    by_slug = widget()
    serializer = MultiLookupSerializer(
        Widget.objects.all(), data=[{"id": by_id.pk, "slug": by_slug.slug, "name": "id-wins"}],
        many=True, partial=True, context=serializer_context(factory),
    )
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data[0]["id"] == by_id.pk
    assert "slug" not in serializer.validated_data[0]
    serializer.save()
    by_id.refresh_from_db()
    by_slug.refresh_from_db()
    assert by_id.name == "id-wins"
    assert by_slug.name == "before"
    assert by_id.slug != by_slug.slug


def test_serializer_dynamic_get_id_attrs_override_is_retained(factory):
    obj = widget()

    class DynamicLookupSerializer(WidgetSerializer):
        def get_id_attrs(self):
            return ("slug",)

    class DynamicLookupList(BulkListSerializer):
        def get_id_attrs(self):
            return self.child.get_id_attrs()

    class DynamicSerializer(DynamicLookupSerializer):
        class Meta(WidgetSerializer.Meta):
            list_serializer_class = DynamicLookupList

    response = collection(
        factory, "patch", [{"slug": obj.slug, "name": "dynamic"}],
        view_type(serializer=DynamicSerializer),
    )
    assert response.status_code == 200, response.data
    obj.refresh_from_db()
    assert obj.name == "dynamic"


def test_readonly_custom_lookup_native_value_is_not_reparsed_on_save(factory):
    obj = widget()
    parsed = []

    class WireOnlyInteger(serializers.IntegerField):
        def to_internal_value(self, value):
            parsed.append(value)
            if not isinstance(value, str) or not value.startswith("widget:"):
                raise ValidationError("Expected wire-format lookup.")
            return super().to_internal_value(value.removeprefix("widget:"))

    class WireSerializer(WidgetSerializer):
        id = WireOnlyInteger(read_only=True)

    serializer = WireSerializer(
        Widget.objects.all(), data=[{"id": f"widget:{obj.pk}", "name": "after"}],
        many=True, partial=True, context=serializer_context(factory),
    )
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data[0]["id"] == obj.pk
    serializer.save()
    assert parsed and set(parsed) == {f"widget:{obj.pk}"}
    obj.refresh_from_db()
    assert obj.name == "after"


def test_slug_reassigned_after_validation_cannot_retarget_direct_serializer(factory):
    original = widget(slug="selected")
    replacement = widget(slug="replacement")
    serializer = SlugFallbackSerializer(
        Widget.objects.all(), data=[{"slug": "selected", "name": "must-not-write"}],
        many=True, partial=True, context=serializer_context(factory),
    )
    assert serializer.is_valid(), serializer.errors
    Widget.objects.filter(pk=original.pk).update(slug="original-moved")
    Widget.objects.filter(pk=replacement.pk).update(slug="selected")
    before = all_snapshots()
    with pytest.raises(ValidationError):
        serializer.save()
    assert all_snapshots() == before


def test_child_instance_is_set_during_update_and_restored_after_save_failure(factory):
    first, second = widget(), widget()
    seen = []

    class InstanceAwareSerializer(WidgetSerializer):
        def update(self, instance, validated_data):
            seen.append((self.instance, instance))
            result = super().update(instance, validated_data)
            if instance.pk == second.pk:
                raise RuntimeError("late instance-aware update")
            return result

    serializer = InstanceAwareSerializer(
        Widget.objects.all(), data=[payload_for(first), payload_for(second)],
        many=True, partial=True, context=serializer_context(factory),
    )
    sentinel = Widget(pk=9999)
    serializer.child.instance = sentinel
    before = all_snapshots()
    assert serializer.is_valid(), serializer.errors
    with pytest.raises(RuntimeError, match="instance-aware"):
        serializer.save()
    assert len(seen) == 2
    assert all(attached is passed for attached, passed in seen)
    assert serializer.child.instance is sentinel
    assert all_snapshots() == before


def test_forbidden_target_permissions_run_before_its_validators(factory):
    forbidden = widget()
    validator_calls = []

    class ConfidentialSerializer(WidgetSerializer):
        def validate(self, attrs):
            validator_calls.append(self.instance.pk)
            raise ValidationError("This must not leak forbidden object state.")

    class ForbiddenView(WidgetViewSet):
        serializer_class = ConfidentialSerializer

        def check_object_permissions(self, request, obj):
            raise PermissionDenied("No object access.")

    before = all_snapshots()
    response = collection(factory, "patch", [payload_for(forbidden)], ForbiddenView)
    assert response.status_code == 403, response.data
    assert validator_calls == []
    assert "must not leak" not in str(response.data)
    assert all_snapshots() == before


def test_bulk_delete_deduplicates_join_results_before_permissions_and_delete_hooks(factory):
    parent = Parent.objects.create(name="join-source")
    first = widget(parent=parent)
    second = widget(parent=parent)
    events = []

    class JoinView(AllowedDestroyViewSet):
        def filter_queryset(self, queryset):
            return queryset.filter(parent__widgets__visible=True)

        def check_object_permissions(self, request, obj):
            events.append(("permission", obj.pk))

        def perform_destroy(self, obj):
            events.append(("delete", obj.pk))
            return super().perform_destroy(obj)

    # Both widgets join to both children of the parent: four raw rows, two
    # object identities. Owner hooks must not execute twice for either object.
    assert JoinView().filter_queryset(Widget.objects.all()).count() == 4
    response = collection(factory, "delete", view=JoinView)
    assert response.status_code == 204
    assert events == [
        ("permission", first.pk), ("permission", second.pk),
        ("delete", first.pk), ("delete", second.pk),
    ]
    assert Widget.objects.count() == 0


def test_denied_destroy_policy_rolls_back_its_own_database_writes(factory, write_alias):
    widget(write_alias)
    before = all_snapshots()

    class DenyingPolicyView(WidgetViewSet):
        db_alias = write_alias

        def allow_bulk_destroy(self, qs, filtered):
            AuditEntry.objects.create(message="policy ran then refused")
            return False

    response = collection(factory, "delete", view=DenyingPolicyView)
    assert response.status_code == 400
    assert all_snapshots() == before


def test_post_with_unadapted_read_replica_validator_fails_closed_without_writes(factory, settings):
    settings.DATABASE_ROUTERS = [ReadDefaultWriteOtherRouter()]
    before = all_snapshots()
    # A stock UniqueValidator reads the configured replica. That is an
    # unsupported cross-alias query inside a single-write-alias unit, not an
    # excuse to silently read stale uniqueness state or commit partial data.
    with pytest.raises(ImproperlyConfigured):
        collection(factory, "post", [{"name": "new", "code": "fresh"}], view_type(None))
    assert all_snapshots() == before


def test_regular_single_crud_and_collection_get_options_are_preserved(factory):
    response = collection(factory, "post", {"name": "created", "code": "single", "value": 1})
    assert response.status_code == 201, response.data
    pk = response.data["id"]
    assert detail(factory, "get", pk).status_code == 200
    assert collection(factory, "get").status_code == 200
    response = detail(factory, "put", pk, {"name": "replaced", "code": "single", "value": 2})
    assert response.status_code == 200, response.data
    response = detail(factory, "patch", pk, {"name": "patched"})
    assert response.status_code == 200, response.data
    assert Widget.objects.get(pk=pk).value == 2
    assert Widget.objects.get(pk=pk).name == "patched"
    response = WidgetViewSet.as_view({"get": "list", "post": "create"})(factory.options("/widgets/"))
    assert response.status_code == 200, response.data
    assert detail(factory, "delete", pk).status_code == 204
    assert not Widget.objects.filter(pk=pk).exists()


@pytest.mark.parametrize(
    ("name", "kwargs", "mapping"),
    [
        ("widget-list", {}, {"get": "list", "post": "create", "put": "bulk_update", "patch": "partial_bulk_update", "delete": "bulk_destroy"}),
        ("widget-detail", {"pk": 7}, {"get": "retrieve", "put": "update", "patch": "partial_update", "delete": "destroy"}),
        ("parent-widget-list", {"parent_pk": 3}, {"get": "list", "post": "create", "put": "bulk_update", "patch": "partial_bulk_update", "delete": "bulk_destroy"}),
        ("parent-widget-detail", {"parent_pk": 3, "pk": 7}, {"get": "retrieve", "put": "update", "patch": "partial_update", "delete": "destroy"}),
    ],
)
def test_default_and_nested_bulk_router_resolve_modern_action_maps(name, kwargs, mapping):
    match = resolve(reverse(name, kwargs=kwargs))
    for method, action in mapping.items():
        assert match.func.actions[method] == action
    assert match.kwargs == {key: str(value) for key, value in kwargs.items()}


def test_default_router_api_root_and_format_suffix_work(factory):
    root = resolve(reverse("api-root"))
    request = factory.get("/")
    request.resolver_match = root
    response = root.func(request, **root.kwargs)
    assert response.status_code == 200
    assert "widgets" in response.data
    match = resolve(reverse("widget-list", kwargs={"format": "json"}))
    assert match.func.actions["patch"] == "partial_bulk_update"
    assert match.kwargs["format"] == "json"


@pytest.mark.parametrize("method", ["put", "patch", "delete"])
def test_nested_bulk_routes_keep_parent_scope_and_dispatch_actual_view(factory, method):
    parent = Parent.objects.create(name="selected")
    other_parent = Parent.objects.create(name="outside")
    target = widget(parent=parent)
    outside = widget(parent=other_parent)
    route = reverse("parent-widget-list", kwargs={"parent_pk": parent.pk})
    match = resolve(route)
    if method == "delete":
        response = match.func(factory.delete(route), **match.kwargs)
        assert response.status_code == 204
        assert not Widget.objects.filter(pk=target.pk).exists()
        assert Widget.objects.filter(pk=outside.pk).exists()
    else:
        before = all_snapshots()
        response = match.func(
            getattr(factory, method)(route, [payload_for(outside, full=method == "put")], format="json"),
            **match.kwargs,
        )
        assert response.status_code == 400
        assert all_snapshots() == before
        response = match.func(
            getattr(factory, method)(route, [payload_for(target, full=method == "put")], format="json"),
            **match.kwargs,
        )
        assert response.status_code == 200, response.data
        target.refresh_from_db()
        outside.refresh_from_db()
        assert target.name == "after"
        assert outside.name == "before"
