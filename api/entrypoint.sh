#!/bin/bash
set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log() { echo -e "${GREEN}[entrypoint]${NC} $1"; }
warn() { echo -e "${YELLOW}[entrypoint]${NC} $1"; }
err() { echo -e "${RED}[entrypoint]${NC} $1"; }

if [ "$DB_ENGINE" = "django.db.backends.postgresql" ]; then
    log "Waiting for PostgreSQL at ${DB_HOST}:${DB_PORT:-5432}..."
    retries=0
    until python -c "
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(2)
s.connect(('${DB_HOST}', ${DB_PORT:-5432}))
s.close()
" 2>/dev/null; do
        retries=$((retries + 1))
        if [ $retries -ge 30 ]; then
            err "PostgreSQL not available after 30 attempts. Exiting."
            exit 1
        fi
        warn "PostgreSQL not ready (attempt ${retries}/30). Retrying in 2s..."
        sleep 2
    done
    log "PostgreSQL is ready."
fi

if [ -n "$CELERY_BROKER_URL" ]; then
    # Python's own urllib, not sed regex — the previous regex assumed
    # "redis://host:port" specifically and silently mis-extracted the
    # whole URL as the hostname the moment a password was present
    # ("redis://:password@host:port" — the char right after "redis://" is
    # itself a colon, which the old pattern couldn't handle). Not
    # currently triggered by anything in this project's own
    # docker-compose.yml (no Redis auth configured there), but a real gap
    # for anyone hardening Redis with a password, which urlparse handles
    # correctly regardless.
    REDIS_HOST=$(python -c "from urllib.parse import urlparse; print(urlparse('${CELERY_BROKER_URL}').hostname or '')")
    REDIS_PORT=$(python -c "from urllib.parse import urlparse; print(urlparse('${CELERY_BROKER_URL}').port or 6379)")
    log "Checking Redis at ${REDIS_HOST}:${REDIS_PORT}..."
    retries=0
    until python -c "
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(2)
s.connect(('${REDIS_HOST}', ${REDIS_PORT}))
s.close()
" 2>/dev/null; do
        retries=$((retries + 1))
        if [ $retries -ge 15 ]; then
            warn "Redis not available. Continuing without it."
            break
        fi
        sleep 2
    done
fi

PROCESS_TYPE=${PROCESS_TYPE:-web}

# Migrations, static files, and the admin superuser are owned by exactly ONE
# process type. api, celery-worker, and celery-beat all share this same
# entrypoint in docker-compose — if every container ran migrate and
# collectstatic on its own startup, three processes would race the same
# database and the same shared static volume simultaneously on every boot.
# That's a real first-run failure mode (a corrupted static manifest, or a
# migration race), not a hypothetical one, so this is gated deliberately.
if [ "$PROCESS_TYPE" = "web" ] || [ "$PROCESS_TYPE" = "migrate" ]; then
    log "Running database migrations..."
    python manage.py migrate --noinput
    log "Migrations complete."

    log "Collecting static files..."
    python manage.py collectstatic --noinput --clear
    log "Static files collected."

    # This creates a Django auth.User for the ADMIN/MODERATION panel only.
    # It has nothing to do with end-user Identities — end users never see
    # this.
    #
    # DJANGO_SUPERUSER_USERNAME/EMAIL/PASSWORD are read via os.environ
    # inside the Python code below, not bash-interpolated into the string
    # literal — a password containing a single quote or backslash (an
    # entirely reasonable choice for a strong one) would otherwise break
    # the generated Python source directly. Confirmed empirically: the
    # bash-interpolated version produced a genuine SyntaxError, and set -e
    # at the top of this script meant that error killed the entire
    # entrypoint — Gunicorn/Celery would never start at all over a
    # perfectly good password. Reading via os.environ sidesteps the
    # problem entirely: the value never touches the Python source text,
    # so its content can't affect how that text parses.
    if [ -n "$DJANGO_SUPERUSER_USERNAME" ] && [ -n "$DJANGO_SUPERUSER_PASSWORD" ]; then
        log "Ensuring admin superuser '${DJANGO_SUPERUSER_USERNAME}' exists..."
        python manage.py shell -c "
import os
from django.contrib.auth import get_user_model
User = get_user_model()
username = os.environ['DJANGO_SUPERUSER_USERNAME']
email = os.environ.get('DJANGO_SUPERUSER_EMAIL', 'admin@example.com')
password = os.environ['DJANGO_SUPERUSER_PASSWORD']
if not User.objects.filter(username=username).exists():
    User.objects.create_superuser(username, email, password)
    print('Superuser created.')
else:
    print('Superuser already exists.')
"
    fi
fi

mkdir -p /app/media/drops/photos

case "$PROCESS_TYPE" in
    web)
        log "Starting Gunicorn (web)..."
        # Default bumped from 120s to 300s specifically for video uploads
        # (MEDIA_ENABLED, private deployments only, up to 100MB) — 120s
        # requires ~6.7Mbps sustained upload throughput to complete a
        # 100MB file, which isn't a safe assumption on mobile connections,
        # and video documenting a physical drop location is exactly a
        # mobile-upload use case. Adjust via GUNICORN_TIMEOUT if needed.
        exec gunicorn deaddrop.wsgi:application \
            --bind 0.0.0.0:${PORT:-8000} \
            --workers ${GUNICORN_WORKERS:-3} \
            --worker-class gthread \
            --threads ${GUNICORN_THREADS:-2} \
            --timeout ${GUNICORN_TIMEOUT:-300} \
            --max-requests 1000 \
            --max-requests-jitter 50 \
            --access-logfile - \
            --error-logfile -
        ;;
    celery-worker)
        log "Starting Celery worker..."
        exec celery -A deaddrop worker --loglevel=info --concurrency=${CELERY_CONCURRENCY:-2}
        ;;
    celery-beat)
        log "Starting Celery beat..."
        exec celery -A deaddrop beat --loglevel=info --schedule=/tmp/celerybeat-schedule
        ;;
    migrate)
        log "Migration-only mode. Done."
        exit 0
        ;;
    *)
        err "Unknown PROCESS_TYPE: ${PROCESS_TYPE}"
        err "Use: web | celery-worker | celery-beat | migrate"
        exit 1
        ;;
esac
