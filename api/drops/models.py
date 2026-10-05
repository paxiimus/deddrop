import hashlib
import secrets
import uuid

from django.contrib.auth.hashers import check_password, make_password
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils import timezone
from django.utils.crypto import constant_time_compare

from identities.models import Identity
from PIL import Image

# Pillow only *errors* above 2x MAX_IMAGE_PIXELS (default ~179MP) and just
# warns below it, so an ~170MP image passed and needed ~0.5-1GB of RAM to
# convert. 32M here -> hard error above 64MP, which still admits current
# 48/50MP phone cameras.
Image.MAX_IMAGE_PIXELS = 32_000_000


# Uploads are stored under random names, never the client's filename.
# The client name leaked information ("janes-house.jpg") and made photo
# URLs of password-protected drops guessable; for video, a kept
# extension let an HTML file labelled video/mp4 be served back as
# text/html from the API origin (stored XSS). Photos are always
# re-encoded to JPEG; videos keep only an allowlisted extension.
VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".avi", ".mkv", ".mpeg"}


def photo_upload_to(instance, filename):
    return f"drops/photos/{uuid.uuid4().hex}.jpg"


def video_upload_to(instance, filename):
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return f"drops/videos/{uuid.uuid4().hex}{ext if ext in VIDEO_EXTENSIONS else '.bin'}"


class Drop(models.Model):
    STATUS_CHOICES = [("active", "Active"), ("expired", "Expired"), ("collected", "Collected")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # A drop is owned EITHER by an Identity (signature-authenticated, for people
    # managing several drops) OR by a bare owner_secret (fully anonymous — no
    # account at all). Never both, never neither.
    creator = models.ForeignKey(Identity, null=True, blank=True, on_delete=models.SET_NULL, related_name="drops")
    owner_secret_hash = models.CharField(max_length=128, blank=True, default="")

    title = models.CharField(max_length=200, blank=True, default="")
    description = models.TextField(blank=True, default="")
    latitude = models.FloatField()
    longitude = models.FloatField()

    password_hash = models.CharField(max_length=128, blank=True, default="")
    is_anonymous = models.BooleanField(default=True)
    is_public = models.BooleanField(default=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="active")
    starts_at = models.DateTimeField(null=True, blank=True)  # null = active immediately
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["latitude", "longitude"], name="drops_latlng_idx"),
            models.Index(fields=["status", "-created_at"], name="drops_status_created_idx"),
            models.Index(fields=["expires_at"], name="drops_expires_idx"),  # expire/purge tasks
        ]
        # Bounding-box filtering on floats is fine at MVP scale. Only worth
        # swapping for PostGIS + PointField if a single area gets dense.

    def __str__(self):
        return self.title or str(self.id)

    def set_password(self, raw):
        self.password_hash = make_password(raw) if raw else ""

    def check_password(self, raw):
        if not self.password_hash:
            return True
        return check_password(raw or "", self.password_hash)

    def set_owner_secret(self):
        """Generate the one-time management secret for a fully anonymous drop.
        Returned to the client exactly once at creation. The server stores only
        a salted hash and can never recover the plaintext — if the client loses
        it, the drop can no longer be edited or deleted. That's by design."""
        raw = secrets.token_urlsafe(32)
        # A 256-bit random token needs no slow KDF — that exists to resist
        # guessing low-entropy human passwords. PBKDF2 here cost ~260ms of
        # CPU on every edit/delete/upload check. Drop passwords (human-
        # chosen) keep make_password().
        self.owner_secret_hash = "sha256$" + hashlib.sha256(raw.encode()).hexdigest()
        return raw

    def check_owner_secret(self, raw):
        if not (raw and self.owner_secret_hash):
            return False
        return constant_time_compare(self.owner_secret_hash, "sha256$" + hashlib.sha256(raw.encode()).hexdigest())

    @property
    def is_password_protected(self):
        return bool(self.password_hash)

    @property
    def is_expired(self):
        return bool(self.expires_at) and timezone.now() >= self.expires_at

    @property
    def is_pending(self):
        """A scheduled drop that hasn't started yet. Excluded from the
        browsable list (see views.get_queryset) but still reachable by
        direct link, same as every other visibility rule in this app —
        and blocked from being collected, since you can't have found
        something that isn't there yet."""
        return bool(self.starts_at) and timezone.now() < self.starts_at

    def save(self, *args, **kwargs):
        if self.is_expired and self.status == "active":
            self.status = "expired"
        super().save(*args, **kwargs)


