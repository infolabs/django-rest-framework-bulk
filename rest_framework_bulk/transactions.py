"""Single-database bulk units; deliberately not distributed transactions."""
from contextlib import ExitStack, contextmanager

from django.core.exceptions import ImproperlyConfigured
from django.db import DEFAULT_DB_ALIAS, connections, router, transaction


def write_database(queryset=None, serializer=None):
    child = getattr(serializer, 'child', serializer)
    model = getattr(queryset, 'model', None) or getattr(getattr(child, 'Meta', None), 'model', None)
    alias = router.db_for_write(model) if model is not None else DEFAULT_DB_ALIAS
    explicit = getattr(queryset, '_db', None)
    if explicit is not None and explicit != alias:
        raise ImproperlyConfigured('Bulk queryset and model write router must use the same database.')
    return alias


def check_instance_database(instance, alias):
    if instance._state.db != alias or router.db_for_write(type(instance), instance=instance) != alias:
        raise ImproperlyConfigured('Bulk targets must use the selected write database.')


@contextmanager
def bulk_atomic(alias):
    """One alias, synchronous ORM/cursor.execute/executemany only.

    Not a sandbox: callproc, raw cursors, new connections/threads/async, custom
    non-Django drivers, external effects and post-commit callbacks are outside
    this guarantee. Applications must not perform them inside bulk hooks.
    An on_commit callback failure happens after commit and cannot roll it back.
    """
    refused = []

    def reject_other_database(execute, sql, params, many, context):
        refused.append(True)
        raise ImproperlyConfigured('Cross-database queries are not allowed inside a bulk operation.')

    with ExitStack() as stack:
        for other in connections:
            if other != alias:
                stack.enter_context(connections[other].execute_wrapper(reject_other_database))
        with transaction.atomic(using=alias):
            yield
            # A custom hook may catch the guard exception. Still poison the
            # complete unit so an attempted second-alias query cannot commit it.
            if refused:
                raise ImproperlyConfigured('Bulk operation attempted a cross-database query.')
