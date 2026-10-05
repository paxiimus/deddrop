from django.conf import settings
from rest_framework.throttling import SimpleRateThrottle

# Keyed off IP in the cache (Redis) only — never written to the database, and
# never tied to an Identity. Applies regardless of auth status: minting a new
# throwaway keypair is free, so an identity-only throttle would be pointless
# for abuse prevention. This is deliberately the *only* place an IP touches
# the system at all, and it's never persisted.


class _IPRateThrottle(SimpleRateThrottle):
    def get_cache_key(self, request, view):
        ident = self.get_ident(request)
        return self.cache_format % {"scope": self.scope, "ident": ident}


class DropCreateThrottle(_IPRateThrottle):
    scope = "drop_create"


class ReportThrottle(_IPRateThrottle):
    scope = "report"


class PhotoUploadThrottle(_IPRateThrottle):
    scope = "photo_upload"


class MessageThrottle(_IPRateThrottle):
    scope = "message_create"


class DropPasswordThrottle(_IPRateThrottle):
    """Every password check on a drop — retrieve() via the X-Drop-Password
    header, collect() via the request body — previously had zero rate
    limiting. For a protection explicitly documented as casual, not
    high-security, that still meaningfully undermines it: nothing stopped
    hammering either endpoint as a straightforward password-guessing
    oracle. Scoped specifically to requests that actually include a
    password attempt (see DropViewSet.get_throttles) rather than applied
    to retrieve() universally — Identity.tsx polls every 30 seconds,
    potentially across several tracked drops, and none of that polling
    ever sends a password at all, so it's untouched by this."""

    scope = "drop_password_attempt"


class PrivateAccessThrottle(_IPRateThrottle):
    """The private-access code (see deaddrop/private_access.py) is a
    single, shared secret gating the entire instance — the same
    brute-force-oracle concern as DropPasswordThrottle above, just for a
    much higher-stakes surface (the whole app, not one drop). Tighter
    than that one (10/hour, not 30) since a legitimate user should only
    ever need this once, maybe twice for a mistyped long code — nothing
    like the repeated, correct-password-on-an-active-thread pattern that
    shaped DropPasswordThrottle's looser rate."""

    scope = "private_access_attempt"


class DropReadThrottle(_IPRateThrottle):
    """A baseline for every drop action that previously had no throttle at
    all — list (the public discovery endpoint), a plain retrieve with no
    password attempt, update, destroy, and collect with no password. Loose
    enough to never bother real use (300/hour comfortably covers active
    30-second polling across several drops at once) while still giving a
    public, anonymous, internet-facing instance some baseline protection
    against a single IP hammering it — which it previously had none of."""

    scope = "drop_read"


class AnonymousDropCapThrottle(_IPRateThrottle):
    """Caps anonymous (no-identity) drop creation per IP, over a window
    that matches the platform's own retention ceiling — so a given IP's
    standing usage is bounded the same way a single drop's lifetime is.
    Both the count and the window read from settings (private deployments
    can configure both; public is fixed at 1 per 14 days — see
    settings.py). Identity holders bypass this entirely — see the
    active-drops check in CreateDropSerializer instead, which is DB-backed
    (identities are already persisted) rather than this IP/cache-only
    mechanism.

    DRF's "num/period" rate-string format only expresses single time units
    (second/minute/hour/day) — there's no built-in way to say "N days" as
    a string, so the window is computed directly here instead.
    THROTTLE_RATES still needs a placeholder entry for this scope
    (get_rate() requires one to exist), but its value is never used.
    """

    scope = "drop_create_anon_cap"

    def parse_rate(self, rate):
        return (settings.MAX_ANON_DROPS_PER_IP, settings.MAX_DROP_LIFETIME_HOURS * 3600)
