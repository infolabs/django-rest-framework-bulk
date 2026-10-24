from django.urls import include, path
from rest_framework_bulk.routes import BulkRouter

from .views import SimpleViewSet

router = BulkRouter()
router.register('simple', SimpleViewSet, 'simple')
urlpatterns = [path('api/', include((router.urls, 'api'), namespace='api'))]
