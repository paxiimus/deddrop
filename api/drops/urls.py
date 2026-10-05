from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import DropPhotoUploadView, DropVideoUploadView, DropViewSet, MessageListCreateView

router = DefaultRouter()
router.register("", DropViewSet, basename="drop")

urlpatterns = [
    path("<uuid:drop_pk>/messages/", MessageListCreateView.as_view(), name="drop-messages"),
    path("<uuid:drop_pk>/photos/", DropPhotoUploadView.as_view(), name="drop-photo-upload"),
    path("<uuid:drop_pk>/videos/", DropVideoUploadView.as_view(), name="drop-video-upload"),
    path("", include(router.urls)),
]
