import os
from pathlib import Path

from corsheaders.defaults import default_headers

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-insecure-key-change-in-production")
# Fails closed like everything else in this file — "False" as the
# fallback, not "True". Every other setting here fails to the safe
# direction regardless of what any env var says; this one previously
# didn't, which was a real inconsistency, not a stylistic one:
# CORS_ALLOW_ALL_ORIGINS = DEBUG below means a missing DEBUG env var would
# have also silently disabled CORS restrictions entirely, on top of
# enabling Django's own verbose debug pages (which leak SECRET_KEY,
# installed apps, file paths, and query details). .env.example already
# sets this explicitly, but that's not the same guarantee as the fallback
# itself being safe — an operator setting env vars directly through a
# deployment platform's dashboard rather than a literal .env file could
# easily miss it.
DEBUG = os.environ.get("DEBUG", "False").lower() == "true"
# The dev fallback above is public (it's in this repo). It only applies
# when DJANGO_SECRET_KEY is absent entirely, and must never reach a real
# deployment — anyone holding it can forge anything Django signs.
if not DEBUG and SECRET_KEY in ("", "dev-insecure-key-change-in-production"):
    from django.core.exceptions import ImproperlyConfigured

    raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set when DEBUG=False (make secrets).")
ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "").split(",")
# Deliberately fails closed, not to "*" — an operator who forgets to set
# this should get a working local/healthcheck instance and a broken
# public one (loud, obvious, immediately noticed) rather than a silently
# wide-open one (the actual production anti-pattern this guards against).
# The Docker healthcheck (see docker-compose.yml) runs *inside* the
# container and reaches itself via localhost — a completely separate
# concern from which public hostname the app should accept from the
# internet. Without this, locking ALLOWED_HOSTS down to a real domain (the
# correct thing to do in production) makes Django reject the healthcheck's
# own request with a 400 (DisallowedHost), which Docker Compose then reads
# as "unhealthy" forever — and since celery-worker/celery-beat depend on
# api being healthy, they'd never auto-start either.
for _host in ("localhost", "127.0.0.1"):
    if _host not in ALLOWED_HOSTS and "*" not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(_host)

