import uuid

from django.db import models


class Identity(models.Model):
    """A pseudonymous identity: just a public key. No email, no username, no
    password, no PII of any kind. There is no signup step — the first request
    signed with a given key auto-registers it (see identities/auth.py)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    public_key = models.CharField(max_length=64, unique=True)  # hex-encoded ed25519 pubkey
    display_name = models.CharField(max_length=50, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def is_authenticated(self):
        # Duck-types like Django's User for DRF's IsAuthenticated permission.
        return True

    def __str__(self):
        return self.display_name or self.public_key[:8]
