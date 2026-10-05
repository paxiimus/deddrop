from rest_framework.permissions import BasePermission


class CanManageDrop(BasePermission):
    """Edit/delete a drop if you're the Identity that created it, or if you
    hold its one-time owner secret (fully anonymous drops with no account)."""

    def has_object_permission(self, request, view, obj):
        if obj.creator_id:
            return getattr(request.user, "id", None) == obj.creator_id
        return obj.check_owner_secret(request.headers.get("X-Owner-Secret"))