INSTALLED_APPS = [
    "django.contrib.admin",  # staff-only moderation queue — never exposed to end users
    "django.contrib.auth",  # backs the admin login only; end users never touch this
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "corsheaders",
    "identities",
    "drops",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    # Same CORS-ordering reasoning as CoolingMiddleware below applies here
    # too — placed after CorsMiddleware so a short-circuited 403 still
    # gets correct CORS headers on the way back out. Placed BEFORE
    # CoolingMiddleware specifically: "you're not authorized to be here"
    # is the more fundamental rejection, and should take precedence over
    # "the site's temporarily paused" if a private deployment somehow
    # hits both at once. Doesn't share CoolingMiddleware's need to run
    # after SessionMiddleware either — it verifies a signed cookie
    # directly via django.core.signing, not Django's session framework,
    # so there's no ordering constraint forcing it later in this list.
    "deaddrop.middleware.PrivateAccessMiddleware",
    # Right after CORS, before everything else — rejects a cooling
    # request as early as possible, before session handling, static-file
    # serving, or any other work runs for a request that's about to be
    # turned away regardless. Placed *after* CorsMiddleware specifically
    # so its short-circuited 503 still gets correct CORS headers applied
    # on the way back out — Django's middleware wraps outer-to-inner for
    # requests but inner-to-outer for responses, so CorsMiddleware being
    # first in the list means it still wraps around whatever this
    # middleware returns, even when this one never calls further down
    # the chain at all.
    "deaddrop.middleware.CoolingMiddleware",
    "deaddrop.middleware.AdminLoginThrottleMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",  # serves /static/ from Gunicorn — no separate nginx needed
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "deaddrop.urls"
WSGI_APPLICATION = "deaddrop.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]

DATABASES = {
    "default": {
        "ENGINE": os.environ.get("DB_ENGINE", "django.db.backends.sqlite3"),
        "NAME": os.environ.get("DB_NAME", BASE_DIR / "db.sqlite3"),
        "USER": os.environ.get("DB_USER", ""),
        "PASSWORD": os.environ.get("DB_PASSWORD", ""),
        "HOST": os.environ.get("DB_HOST", ""),
        "PORT": os.environ.get("DB_PORT", ""),
        # Reuse connections instead of opening a new Postgres connection
        # (auth handshake + backend process) on every request.
        "CONN_MAX_AGE": 60,
        "CONN_HEALTH_CHECKS": True,
    }
}

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["identities.auth.SignatureAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    # Every throttle is keyed on client IP. With NUM_PROXIES unset, DRF
    # uses the whole X-Forwarded-For header as the key — safe only if the
    # proxy overwrites it. Pinning it takes the client's IP from the
    # proxy's own entry instead. 1 = one reverse proxy (Caddy, as
    # documented); 0 = gunicorn exposed directly.
    "NUM_PROXIES": int(os.environ.get("NUM_PROXIES", 1)),
    # No browsable HTML API outside dev — JSON is all the frontend uses.
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"]
    + (["rest_framework.renderers.BrowsableAPIRenderer"] if DEBUG else []),
    # Deliberately no pagination. Every list endpoint (drops, messages) is
    # consumed by frontend code that expects a bare array — with pagination
    # configured, DRF wraps that in {count, next, previous, results}
    # instead, and nothing anywhere in this app ever unwraps .results or
    # renders "next page" UI. That mismatch is invisible to static review
    # (it's DRF's own framework behavior, applied automatically, not
    # visible in any individual view or serializer) and only breaks once a
    # real server actually responds — every .filter()/.map() on a list
    # response throws "X.filter is not a function" the moment it does.
    # This app's realistic scale (one area's nearby drops, one person's own
    # drops, one thread's messages) doesn't need pagination anyway.
    "DEFAULT_THROTTLE_RATES": {
        "drop_create": "20/hour",  # per-IP, cache-only — see drops/throttles.py
        "report": "30/hour",
        "photo_upload": "30/hour",
        "message_create": "60/hour",
        "drop_password_attempt": "30/hour",  # per-IP — see DropPasswordThrottle
        "drop_read": "300/hour",  # per-IP baseline for everything else — see DropReadThrottle
        "identity_me": "100/hour",  # per-IP — see identities/throttles.py:IdentityThrottle
        "private_access_attempt": "10/hour",  # per-IP — see drops/throttles.py:PrivateAccessThrottle
        "drop_create_anon_cap": "1/day",  # placeholder only — AnonymousDropCapThrottle overrides the actual window to MAX_DROP_LIFETIME_HOURS (14 days by default, configurable on private deployments)
    },
}

CORS_ALLOW_ALL_ORIGINS = DEBUG
# Both localhost and 127.0.0.1 variants — a browser treats them as
# different origins even though they're the same machine, and either is
# a normal way to reach the Vite dev/preview server locally.
CORS_ALLOWED_ORIGINS = os.environ.get(
    "CORS_ORIGINS",
    "http://localhost:5173,http://localhost:4173,http://127.0.0.1:5173,http://127.0.0.1:4173",
).split(",")
# Needed for the private-access cookie (see deaddrop/private_access.py) to
# actually work at all — the frontend and API run on different origins in
# any real deployment, and fetch() doesn't send cookies cross-origin
# without credentials: "include" on the client paired with this setting
# on the server; without both, the browser silently drops the cookie.
# Confirmed directly against django-cors-headers' own source: this does
# NOT downgrade CORS_ALLOW_ALL_ORIGINS to a literal wildcard once this is
# True — it echoes back the specific requesting origin instead, in both
# the ALLOW_ALL (dev) and explicit-allowlist (production) cases, which is
# what the CORS spec itself requires once credentials are involved.
CORS_ALLOW_CREDENTIALS = True
# Browsers CORS-preflight custom headers; this only matters for the web build.
CORS_ALLOW_HEADERS = list(default_headers) + [
    "x-public-key",
    "x-signature",
    "x-timestamp",
    "x-owner-secret",
    "x-drop-password",
]

# One flag governs the whole deployment tier, not a pile of independent
# switches an operator has to remember to keep consistent with each other.
# "public" is the safe, restrictive default — an unrecognised value (a typo
# in .env) fails closed to "public" rather than silently granting more than
# intended.
#
# Media (photos, video) is a public/private axis for real reasons, not a
# cost-saving one: the public instance is anonymous, internet-facing, and
# has no content-scanning infrastructure wired up (nor can one be — that
# needs an operator's own provider credentials, e.g. Thorn Safer, Google
# CSAI Match, Microsoft PhotoDNA). Requiring an identity per-upload was
# tried and deliberately removed — a throwaway keypair costs nothing and
# verifies nothing, so it added real complexity and real data collection
# for essentially no protection against a determined bad actor. The actual
# fix is not offering the risky surface to an anonymous, unaccountable
# population at all. A private deployment is a different, legitimate case:
# the whole population is vetted and accountable to whoever is running it.
DEPLOYMENT_MODE = os.environ.get("DEPLOYMENT_MODE", "public")
if DEPLOYMENT_MODE not in ("public", "private"):
    DEPLOYMENT_MODE = "public"
MEDIA_ENABLED = DEPLOYMENT_MODE == "private"

# Every one of these is a specific, load-bearing promise about what a
# private deployment operator can actually control — "whatever the org
# wants to pay to store", "whatever the org's policy requires". They need
# to be able to act on that without editing source, which — until now —
# they couldn't. On "public" these stay hard-fixed at the values this
# project has always shipped with, regardless of what any env var says: an
# operator can't accidentally weaken the public instance's retention or
# cap promises via a stray or copy-pasted .env value. Only "private"
# reads the environment at all.
if DEPLOYMENT_MODE == "private":
    MAX_DROP_LIFETIME_HOURS = int(os.environ.get("MAX_DROP_LIFETIME_HOURS", 24 * 14))
    MAX_ACTIVE_DROPS_PER_IDENTITY = int(os.environ.get("MAX_ACTIVE_DROPS_PER_IDENTITY", 30))
    MAX_ANON_DROPS_PER_IP = int(os.environ.get("MAX_ANON_DROPS_PER_IP", 1))
    MAX_PHOTOS_PER_DROP = int(os.environ.get("MAX_PHOTOS_PER_DROP", 2))
    MAX_PHOTO_SIZE_BYTES = int(os.environ.get("MAX_PHOTO_SIZE_MB", 5)) * 1024 * 1024
    MAX_VIDEOS_PER_DROP = int(os.environ.get("MAX_VIDEOS_PER_DROP", 1))
    MAX_VIDEO_SIZE_BYTES = int(os.environ.get("MAX_VIDEO_SIZE_MB", 100)) * 1024 * 1024
    MAX_MESSAGE_LENGTH = int(os.environ.get("MAX_MESSAGE_LENGTH", 20000))
    MAX_MESSAGES_PER_DROP = int(os.environ.get("MAX_MESSAGES_PER_DROP", 100))
else:
    MAX_DROP_LIFETIME_HOURS = 24 * 14
    MAX_ACTIVE_DROPS_PER_IDENTITY = 30
    MAX_ANON_DROPS_PER_IP = 1
    MAX_PHOTOS_PER_DROP = 2  # moot in practice — MEDIA_ENABLED is False on public anyway
    MAX_PHOTO_SIZE_BYTES = 5 * 1024 * 1024
    MAX_VIDEOS_PER_DROP = 1
    MAX_VIDEO_SIZE_BYTES = 100 * 1024 * 1024
    MAX_MESSAGE_LENGTH = 20000
    MAX_MESSAGES_PER_DROP = 100

# Deliberately independent of DEPLOYMENT_MODE, not ANDed with it — gates on
# nothing but whether this is actually set, regardless of "public" or
# "private". DEPLOYMENT_MODE controls a feature set (media, configurable
# limits); this controls who can reach the instance at all, which is a
# genuinely separate concern — an operator running a private beta of the
# public feature set, gating it before opening it up fully, is exactly as
# valid a case as gating an actual private deployment. Blank (the default)
# means no gate at all, matching how this project has always shipped.
PRIVATE_ACCESS_CODE = os.environ.get("PRIVATE_ACCESS_CODE", "")

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# A real, explicit cache — not Django's default. Without this, Django
# falls back to LocMemCache: per-process, in-memory, never shared across
# workers. This app runs multiple Gunicorn workers (GUNICORN_WORKERS in
# docker-compose.yml) — every throttle in throttles.py relies on
# SimpleRateThrottle's cache-based counting, and without a shared cache,
# each worker keeps its own separate, never-synchronized count. A single
# IP could get up to (worker count) times the stated rate, silently,
# with no error anywhere — the throttle classes themselves are correct,
# this was purely a missing infrastructure setting. Separate Redis DB
# index (1, not CELERY_BROKER_URL's 0) so cache keys and Celery's own
# broker/queue data never share a namespace.
#
# Not Django's raw RedisCache — deaddrop.cache.FailOpenRedisCache, a
# thin wrapper that fails open instead of raising if Redis itself is
# ever briefly unreachable. See that file for why: this cache backs
# every throttle here, not just this project's own cooling middleware.
CACHES = {
    "default": {
        "BACKEND": "deaddrop.cache.FailOpenRedisCache",
        "LOCATION": os.environ.get("REDIS_CACHE_URL", "redis://redis:6379/1"),
    }
}

CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_BEAT_SCHEDULE = {
    "expire-drops": {"task": "drops.tasks.expire_drops", "schedule": 300},  # every 5 min
    "purge-old-drops": {"task": "drops.tasks.purge_old_drops", "schedule": 3600},  # every hour
    "purge-orphan-identities": {"task": "drops.tasks.purge_orphan_identities", "schedule": 86400},  # daily
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_TZ = True

if not DEBUG:
    # Behind Caddy, Django sees plain HTTP from the proxy. Without this
    # header it treats every request as insecure, so the admin login's
    # CSRF Origin check (https://… vs expected http://…) rejects every
    # login. Secure cookies keep the admin session off plain HTTP.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

# Django's default logging only prints to the console when DEBUG=True and
# otherwise emails ADMINS (unset here) — so production 500 tracebacks
# went nowhere. This sends warnings and errors to stdout/stderr, which
# `make logs-api` shows.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {"django": {"handlers": ["console"], "level": "WARNING", "propagate": False}},
}
