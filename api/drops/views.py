import uuid
from math import cos, isfinite, radians

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from PIL import UnidentifiedImageError
from PIL.Image import DecompressionBombError
from rest_framework import generics, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, Throttled, ValidationError
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response

from deaddrop import private_access
from deaddrop.middleware import cooling_info
from identities.models import Identity

from .models import Drop, Message
from .permissions import CanManageDrop
from .serializers import (
    CreateDropSerializer,
    DropPhotoSerializer,
    DropSerializer,
    DropVideoSerializer,
    MessageSerializer,
    ReportSerializer,
    UpdateDropSerializer,
)
from .throttles import (
    AnonymousDropCapThrottle,
    DropCreateThrottle,
    DropPasswordThrottle,
    DropReadThrottle,
    MessageThrottle,
    PhotoUploadThrottle,
    PrivateAccessThrottle,
    ReportThrottle,
)

# MAX_PHOTOS_PER_DROP, MAX_PHOTO_SIZE_BYTES, MAX_VIDEOS_PER_DROP, and
# MAX_VIDEO_SIZE_BYTES live in settings.py now — private deployments can
# configure all four (see settings.py for why public can't). Referenced
# below as settings.MAX_PHOTOS_PER_DROP etc.


MAX_LIST_RADIUS_M = 50000
MAX_LIST_RESULTS = 500
MAX_BATCH_IDS = 50
MESSAGE_PASSWORD_FAILS_PER_HOUR = 30


class DropViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.AllowAny]

    def get_serializer_class(self):
        if self.action == "create":
            return CreateDropSerializer
        if self.action in ("update", "partial_update"):
            return UpdateDropSerializer
        return DropSerializer

    def get_permissions(self):
        if self.action in ("update", "partial_update", "destroy"):
            return [CanManageDrop()]
        return super().get_permissions()

    def get_throttles(self):
        if self.action == "create":
            throttles = [DropCreateThrottle()]
            # The per-identity cap (30 active drops, checked in
            # CreateDropSerializer) is DB-backed and doesn't need a
            # throttle — an identity is already a persisted, accountable
            # thing. Anonymous creation has no such record, so it gets this
            # IP/cache-based cap instead, bypassed entirely once signed in
            # with an identity.
            if not isinstance(self.request.user, Identity):
                throttles.append(AnonymousDropCapThrottle())
            return throttles
        if self.action == "report":
            return [ReportThrottle()]
        # A password attempt specifically — retrieve() via the header,
        # collect() via the body — gets the tighter throttle regardless of
        # whether it's correct or not; a correct-but-repeated password on
        # an active conversation (every DropDetail action re-sends it as
        # part of its own refresh) is indistinguishable from a guess at
        # this layer, which is exactly why the rate is loose enough (30/hr)
        # to absorb normal active use while still meaningfully slowing a
        # single IP's brute-force attempts. Anything else previously fell
        # through to super().get_throttles(), which resolves to no
        # throttle at all — the public discovery list and a plain,
        # passwordless retrieve had zero rate limiting whatsoever.
        if self.action == "retrieve" and self.request.headers.get("X-Drop-Password"):
            return [DropPasswordThrottle()]
        if self.action == "collect" and self.request.data.get("password"):
            return [DropPasswordThrottle()]
        return [DropReadThrottle()]

    def get_queryset(self):
        # No longer a bulk status="expired" update here. It used to run on
        # every single call to this method — which DRF invokes for list,
        # retrieve, update, destroy, and collect alike — meaning a plain,
        # read-only GET (including the 30-second polling Identity.tsx does
        # per tracked drop) triggered a database write every time, even
        # when nothing matched. That worked directly against the reason
        # throttling and the kill switch exist: unnecessary write load on
        # exactly the requests that should be cheap reads, worst during
        # the traffic a surge would bring. expire_drops (Celery, every 5
        # minutes) is the real enforcement now; the status field shown to
        # clients can lag actual expiry by up to that window, which is a
        # cosmetic display staleness, not a correctness gap — collect()
        # below checks is_expired directly rather than trusting this
        # field's freshness, specifically so that staleness window can
        # never let someone collect a drop that's actually already
        # expired, only make an already-expired one visibly linger in a
        # list for a few extra minutes.
        qs = Drop.objects.prefetch_related("photos", "videos")

        if self.action != "list":
            # Direct-link / QR access, delete, and collect all need to keep
            # working after a drop expires or is collected — status="active"
            # only makes sense as a filter on the browsable list, never here.
            # (This was previously applied unconditionally and broke viewing
            # a drop immediately after marking it collected — a 404 on the
            # very next request after the happy path's main action.)
            return qs

        if self.request.query_params.get("mine") and isinstance(self.request.user, Identity):
            # "mine" is a management view, not discovery — an owner needs to
            # see everything they have: active, expired, collected, and
            # scheduled drops that haven't started yet. Applying the same
            # active/started-only filters used for public browsing here
            # would mean a drop vanishes from its own owner's "my drops"
            # list the moment it expires or gets collected, and a scheduled
            # drop would never be visible to manage before it even starts.
            return qs.filter(creator=self.request.user)

        if ids := self.request.query_params.get("ids"):
            # Batch lookup by ID, for a device checking on the anonymous
            # drops it holds owner secrets for — one request instead of one
            # per drop. Exposes nothing a per-ID retrieve doesn't: the same
            # serializer hides locked/pending coordinates the same way.
            try:
                id_list = [uuid.UUID(i) for i in ids.split(",")[:MAX_BATCH_IDS]]
            except ValueError:
                raise ValidationError("ids must be comma-separated drop IDs.")
            return qs.filter(pk__in=id_list)

        qs = qs.filter(status="active")
        # A scheduled drop that hasn't started yet stays out of the
        # browsable list — still reachable by direct link, same rule as
        # everything else here — see Drop.is_pending.
        qs = qs.filter(Q(starts_at__isnull=True) | Q(starts_at__lte=timezone.now()))
        # Discovery is always bounded: lat/lng required, radius clamped,
        # result count capped. Previously no lat/lng (or radius=1e12, or
        # inf) returned every public drop on the instance in one
        # unpaginated response.
        try:
            lat = float(self.request.query_params["lat"])
            lng = float(self.request.query_params["lng"])
            radius = float(self.request.query_params.get("radius", MAX_LIST_RADIUS_M))
        except (KeyError, ValueError):
            raise ValidationError("lat and lng are required; lat, lng and radius must be numbers.")
        if not all(isfinite(v) for v in (lat, lng, radius)) or not (-90 <= lat <= 90 and -180 <= lng <= 180):
            raise ValidationError("lat/lng out of range.")
        radius = min(max(radius, 0), MAX_LIST_RADIUS_M)
        dlat = radius / 111320
        # A degree of longitude shrinks with cos(latitude) — without this
        # the box was too narrow east-west away from the equator (half
        # width at 60°N). Clamped so the poles don't divide by ~0.
        dlng = dlat / max(cos(radians(lat)), 0.01)
        qs = qs.filter(latitude__range=(lat - dlat, lat + dlat), longitude__range=(lng - dlng, lng + dlng))
        return qs.filter(is_public=True)[:MAX_LIST_RESULTS]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        drop = serializer.save()
        # password_verified=True here specifically — a freshly-created
        # drop's response goes back to its own creator, who inherently
        # knows everything about it including whatever password they just
        # set. Without this, _coords_visible() in DropSerializer would hide
        # the drop's own coordinates from the person who just typed them
        # in, since is_password_protected is already True the instant
        # set_password() runs above. Currently latent, not user-facing —
        # the frontend already has the coordinates in its own local state
        # and never reads them back from this response — but a real API
        # correctness bug regardless, for any other client or any future
        # frontend change that started relying on this response's fields.
        context = {**self.get_serializer_context(), "password_verified": True, "owner": True}
        data = DropSerializer(drop, context=context).data
        if getattr(serializer, "owner_secret", None):
            # Shown exactly once. There is no "forgot password" for this —
            # losing it means the drop can never be edited or deleted again.
            data["owner_secret"] = serializer.owner_secret
        return Response(data, status=status.HTTP_201_CREATED)

    def retrieve(self, request, *args, **kwargs):
        drop = self.get_object()
        # The password travels as a header, not a query parameter — a query
        # string ends up verbatim in access logs (?password=... is exactly
        # as bad as it looks), browser history, and any proxy that logs
        # request URLs. A header isn't captured by any of those by default.
        verified = drop.check_password(request.headers.get("X-Drop-Password"))
        if not verified:
            return Response(
                {"detail": "Password required.", "is_password_protected": True},
                status=status.HTTP_403_FORBIDDEN,
            )
        context = {**self.get_serializer_context(), "password_verified": True}
        return Response(DropSerializer(drop, context=context).data)

    @action(detail=True, methods=["post"])
    def collect(self, request, pk=None):
        with transaction.atomic():
            # A direct, minimal fetch — not self.get_object(), which goes
            # through get_queryset()'s select_related/prefetch_related for
            # data this action never touches, and which sidesteps needing
            # to reason through how select_for_update() interacts with
            # prefetch_related() at all. collect has no object-level
            # permission check regardless (see get_permissions — it's not
            # in the update/partial_update/destroy list), so nothing is
            # lost by not routing through self.get_object() here.
            #
            # select_for_update() locks this specific row for the rest of
            # this transaction — without it, two near-simultaneous collect
            # requests on the same physical drop could both read the same
            # "active" status before either writes "collected", both
            # passing every check below and both succeeding (each
            # creating its own "Drop has been collected" system message).
            drop = get_object_or_404(Drop.objects.select_for_update(), pk=pk)
            if drop.is_pending:
                return Response({"detail": "This drop hasn't started yet."}, status=status.HTTP_403_FORBIDDEN)
            if drop.status == "collected":
                return Response({"detail": "This drop has already been collected."}, status=status.HTTP_400_BAD_REQUEST)
            # is_expired directly, not just the stored status field — that
            # field is only updated by expire_drops on its own 5-minute
            # schedule now (see get_queryset's comment above), so a drop can
            # be genuinely, actually expired while status still says
            # "active" for up to that long. Checking the live property here
            # is what keeps that acceptable, cosmetic staleness from ever
            # becoming a real correctness gap — someone collecting a drop
            # that's actually already expired, just not yet marked as such.
            if drop.status == "expired" or drop.is_expired:
                return Response({"detail": "This drop has expired."}, status=status.HTTP_400_BAD_REQUEST)
            # A password-protected drop's id is still visible in public listings
            # (only its coordinates are hidden) — without this check, anyone
            # could mark it collected without ever having actually found it.
            if not drop.check_password(request.data.get("password")):
                return Response(
                    {"detail": "Password required.", "is_password_protected": True},
                    status=status.HTTP_403_FORBIDDEN,
                )
            drop.status = "collected"
            drop.save()
            Message.objects.create(drop=drop, content="Drop has been collected.", is_system=True)
        return Response({"status": "collected"})

    @action(detail=True, methods=["post"])
    def report(self, request, pk=None):
        serializer = ReportSerializer(data={**request.data, "drop": pk})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(status=status.HTTP_201_CREATED)


