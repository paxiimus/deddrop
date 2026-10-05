from rest_framework import generics, permissions

from .serializers import IdentitySerializer
from .throttles import IdentityThrottle


class MeView(generics.RetrieveUpdateAPIView):
    """GET signed with your key returns (and auto-creates) your identity.
    PATCH lets you set an optional display name — never required."""

    serializer_class = IdentitySerializer
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [IdentityThrottle]

    def get_object(self):
        return self.request.user
