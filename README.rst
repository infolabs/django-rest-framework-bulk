Django REST Framework Bulk — InfLabs
===================================

``0.3.0a1`` is an **unreleased modernization candidate**, not the generic PyPI
``0.2.1`` implementation. It keeps the ``rest_framework_bulk`` namespace and the
InfLabs lookup/permission hooks required by EDW.

Supported target
----------------

* Python 3.12 (metadata requires Python >=3.12; other versions are not verified).
* Django 5.2 LTS, DRF 3.18.
* Acceptance pins and test/build commands are in ``requirements-dev.txt`` and
  ``tox.ini``. Django <5.2 and DRF 2 are not supported by this candidate.

Do not install the old PyPI release by name and assume it contains these changes.
Build from a reviewed commit of
https://github.com/infolabs/django-rest-framework-bulk, record its wheel SHA256,
and install that artifact into a separate environment for acceptance. This branch
has not been published as a release.

Example
-------

.. code-block:: python

    from rest_framework.serializers import ModelSerializer
    from rest_framework_bulk import BulkListSerializer, BulkSerializerMixin
    from rest_framework_bulk.generics import ListBulkCreateUpdateDestroyAPIView

    class FooSerializer(BulkSerializerMixin, ModelSerializer):
        class Meta:
            model = FooModel
            fields = ('id', 'name')
            list_serializer_class = BulkListSerializer

    class FooView(ListBulkCreateUpdateDestroyAPIView):
        queryset = FooModel.objects.all()  # Must already enforce caller scope.
        serializer_class = FooSerializer
        # Set explicit authentication/permission_classes for the application.

A JSON object POST uses ordinary DRF create. A JSON list POST uses bulk create.
Collection PUT/PATCH updates the explicitly identified objects only. Individual
resource CRUD still uses DRF's ordinary viewset actions.

Routers
-------

``rest_framework_bulk.routes.BulkRouter`` and ``BulkRouterMixin`` map collection
PUT/PATCH/DELETE to ``bulk_update``, ``partial_bulk_update``, and ``bulk_destroy``.
They do not mutate DRF's global ``SimpleRouter.routes``. For a nested router:

.. code-block:: python

    from rest_framework_nested.routers import NestedSimpleRouter
    from rest_framework_bulk.routes import BulkRouterMixin

    class NestedBulkRouter(BulkRouterMixin, NestedSimpleRouter):
        pass

``drf-nested-routers`` is an optional consumer dependency, not required by this
package. Router availability does not imply that collection DELETE is enabled.

Lookup and validation contract
------------------------------

``BulkSerializerMixin`` restores typed read-only lookup values for PUT/PATCH.
It supports ordered ``Meta.lookup_fields`` (default ``('id',)``), the legacy
single ``Meta.update_lookup_field`` fallback, and overridable ``get_id_attrs()``
on both child/list serializers (used by EDW dynamic REST metadata).

Only direct serializer/model field names are supported as lookup fields. The
first supplied lookup wins; subsequent writable fields remain ordinary update
payload, so applications must define any additional cross-field consistency
rules. Invalid/unhashable/null/bool lookups, unresolved or non-unique scoped
targets, and repeated targets (including the same object via id and slug) are
rejected before any child update. Lookup validation runs the read-only field's
custom ``run_validation`` using a temporary writable copy; normal writable-field
validators run with the per-row ``child.instance``. Previous child instance and
initial-data state are restored even after failure.

Unknown objects and objects outside the provided queryset produce the same
fixed target error. Validation uses DRF's standard list/type/empty/min/max
handling. ``validate_bulk_update(objects)`` remains a consumer hook before the
first child update. View-level ``check_object_permissions`` is also called for
each existing update/delete target, independently of an overridden EDW hook.

POST lookup retention is not upsert authorization
------------------------------------------------

``Meta.need_add_lookup_fields_request_methods = True`` retains lookup fields on
bulk POST for consumers such as EDW with a custom ``child.create`` upsert.
The library does not invent an upsert policy. An opt-in serializer MUST also
provide an explicit ``BulkListSerializer.validate_bulk_create(validated_data)``
override. Without it, bulk save raises ``ImproperlyConfigured`` before writes.

