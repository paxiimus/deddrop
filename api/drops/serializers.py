from datetime import timedelta
from math import asin, cos, radians, sin, sqrt

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from identities.models import Identity

from .models import Drop, DropPhoto, DropVideo, Message, Report

# MAX_DROP_LIFETIME_HOURS and MAX_ACTIVE_DROPS_PER_IDENTITY live in
# settings.py now, not here — private deployments can configure both (see
# settings.py for why public can't). Referenced below as
# settings.MAX_DROP_LIFETIME_HOURS / settings.MAX_ACTIVE_DROPS_PER_IDENTITY.


class DropPhotoSerializer(serializers.ModelSerializer):
    class Meta:
        model = DropPhoto
        fields = ["id", "image", "order"]


class DropVideoSerializer(serializers.ModelSerializer):
    class Meta:
        model = DropVideo
        fields = ["id", "video", "order"]


class MessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Message
        fields = ["id", "drop", "ciphertext", "content", "is_system", "created_at"]
        read_only_fields = ["id", "drop", "content", "is_system", "created_at"]

    def validate_ciphertext(self, value):
        # ciphertext is a TextField with no max_length at the DB level —
        # without this, combined with MessageThrottle, there was no limit
        # at all on how much data a single message could carry. Limit
        # itself lives in settings.MAX_MESSAGE_LENGTH — configurable on
        # private deployments, fixed on public, same pattern as everything
        # else in settings.py.
        if len(value) > settings.MAX_MESSAGE_LENGTH:
            raise serializers.ValidationError(f"Message too large (max {settings.MAX_MESSAGE_LENGTH} encrypted characters).")
        return value


class DropSerializer(serializers.ModelSerializer):
    is_password_protected = serializers.BooleanField(read_only=True)
    photos = serializers.SerializerMethodField()
    videos = serializers.SerializerMethodField()
    distance_m = serializers.SerializerMethodField()
    latitude = serializers.SerializerMethodField()
    longitude = serializers.SerializerMethodField()

    class Meta:
        model = Drop
        fields = [
            "id", "title", "description", "latitude", "longitude", "photos", "videos",
            "is_password_protected", "is_anonymous", "is_public", "status",
            "starts_at", "expires_at", "created_at", "distance_m",
        ]

    def _is_owner(self, obj):
        if self.context.get("owner"):
            return True
        request = self.context.get("request")
        if not request:
            return False
        if obj.creator_id:
            return getattr(request.user, "id", None) == obj.creator_id
        return obj.check_owner_secret(request.headers.get("X-Owner-Secret"))

    def _coords_visible(self, obj):
        # A scheduled drop's location stays hidden until it starts, except
        # from its owner — previously a direct GET by ID revealed it early.
        if obj.is_pending and not self._is_owner(obj):
            return False
        # A locked drop shows up in listings (title, description, the fact
        # that it exists) but never its coordinates until the password has
        # been verified server-side — set via context in views.retrieve().
        # Returning coordinates unconditionally here would make the password
        # theatre: anyone hitting the list endpoint would see exact lat/lng
        # for every "protected" drop regardless of whether they know it.
        return (not obj.is_password_protected) or self.context.get("password_verified", False)

    def get_latitude(self, obj):
        return obj.latitude if self._coords_visible(obj) else None

    def get_longitude(self, obj):
        return obj.longitude if self._coords_visible(obj) else None

    def get_photos(self, obj):
        # Same reasoning as coordinates: a photo of the storefront or street
        # sign at a drop can identify the location at least as effectively as
        # raw lat/lng — arguably more so, since a person can recognise a
        # place from a photo without needing to interpret coordinates at all.
        # This field was previously ungated, which quietly leaked exactly
        # what the coordinate fix above was written to prevent.
        if not self._coords_visible(obj):
            return []
        return DropPhotoSerializer(obj.photos.all(), many=True, context=self.context).data

    def get_videos(self, obj):
        if not self._coords_visible(obj):
            return []
        return DropVideoSerializer(obj.videos.all(), many=True, context=self.context).data

    def get_distance_m(self, obj):
        if not self._coords_visible(obj):
            return None
        request = self.context.get("request")
        if not request:
            return None
        lat, lng = request.query_params.get("lat"), request.query_params.get("lng")
        if lat and lng:
            try:
                return round(_haversine_m(float(lat), float(lng), obj.latitude, obj.longitude))
            except ValueError:
                # Malformed lat/lng shouldn't break the whole response over
                # what's just a best-effort display refinement — a plain
                # ValueError here isn't one of the types DRF's exception
                # handler recognises (only APIException subclasses are), so
                # left unguarded this became an unhandled 500 instead of
                # just silently not showing a distance.
                return None
        return None


class CreateDropSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False, allow_blank=True, max_length=128)
    # TextField has no DB length limit; without this cap a single drop
    # could carry ~2.5MB (Django's request-body ceiling) into every list
    # response it appears in.
    description = serializers.CharField(required=False, allow_blank=True, max_length=2000)
    expires_hours = serializers.IntegerField(
        write_only=True, required=False, min_value=1, max_value=settings.MAX_DROP_LIFETIME_HOURS
    )
    starts_in_hours = serializers.IntegerField(write_only=True, required=False, min_value=1, max_value=24 * 90)
    latitude = serializers.FloatField(min_value=-90, max_value=90)
    longitude = serializers.FloatField(min_value=-180, max_value=180)

    class Meta:
        model = Drop
        fields = [
            "title", "description", "latitude", "longitude", "is_public",
            "password", "expires_hours", "starts_in_hours",
        ]

    def validate(self, attrs):
        # expires_at is capped from CREATION time, not from starts_at — a
        # drop scheduled to start further out than its own expiry would be
        # purged before it ever became visible at all.
        starts_in_hours = attrs.get("starts_in_hours")
        expires_hours = attrs.get("expires_hours") or settings.MAX_DROP_LIFETIME_HOURS
        if starts_in_hours and starts_in_hours >= expires_hours:
            raise serializers.ValidationError("A drop must start before it expires — reduce the schedule or extend the expiry.")
        return attrs

    def create(self, validated_data):
        password = validated_data.pop("password", "")
        # Omitting expires_hours no longer means "permanent" — it means
        # "give me the maximum", which is the same ceiling every drop is
        # bound by regardless. There's no path to a drop that outlives it.
        expires_hours = validated_data.pop("expires_hours", None) or settings.MAX_DROP_LIFETIME_HOURS
        starts_in_hours = validated_data.pop("starts_in_hours", None)

        identity = self.context["request"].user
        identity = identity if isinstance(identity, Identity) else None

        with transaction.atomic():
            if identity is not None:
                # select_for_update() locks this identity's row for the
                # rest of this transaction — without it, two near-
                # simultaneous create requests from the same identity,
                # both arriving when it's one below the cap, could both
                # read that same, still-under-the-limit count before
                # either commits its own new row, letting the identity
                # end up with more active drops than the stated maximum.
                # A second, concurrent request for the same identity
                # blocks here until the first transaction completes,
                # then correctly sees the freshly-committed count.
                Identity.objects.select_for_update().get(pk=identity.pk)
                if identity.drops.filter(status="active").count() >= settings.MAX_ACTIVE_DROPS_PER_IDENTITY:
                    raise serializers.ValidationError(f"Maximum {settings.MAX_ACTIVE_DROPS_PER_IDENTITY} active drops per identity.")

            drop = Drop(creator=identity, is_anonymous=identity is None, **validated_data)
            if password:
                drop.set_password(password)
            drop.expires_at = timezone.now() + timedelta(hours=expires_hours)
            if starts_in_hours:
                drop.starts_at = timezone.now() + timedelta(hours=starts_in_hours)

            # Only fully anonymous drops (no Identity) get an owner secret — an
            # Identity-authenticated drop is already manageable via signature.
            self.owner_secret = drop.set_owner_secret() if identity is None else None
            drop.save()
        return drop


