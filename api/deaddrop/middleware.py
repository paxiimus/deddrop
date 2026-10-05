import logging

from django.core.cache import cache
from django.http import JsonResponse

from . import private_access

logger = logging.getLogger(__name__)

# Deliberately just this one path, not shared with CoolingMiddleware's own
# EXEMPT_PREFIXES below (which also lists /admin/ and /static/) — this
# gate's own reasoning for what to exempt is different, see
# PrivateAccessMiddleware's docstring.
PRIVATE_ACCESS_EXEMPT_PREFIXES = ("/api/status/",)

# Anything starting with one of these is let through even while cooling.
# /api/status/ so a client can find out cooling is active at all; /admin/
# so the operator isn't locked out of the one place they can end the
# cooling period early or extend it; /static/ for the same reason as
# /admin/, not a separate one — the admin page's own CSS/JS is served
# from here (WhiteNoiseMiddleware), and without this exemption the admin
# path being "exempt" wouldn't actually matter: the page would load with
# every stylesheet and script rejected, rendering as broken, unstyled
# HTML right when the operator most needs it to work.
EXEMPT_PREFIXES = ("/api/status/", "/admin/", "/static/")

CACHE_KEY = "site_cooling_status"
CACHE_TTL_SECONDS = 5  # see the docstring below for why this matters


def cooling_info():
    """Cached SiteStatus snapshot, shared by CoolingMiddleware and
    StatusView (which clients poll every 30s and which is exempt from
    cooling — previously an uncached DB query per call, exactly during
    the surges cooling exists for). Raises on cache/DB failure; callers
    decide how to fail."""
    info = cache.get(CACHE_KEY)
    if info is None:
        from drops.models import SiteStatus  # deferred: avoids a circular import at app load

        s = SiteStatus.get()
        info = {
            "cooling": s.is_cooling,
            "message": s.cooling_message if s.is_cooling else "",
            "until": s.cooling_until.isoformat() if s.is_cooling else None,
        }
        cache.set(CACHE_KEY, info, timeout=CACHE_TTL_SECONDS)
    return info


class PrivateAccessMiddleware:
    """The whole-instance access gate for a private deployment. Not the
    same concern as DEPLOYMENT_MODE, which only ever changed a feature set
    (media, configurable limits) — flipping it to "private" never
    actually restricted who could reach the instance at all. This is what
    does that: every non-exempt request gets rejected with a 403 unless it
    carries a valid, still-current grant (see private_access.py) — a
    middleware, same reasoning as CoolingMiddleware, so a future new
    endpoint can't be added without this check by accident.

    Deliberately does NOT exempt /admin/ or /static/ the way
    CoolingMiddleware does — there, the operator needed a working escape
    hatch to manage cooling without knowing it was active yet. Here,
    there's no equivalent need: the operator already knows the private
    access code (they set it), so requiring it before /admin/ too is
    genuine defense in depth, not a self-lockout risk. Only /api/status/
    is exempt — the one bootstrapping endpoint that must always be
    reachable so the frontend can even find out a code is required at
    all, the same reason it's exempt from CoolingMiddleware.

    No caching layer the way CoolingMiddleware has one — this only ever
    reads settings.PRIVATE_ACCESS_CODE (already in memory, resolved once
    at process start) and verifies a signed cookie, both pure in-memory
    operations with no database or cache round-trip involved either way.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if private_access.is_required() and not any(request.path.startswith(p) for p in PRIVATE_ACCESS_EXEMPT_PREFIXES):
            if not private_access.is_granted(request):
                return JsonResponse(
                    {"private_access_required": True, "detail": "This instance requires an access code."},
                    status=403,
                )
        return self.get_response(request)


class CoolingMiddleware:
    """The actual enforcement behind the kill switch — SiteStatus in
    drops/models.py is just a database row an operator can toggle;
    without this, nothing would ever act on it. Every non-exempt request
    gets rejected with a plain 503 while cooling is active, regardless of
    which view it would otherwise have reached — a middleware, not a
    per-view permission class, specifically so a future new endpoint
    can't accidentally be added without this check, the way a permission
    class would require remembering to attach it every time.

    Cached with a short TTL rather than querying SiteStatus on every
    single request: this middleware runs before every request this
    instance receives, including exactly the surge of traffic it exists
    to protect against, and adding a database hit to every single one of
    those requests would be working against the reason this exists at
    all. A few seconds' delay before a newly-set cooling period actually
    takes effect everywhere is a completely reasonable trade for that.

    Fails open, deliberately, not closed. Django's Redis cache backend
    raises on a connection failure by default, and this runs on every
    single request with nothing else standing between it and the rest of
    the app — a transient Redis blip (a container restart, a brief
    network hiccup, both completely normal operational events) would
    otherwise take the entire site down, for every request, for a reason
    that has nothing to do with actual load. That's backwards for a
    feature whose whole purpose is protecting availability. The "fail
    closed" pattern used elsewhere in this project (DEBUG, ALLOWED_HOSTS)
    is for security settings, where more-permissive is the dangerous
    direction — this isn't a security boundary, and failing closed here
    would just mean an unrelated infrastructure hiccup becomes a new,
    self-inflicted outage.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not any(request.path.startswith(p) for p in EXEMPT_PREFIXES):
            try:
                info = cooling_info()
                if info["cooling"]:
                    return JsonResponse(
                        {"cooling": True, "message": info["message"], "detail": "This instance is temporarily paused."},
                        status=503,
                    )
            except Exception:
                logger.exception("CoolingMiddleware couldn't check cooling status — failing open, request allowed through")
        return self.get_response(request)


ADMIN_LOGIN_ATTEMPTS_PER_HOUR = 10


class AdminLoginThrottleMiddleware:
    """Django's admin login has no rate limiting of its own, and /admin/
    sits on the public API domain. Caps login POSTs per IP. For more,
    restrict /admin/ to known IPs at the proxy (see Caddyfile.example)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == "POST" and request.path.startswith("/admin/login/"):
            from rest_framework.throttling import BaseThrottle

            key = f"admin_login:{BaseThrottle().get_ident(request)}"
            attempts = cache.get(key, 0)
            if attempts >= ADMIN_LOGIN_ATTEMPTS_PER_HOUR:
                return JsonResponse({"detail": "Too many login attempts. Try again later."}, status=429)
            cache.set(key, attempts + 1, 3600)
        return self.get_response(request)
