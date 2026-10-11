import hashlib
import hmac

from django.conf import settings
from rest_framework.throttling import BaseThrottle

_KEY = None


def ip_token(request, *scope):
    """Keyed hash of the client IP (plus any scope parts) for cache keys, so
    Redis never holds a raw IP — or a readable drop-to-IP link. It's a
    pseudonym, not anonymity: someone holding both Redis and SECRET_KEY could
    still test candidate IPs against it."""
    global _KEY
    if _KEY is None:
        _KEY = hmac.new(settings.SECRET_KEY.encode(), b"deaddrop-ip-token", hashlib.sha256).digest()
    msg = "|".join([BaseThrottle().get_ident(request) or "", *map(str, scope)])
    return hmac.new(_KEY, msg.encode(), hashlib.sha256).hexdigest()[:32]