class UpdateDropSerializer(serializers.ModelSerializer):
    """Deliberately separate from DropSerializer (whose latitude/longitude
    are read-only SerializerMethodFields, and which has no password/expiry/
    schedule inputs at all) and from CreateDropSerializer (which always
    creates a new row). A location isn't editable here — reusing a drop's
    coordinates means creating a new drop there, not moving an existing one.

    Omit a field entirely to leave it unchanged. For password specifically,
    an empty value clears it (removes protection). For expires_hours, an
    empty value resets it to the deployment's maximum — there's no "clear
    to permanent" anymore, since permanent isn't a concept at all.
    For starts_in_hours, an empty value clears it (starts immediately).
    """

    password = serializers.CharField(write_only=True, required=False, allow_blank=True, max_length=128)
    # TextField has no DB length limit; without this cap a single drop
    # could carry ~2.5MB (Django's request-body ceiling) into every list
    # response it appears in.
    description = serializers.CharField(required=False, allow_blank=True, max_length=2000)
    expires_hours = serializers.IntegerField(
        write_only=True, required=False, allow_null=True, min_value=1, max_value=settings.MAX_DROP_LIFETIME_HOURS
    )
    starts_in_hours = serializers.IntegerField(write_only=True, required=False, allow_null=True, min_value=1, max_value=24 * 90)

    class Meta:
        model = Drop
        fields = ["title", "description", "is_public", "password", "expires_hours", "starts_in_hours"]

    def validate(self, attrs):
        # Same reasoning as CreateDropSerializer.validate. This only checks
        # the case where both are being changed in the same request — if
        # only one is touched, the other keeps whatever the instance
        # already had, which was already validated correctly when it was
        # set. The gap that's NOT covered: changing only starts_in_hours to
        # something that outlives the instance's existing, unrelated
        # expires_at. Low-severity if hit (a very short or zero active
        # window, not a security or data issue) and not worth the added
        # complexity of reconstructing "what will the final state be" for
        # a partial update just to close it.
        starts_in_hours = attrs.get("starts_in_hours")
        expires_hours = attrs.get("expires_hours")
        if starts_in_hours and expires_hours and starts_in_hours >= expires_hours:
            raise serializers.ValidationError("A drop must start before it expires — reduce the schedule or extend the expiry.")
        return attrs

    def update(self, instance, validated_data):
        password = validated_data.pop("password", None)
        expires_set = "expires_hours" in validated_data
        expires_hours = validated_data.pop("expires_hours", None)
        starts_set = "starts_in_hours" in validated_data
        starts_in_hours = validated_data.pop("starts_in_hours", None)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        if password is not None:
            instance.set_password(password)  # blank clears it — set_password already treats "" as "no password"
        if expires_set:
            # No "clear to permanent" path — blank/null resets to the
            # maximum instead, same as omitting it entirely at creation.
            # Capped at the lifetime ceiling measured from creation, not
            # from now — repeated PATCHes previously kept a drop alive
            # forever, breaking the retention promise.
            ceiling = instance.created_at + timedelta(hours=settings.MAX_DROP_LIFETIME_HOURS)
            instance.expires_at = min(
                timezone.now() + timedelta(hours=expires_hours or settings.MAX_DROP_LIFETIME_HOURS), ceiling
            )
            # Drop.save() only ever flips active -> expired, never back.
            # Without this, extending an already-expired drop's deadline
            # would leave status stuck at "expired" forever — permanently
            # invisible to the list view (which filters on status="active")
            # regardless of how far into the future the new expiry is.
            if instance.status == "expired" and not instance.is_expired:
                instance.status = "active"
        if starts_set:
            instance.starts_at = timezone.now() + timedelta(hours=starts_in_hours) if starts_in_hours else None

        if instance.starts_at and instance.expires_at and instance.starts_at >= instance.expires_at:
            raise serializers.ValidationError("A drop must start before it expires.")
        instance.save()
        return instance


class ReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = Report
        fields = ["id", "drop", "reason", "details", "created_at"]
        read_only_fields = ["id", "created_at"]


def _haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000
    p1, p2 = radians(lat1), radians(lat2)
    dp, dl = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 2 * r * asin(sqrt(a))