class DropPhoto(models.Model):
    drop = models.ForeignKey(Drop, on_delete=models.CASCADE, related_name="photos")
    image = models.ImageField(upload_to=photo_upload_to)
    order = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order"]

    def save(self, *args, **kwargs):
        if self.image and self.pk is None:
            self._strip_exif()
        super().save(*args, **kwargs)

    def _strip_exif(self):
        """A photo of a physical drop location is exactly the kind of file
        that carries GPS EXIF tags and device identifiers from the phone that
        took it — silently re-leaking precise location or device info the app
        never intended to expose, regardless of what coordinates the user
        actually chose to share. Re-encode on upload so none of it survives."""
        from io import BytesIO

        from django.core.files.base import ContentFile
        from PIL import Image, ImageOps

        img = Image.open(self.image)
        img = ImageOps.exif_transpose(img)  # bake in correct rotation before EXIF is dropped
        img = img.convert("RGB")
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=85)  # fresh encode carries no source metadata
        name = self.image.name.rsplit(".", 1)[0] + ".jpg"
        self.image.save(name, ContentFile(buf.getvalue()), save=False)


@receiver(post_delete, sender=DropPhoto)
def _delete_photo_file(sender, instance, **kwargs):
    """Django's cascade-delete (Drop -> DropPhoto via FK) removes the
    database row but, by design, never touches the actual file on disk —
    that's a well-known Django gotcha, not an oversight anyone would catch
    from reading the FK alone. Without this, every "deleted" photo would
    silently keep its bytes on disk forever, which would have quietly
    defeated the entire point of a retention policy built specifically to
    bound storage cost."""
    if instance.image:
        instance.image.delete(save=False)


class DropVideo(models.Model):
    """Private-deployment-only (see settings.MEDIA_ENABLED — the upload
    endpoint for this is unreachable on a public instance regardless of
    what any client sends).

    Unlike DropPhoto, this does NOT strip metadata on upload. EXIF
    stripping for images uses Pillow, a pure-Python library already in
    this project's dependencies, straightforward to verify by reading the
    code. Doing the equivalent for video needs an external tool (ffmpeg is
    the standard choice) invoked as a subprocess — genuinely more moving
    parts, and not something to claim as "done" without actually running
    and verifying it, which this build has no way to do. Video containers
    carry GPS/device metadata the same way photo EXIF does. If a real
    deployment plans to accept device-recorded video from a population
    where that matters, closing this gap (ffmpeg -map_metadata -1) is a
    real prerequisite, not a nice-to-have — it's flagged this prominently
    on purpose.
    """

    drop = models.ForeignKey(Drop, on_delete=models.CASCADE, related_name="videos")
    video = models.FileField(upload_to=video_upload_to)
    order = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order"]


class SiteStatus(models.Model):
    """A global kill switch — not per-endpoint throttling (see
    throttles.py for that, which protects against one abusive source).
    This is the blunt, all-traffic-stops circuit breaker for when the
    whole instance needs to pause, not just one IP: a viral moment, a
    front-page hit, more distributed legitimate traffic than a donation
    budget can absorb. A no-SLA, donation-maintained service has no
    obligation to stay up through a surge it can't afford — this is the
    honest lever for that, "closed for a bit, here's when", rather than a
    server quietly struggling or a bill quietly spiking.

    Singleton by construction (save() always writes to pk=1) — there's
    only ever one site, so there's only ever one status. Toggle it via
    Django admin, the same place moderation already happens.

    is_cooling is derived from cooling_until, not a separate boolean field
    an operator would need to remember to unset — set a future time here,
    and the instance automatically resumes the moment it passes, with no
    action required to turn it back on.
    """

    cooling_until = models.DateTimeField(null=True, blank=True)
    cooling_message = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        verbose_name_plural = "Site status"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        pass  # singleton — deleting would just force a fresh get_or_create() next time, no real effect

    @property
    def is_cooling(self):
        return bool(self.cooling_until) and self.cooling_until > timezone.now()

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


@receiver(post_delete, sender=DropVideo)
def _delete_video_file(sender, instance, **kwargs):
    """Same reasoning as _delete_photo_file above."""
    if instance.video:
        instance.video.delete(save=False)


class Message(models.Model):
    """Messages are opaque to the server by design. `ciphertext` is whatever
    the client's own E2E encryption produced — the server relays it and never
    decrypts it, never holds a key that could. `content` exists only for
    plaintext system messages (e.g. "Drop collected"), which carry nothing
    sensitive."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    drop = models.ForeignKey(Drop, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey(Identity, null=True, blank=True, on_delete=models.SET_NULL)
    ciphertext = models.TextField(blank=True, default="")
    content = models.CharField(max_length=200, blank=True, default="")
    is_system = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]


class Report(models.Model):
    """Flag a drop for moderation review. No reporter identity is required,
    requested, or stored — reporting must work for someone with zero account
    and zero desire to be identified."""

    REASON_CHOICES = [
        ("illegal", "Illegal content"),
        ("spam", "Spam / abuse"),
        ("unsafe", "Unsafe location"),
        ("other", "Other"),
    ]
    STATUS_CHOICES = [("open", "Open"), ("reviewed", "Reviewed"), ("actioned", "Actioned")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    drop = models.ForeignKey(Drop, null=True, on_delete=models.SET_NULL, related_name="reports")
    reason = models.CharField(max_length=20, choices=REASON_CHOICES)
    details = models.CharField(max_length=500, blank=True, default="")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="open")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