This hook must preflight the entire batch: caller scope, permissions on existing
targets, ambiguous id/slug matches and duplicates, admission of genuinely new
objects, and database routing. Merely overriding it with ``pass`` is not a safe
policy. A required new slug cannot be distinguished from an existing out-of-scope
slug by a generic library, which is why the library fails closed instead of
performing a global existence check. Ordinary flag=False ModelSerializer pure
create remains unchanged; arbitrary custom ``child.create`` implementations are
trusted consumer code, not automatically made permission-safe by this library.
Single-object POST remains an ordinary DRF/consumer operation, not this bulk
protocol.

Bulk DELETE deliberately requires opt-in
---------------------------------------

Default ``allow_bulk_destroy(qs, filtered)`` returns False, even for a cloned or
filtered queryset. Queryset object identity is not authorization. Consumers must
explicitly override this hook with an application-specific admission policy;
per-object permission checks still run for every target before the first delete.
Delegating an override back to ``super()`` does not enable DELETE. Model
``delete()`` can cascade to descendants/related rows outside the root selection;
the explicit consumer policy must authorize those consequences too. Root object
permission checks alone do not certify cascade authorization.

``perform_bulk_destroy`` receives the preflighted list of model instances, not an
unevaluated queryset. Per-object ``perform_destroy`` and model ``delete()`` hooks
are retained; the library does not replace them with ``queryset.delete()``.

Transaction and routing boundary
--------------------------------

Bulk collection create/update/delete, standard serializer loops, custom view
``perform_*`` hooks and serializer representation materialization run in an
atomic transaction on one selected write alias. Errors before commit roll back
that alias. Direct ``BulkListSerializer.create/update`` calls also guard their
own write loops, but callers own validation/representation outside those calls.
Direct ``is_valid()`` and ``save()`` must share an application-owned transaction
and row-lock scope if validation depends on current database state; two separate
calls cannot guarantee that state stayed unchanged. Changed lookup-to-primary-key
identity is rejected at save, but same-row concurrent state changes still require
that shared transaction. Collection views hold it across both phases.

The queryset's explicit alias must agree with ``router.db_for_write(model)``.
Implicit read routing is not used to select the write transaction. Each resolved
instance's database and instance-aware write route are checked. Update/delete
select row locks where the database supports them; SQLite does not provide the
same concurrency guarantees as PostgreSQL. Rollback assumes a transactional
backend/table engine. Per-target lookups/locks are O(N); applications must bound
batch/request sizes and handle deadlock/serialization failures with their own
idempotency/retry policy. No implicit retry is performed by the package.

A scoped ``execute_wrapper`` refuses ORM/cursor.execute/executemany on other
configured Django aliases in the current synchronous connection context. A
refused attempt poisons the unit even if a custom hook catches the immediate
exception. This is NOT distributed atomicity and NOT a sandbox: callproc, raw
cursors, new connections/threads/async contexts, non-Django drivers and external
side effects are outside this guarantee. Consumers must not use them in bulk
hooks. Post-commit callback failures happen AFTER commit and cannot be rolled
back; they need their own error/idempotency policy. Rendering by a custom
renderer after the view returns is also outside the database unit.

EDW integration gate
--------------------

The old EDW pin cannot be declared compatible merely because its router imports:

* Collection DELETE needs an explicit policy migration (including overrides
  which currently delegate to ``super``).
* Legacy bulk POST-retention/upsert needs a scoped ``validate_bulk_create``
  implementation.
* EDW's ``filter_class`` declarations and old validator ``set_context`` APIs need
  separate consumer-side modernization for current DRF.
* Consumer related/M2M/custom ORM hooks need end-to-end acceptance under the
  selected alias and object-permission policy.

No global monkeypatch, compatibility stub, or live deployment is part of this
package candidate.

Tests and build
---------------

.. code-block:: console

    python -m pip install -r requirements-dev.txt
    python -m pytest
    python -m build --no-isolation

Tests use synthetic models and isolated in-memory SQLite databases. They are not
production workload, PostgreSQL concurrency, or full EDW application acceptance.
