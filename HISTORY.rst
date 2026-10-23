.. :changelog:

History
-------

0.3.0a1 (unreleased)
~~~~~~~~~~~~~~~~~~~

* InfLabs candidate for Python 3.12, Django 5.2 and DRF 3.18.
* Preserve ordered lookup fields, POST lookup retention and EDW validation hook.
* Validate lookup types, scope per-item validation, reject duplicate targets.
* Atomic bulk ORM units with per-object update/delete permissions; reject
  second-alias execute/executemany in the current synchronous context.
* Bulk delete is disabled unless the consumer explicitly overrides its policy.
* Lazy public exports and build metadata without Django imports; modern tests.
* Older Django/DRF 2 runtimes are not supported by this candidate.

0.2.1 (2015-04-26)
~~~~~~~~~~~~~~~~~~

* Fixed a bug which allowed to submit data for update to serializer
  without update field.
  See `#34 <https://github.com/miki725/django-rest-framework-bulk/issues/34>`_.
* Removed support for Django1.8 with DRF2.x

0.2 (2015-02-09)
~~~~~~~~~~~~~~~~

* Added DRF3 support. Please note that DRF2 is still supported.
  Now we support both DRF2 and DRF3!
* Fixed an issue when using viewsets, single resource update was not working due
  to ``get_object()`` overwrite in viewset.

0.1.4 (2015-02-01)
~~~~~~~~~~~~~~~~~~

* Added base model viewset.
* Fixed installation issues.
  See `#18 <https://github.com/miki725/django-rest-framework-bulk/pull/18>`_,
  `#22 <https://github.com/miki725/django-rest-framework-bulk/pull/22>`_.

0.1.3 (2014-06-11)
~~~~~~~~~~~~~~~~~~

* Fixed bug how ``post_save()`` was called in bulk create.

0.1.2 (2014-04-15)
~~~~~~~~~~~~~~~~~~

* Fixed bug how ``pre_save()`` was called in bulk update.
* Fixed bug of unable to mixins by importing directly ``from rest_framework_bulk import <mixin>``.
  See `#5 <https://github.com/miki725/django-rest-framework-bulk/pull/5>`_ for more info.

0.1.1 (2014-01-20)
~~~~~~~~~~~~~~~~~~

* Fixed installation bug with setuptools.

0.1 (2014-01-18)
~~~~~~~~~~~~~~~~

* First release on PyPI.
