from celery import shared_task
from django.utils import timezone


@shared_task
def expire_drops():
    """Mark expired drops. Runs on a schedule via celery beat."""
    from .models import Drop

    count = Drop.objects.filter(status="active", expires_at__lte=timezone.now()).update(status="expired")
    return f"Expired {count} drops"


@shared_task
def purge_old_drops():
    """Hard-deletes drops past their expiry — not just flipping status like
    expire_drops does. Every drop's expires_at is capped at the deployment's
    own MAX_DROP_LIFETIME_HOURS from creation (14 days by default, operator-
    configurable on private deployments — see settings.py) — there's no more
    "permanent" drop concept — so this is the actual data-retention
    enforcement: once that ceiling passes, the row, its photos (files
    included, via the post_delete signal on DropPhoto — Django's
    cascade-delete alone would only remove the database rows, never the
    underlying files on disk), and its messages are genuinely gone, not
    just hidden behind a status flag. Reports survive (Report.drop is
    SET_NULL, not CASCADE), so a moderation record persists even after the
    reported content is purged.
    """
    from .models import Drop

    qs = Drop.objects.filter(expires_at__lte=timezone.now())
    drop_count = qs.count()
    qs.delete()
    return f"Purged {drop_count} drops (and their cascaded photos/messages)"


@shared_task
def purge_orphan_identities():
    """Deletes identities with no remaining drops once they're older than
    the drop lifetime. Drops are hard-deleted at expiry, but identity rows
    (public key + timestamp) previously stayed forever. Nothing else
    references a dropless identity except Message.sender, which is
    SET_NULL. A returning user's next signed request simply re-registers
    the key (with a new id and no display name)."""
    from datetime import timedelta

    from django.conf import settings

    from identities.models import Identity

    cutoff = timezone.now() - timedelta(hours=settings.MAX_DROP_LIFETIME_HOURS)
    count, _ = Identity.objects.filter(created_at__lt=cutoff, drops__isnull=True).delete()
    return f"Purged {count} orphan identities"
