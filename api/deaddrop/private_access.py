import hashlib

from django.conf import settings
from django.core import signing
from django.utils.crypto import constant_time_compare

COOKIE_NAME = "private_access"
# 30 days — long-lived on purpose. This is meant to be "enter once, stay
# in" for a small, known, trusted audience, not a short session token
# they'd need to keep re-entering.
MAX_AGE_SECONDS = 30 * 24 * 60 * 60
# Signed cookies are readable (base64), not encrypted — so the cookie
# carries a digest of the code, never the code itself, under a salt of its
# own so no other signed value in the app can be swapped in.
SALT = "deaddrop.private-access"


def _digest(code):
    return hashlib.sha256(code.encode()).hexdigest()


def is_required():
    return bool(settings.PRIVATE_ACCESS_CODE)


def is_granted(request):
    """True if this request already carries a valid, still-current grant.

    Checks the LIVE settings.PRIVATE_ACCESS_CODE on every call, not just
    that the cookie is validly signed — the cookie encodes the code it was
    granted *with*, not a bare "access: yes" flag, specifically so that
    rotating the code (the operator's own chosen way to revoke access —
    there's no per-user token here, by design, for a single shared
    secret) correctly, automatically invalidates every existing cookie
    the moment the code changes. A bare boolean flag wouldn't do that; it
    would stay valid until the cookie's own 30-day expiry regardless of
    rotation.
    """
    cookie = request.COOKIES.get(COOKIE_NAME)
    if not cookie:
        return False
    try:
        granted_with = signing.loads(cookie, salt=SALT, max_age=MAX_AGE_SECONDS)
    except signing.BadSignature:
        # Covers both tampering and expiry — SignatureExpired is a
        # subclass of BadSignature.
        return False
    return constant_time_compare(granted_with, _digest(settings.PRIVATE_ACCESS_CODE))


def set_cookie(response, code):
    response.set_cookie(
        COOKIE_NAME,
        signing.dumps(_digest(code), salt=SALT),
        max_age=MAX_AGE_SECONDS,
        httponly=True,  # the frontend never needs to read this itself — the browser just needs to keep sending it
        samesite="Lax",
        secure=not settings.DEBUG,  # matches how a real deployment (DEBUG=False) is always HTTPS; local dev over plain HTTP still works
    )
