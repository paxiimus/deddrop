from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.static import serve

from drops.views import StatusView

urlpatterns = [
    path("admin/", admin.site.urls),  # staff moderation only
    path("api/status/", StatusView.as_view(), name="site-status"),
    path("api/identities/", include("identities.urls")),
    path("api/drops/", include("drops.urls")),
]

# Media served by Django directly, in every mode. Not via
# django.conf.urls.static.static(): that helper returns [] whenever
# DEBUG=False, so on a real private deployment uploads succeeded and then
# 404'd. serve() itself works regardless of DEBUG and guards against path
# traversal (safe_join). Media still sits behind PrivateAccessMiddleware
# and CoolingMiddleware like any other path. Throughput is fine for a
# bounded private instance; a dedicated file server is the upgrade path.
urlpatterns += [
    re_path(r"^media/(?P<path>.+)$", serve, {"document_root": settings.MEDIA_ROOT}),
]