class MessageListCreateView(generics.ListCreateAPIView):
    """Message content is opaque ciphertext — the key travels in the share
    link's URL fragment and never reaches the server. Access to the thread
    itself follows the drop: open for unprotected drops, gated by
    X-Drop-Password for protected ones."""

    serializer_class = MessageSerializer
    permission_classes = [permissions.AllowAny]

    def _drop_or_deny(self, lock=False):
        """The thread inherits its drop's password. Contents were always
        safe (the key lives in the URL fragment, never sent here), but
        anyone with a drop ID could read thread metadata and post into it
        — including filling it to MAX_MESSAGES_PER_DROP and locking the
        real parties out."""
        qs = Drop.objects.select_for_update() if lock else Drop.objects
        drop = get_object_or_404(qs, pk=self.kwargs["drop_pk"])
        if drop.is_password_protected:
            fail_key = f"msg_pwfail:{self.get_throttles()[0].get_ident(self.request)}"
            fails = cache.get(fail_key, 0)
            if fails >= MESSAGE_PASSWORD_FAILS_PER_HOUR:
                raise Throttled(detail="Too many incorrect password attempts.")
            # Only failures count — a legitimate send costs three password
            # checks (post + reload of drop and thread), so counting every
            # attempt against the 30/hour password throttle would cap a
            # password-drop conversation at ~10 messages an hour.
            if not drop.check_password(self.request.headers.get("X-Drop-Password")):
                cache.set(fail_key, fails + 1, 3600)
                raise PermissionDenied("Password required.")
        return drop

    def get_queryset(self):
        return Message.objects.filter(drop=self._drop_or_deny())

    def get_throttles(self):
        # GET now throttled too: a full thread is up to ~2.7MB of
        # ciphertext, and unthrottled reads were free bandwidth to pull.
        return [MessageThrottle()] if self.request.method == "POST" else [DropReadThrottle()]

    def perform_create(self, serializer):
        # select_for_update + atomic: two sends at message 99 can't both
        # pass the count check before either commits.
        with transaction.atomic():
            drop = self._drop_or_deny(lock=True)
            if drop.messages.count() >= settings.MAX_MESSAGES_PER_DROP:
                raise ValidationError(f"Maximum {settings.MAX_MESSAGES_PER_DROP} messages per drop.")
            # Per-IP share of a thread, so one sender can't fill the whole
            # cap. Kept for the drop's maximum lifetime (the key's TTL).
            ip = self.get_throttles()[0].get_ident(self.request)
            share_key = f"msg_share:{drop.pk}:{ip}"
            if cache.get(share_key, 0) >= settings.MAX_MESSAGES_PER_DROP // 3:
                raise ValidationError("You've reached your message limit on this drop.")
            sender = self.request.user if isinstance(self.request.user, Identity) else None
            serializer.save(drop=drop, sender=sender)
            cache.set(share_key, cache.get(share_key, 0) + 1, settings.MAX_DROP_LIFETIME_HOURS * 3600)


