import logging

from django.core.cache.backends.base import DEFAULT_TIMEOUT
from django.core.cache.backends.redis import RedisCache

logger = logging.getLogger(__name__)


class FailOpenRedisCache(RedisCache):
    """Django's own Redis cache backend, wrapped to fail open — return the
    default / no-op — rather than raise, if Redis itself is unreachable.

    Verified directly against DRF's own source: SimpleRateThrottle's
    allow_request() does exactly `self.cache.get(self.key, [])` then, on
    success, `self.cache.set(self.key, self.history, self.duration)` — the
    same two methods CoolingMiddleware calls, on the same "default" cache
    DRF uses unless a throttle class explicitly overrides it (none in
    throttles.py do). One fix here transparently protects both, with
    nothing to change in throttles.py or middleware.py themselves.

    Without this: a transient Redis outage — a container restart, a
    brief network hiccup, both completely normal operational events —
    would make CoolingMiddleware crash on every single request, and
    separately make every throttled endpoint in throttles.py (drop
    creation among them — core functionality, not a peripheral feature)
    raise instead of just failing to rate-limit. The entire site would go
    down for a reason that has nothing to do with actual traffic, which
    is backwards for infrastructure whose purpose is protecting
    availability. Failing open means the worst case during a Redis outage
    is "rate limits and cooling are temporarily not enforced" — a real
    but far smaller and more honest cost than the whole app crashing.
    """

    def get(self, key, default=None, version=None):
        try:
            return super().get(key, default, version)
        except Exception:
            logger.exception("Cache GET failed for key %r — failing open", key)
            return default

    def set(self, key, value, timeout=DEFAULT_TIMEOUT, version=None):
        try:
            super().set(key, value, timeout, version)
        except Exception:
            logger.exception("Cache SET failed for key %r — failing open", key)