class DropPhotoUploadView(generics.CreateAPIView):
    """Plain multipart/form-data upload from the web client's `FormData`.
    Gated entirely on settings.MEDIA_ENABLED — unreachable on a public
    instance regardless of what any client sends. See settings.py for why:
    identity-based accountability for uploads was tried here and removed —
    a throwaway keypair defeats it for free, so it bought real complexity
    and real data collection for no actual protection against a
    determined bad actor. Restricted to whoever can manage the drop
    (identity match or owner secret), same rule as edit/delete."""

    serializer_class = DropPhotoSerializer
    parser_classes = [MultiPartParser]
    permission_classes = [permissions.AllowAny]  # object-level check done manually below
    throttle_classes = [PhotoUploadThrottle]

    def create(self, request, *args, **kwargs):
        if not settings.MEDIA_ENABLED:
            raise PermissionDenied("Media uploads are disabled on this instance.")

        drop = get_object_or_404(Drop, pk=self.kwargs["drop_pk"])
        if not CanManageDrop().has_object_permission(request, self, drop):
            raise PermissionDenied("Only the drop's owner can add photos.")
        if drop.photos.count() >= settings.MAX_PHOTOS_PER_DROP:
            raise ValidationError(f"Maximum {settings.MAX_PHOTOS_PER_DROP} photos per drop.")

        image = request.FILES.get("image")
        if image and image.size > settings.MAX_PHOTO_SIZE_BYTES:
            raise ValidationError(f"Photo must be under {settings.MAX_PHOTO_SIZE_BYTES // (1024 * 1024)}MB.")

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            # serializer.save() is what actually triggers DropPhoto.save(),
            # which calls _strip_exif() — this is where Pillow actually
            # opens and decodes the file. MAX_PHOTO_SIZE_BYTES above only
            # checks compressed size on disk; a decompression bomb is by
            # definition a small file that decodes to an enormous pixel
            # count, so that check alone does nothing against this. Pillow
            # has its own default limit (Image.MAX_IMAGE_PIXELS) that
            # provides some baseline protection, but the exception it
            # raises isn't a type DRF's handler recognises — unguarded,
            # this became an unhandled 500 instead of a clean rejection,
            # same class of gap as the malformed lat/lng ValueErrors fixed
            # earlier this session. UnidentifiedImageError covers the
            # adjacent, non-malicious case of someone uploading a file
            # that just isn't a valid image at all.
            serializer.save(drop=drop)
        except (UnidentifiedImageError, DecompressionBombError):
            raise ValidationError("That doesn't look like a valid image file.")
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class DropVideoUploadView(generics.CreateAPIView):
    """Same shape as DropPhotoUploadView, same MEDIA_ENABLED gate. See
    DropVideo's docstring in models.py — metadata stripping isn't wired up
    for video yet, unlike photos."""

    # Best-effort, not a security boundary: this checks the Content-Type
    # header the client claims, which is exactly as spoofable as the
    # filename extension the frontend's accept="video/*" relies on — a
    # raw request can lie about this as easily as a file input can be
    # bypassed. What it actually stops is the casual/accidental case
    # (browsers report this correctly for real videos), not a deliberate
    # attacker. Genuine content verification needs to actually decode the
    # file — ffprobe or equivalent — which isn't wired up here, same gap
    # as the metadata-stripping note above.
    # Content type -> the only extension a stored video can get. The
    # client's own filename is discarded (see models.video_upload_to).
    ALLOWED_VIDEO_TYPES = {
        "video/mp4": ".mp4", "video/quicktime": ".mov", "video/webm": ".webm",
        "video/x-msvideo": ".avi", "video/x-matroska": ".mkv", "video/mpeg": ".mpeg",
    }

    serializer_class = DropVideoSerializer
    parser_classes = [MultiPartParser]
    permission_classes = [permissions.AllowAny]
    throttle_classes = [PhotoUploadThrottle]

    def create(self, request, *args, **kwargs):
        if not settings.MEDIA_ENABLED:
            raise PermissionDenied("Media uploads are disabled on this instance.")

        drop = get_object_or_404(Drop, pk=self.kwargs["drop_pk"])
        if not CanManageDrop().has_object_permission(request, self, drop):
            raise PermissionDenied("Only the drop's owner can add videos.")
        if drop.videos.count() >= settings.MAX_VIDEOS_PER_DROP:
            raise ValidationError(f"Maximum {settings.MAX_VIDEOS_PER_DROP} video(s) per drop.")

        video = request.FILES.get("video")
        if video and video.size > settings.MAX_VIDEO_SIZE_BYTES:
            raise ValidationError(f"Video must be under {settings.MAX_VIDEO_SIZE_BYTES // (1024 * 1024)}MB.")
        if video and video.content_type not in self.ALLOWED_VIDEO_TYPES:
            raise ValidationError("That doesn't look like a video file.")
        if video:
            video.name = "upload" + self.ALLOWED_VIDEO_TYPES[video.content_type]

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(drop=drop)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class StatusView(generics.GenericAPIView):
    """GET /api/status/ — the one endpoint CoolingMiddleware and
    PrivateAccessMiddleware always let through, so a client can tell
    whether the instance is cooling or gated at all without depending on
    some other request having already succeeded. GET stays unthrottled —
    this needs to stay reachable and cheap precisely during the situation
    (a real surge) where everything else is being turned away.

    POST submits a private-access code — kept on this same view/URL
    rather than a separate one, since both are fundamentally "what screen
    should the frontend show instead of the normal app" concerns, not two
    unrelated things that happened to land in the same file."""

    permission_classes = [permissions.AllowAny]

    def get_throttles(self):
        # Only the code-submission attempt gets throttled (see
        # PrivateAccessThrottle) — GET must stay exactly as unthrottled as
        # it's always been, for the same bootstrapping reason as before.
        return [PrivateAccessThrottle()] if self.request.method == "POST" else []

    def get(self, request):
        info = cooling_info()
        return Response({
            "cooling": info["cooling"],
            "cooling_until": info["until"],
            "message": info["message"],
            "private_access_required": private_access.is_required(),
            "private_access_granted": private_access.is_granted(request),
        })

    def post(self, request):
        if not private_access.is_required():
            raise ValidationError("This instance doesn't require an access code.")
        entered = request.data.get("code", "")
        if not constant_time_compare(entered, settings.PRIVATE_ACCESS_CODE):
            raise ValidationError("Incorrect access code.")
        response = Response({"granted": True})
        private_access.set_cookie(response, entered)
        return response
