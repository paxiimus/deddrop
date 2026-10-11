#!/usr/bin/env bash
# One-command deploy for a single Dead Drop instance. Built for a fresh
# Hetzner Cloud server (Debian 12/13 or Ubuntu 24.04); works on any VPS
# with those images. Run it from YOUR machine, not the server.
#
#   ./deploy.sh            First run: sets up the server, then deploys.
#                          Every run after: redeploys REF from deploy.env.
#   ./deploy.sh rollback   Restore the pre-deploy database dump and the
#                          previous release (undoes the last deploy).
#   ./deploy.sh backup     Download a fresh database dump to ./backups/
#   ./deploy.sh status     Container status and health, server and public.
#   ./deploy.sh logs       Follow the API logs.
#   ./deploy.sh ssh        Shell on the server, in the app directory.
#
# Configuration: deploy.env (copy deploy.env.example). Secrets are generated
# ON the server at first deploy and never leave it.

set -euo pipefail
cd "$(dirname "$0")"

# ---------------------------------------------------------------- config --
[[ -f deploy.env ]] || { echo "Copy deploy.env.example to deploy.env and fill it in first."; exit 1; }
# shellcheck disable=SC1091
source deploy.env

DEPLOY_USER="${DEPLOY_USER:-deploy}"
DEPLOYMENT_MODE="${DEPLOYMENT_MODE:-public}"
ADMIN_ALLOW_IPS="${ADMIN_ALLOW_IPS:-}"
BACKUP_KEEP_DAYS="${BACKUP_KEEP_DAYS:-3}"
BLOCK_UK="${BLOCK_UK:-false}"
AGE_RECIPIENT="${AGE_RECIPIENT:-}"
APP_DIR="deaddrop-privacy"   # relative to the deploy user's home

die() { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }
log() { printf '\033[1;33m==>\033[0m %s\n' "$*"; }

for v in SERVER APP_DOMAIN API_DOMAIN ACME_EMAIL REPO_URL REF; do
  [[ -n "${!v:-}" ]] || die "$v is not set in deploy.env"
done
# Every value below ends up inside a remote shell command or a config file,
# so each is held to a strict shape instead of being escaped.
[[ "$SERVER" =~ ^[A-Za-z0-9.:-]+$ ]] || die "SERVER looks invalid"
[[ "$APP_DOMAIN" =~ ^[a-z0-9.-]+\.[a-z]{2,}$ ]] || die "APP_DOMAIN looks invalid"
[[ "$API_DOMAIN" =~ ^[a-z0-9.-]+\.[a-z]{2,}$ ]] || die "API_DOMAIN looks invalid"
[[ "$ACME_EMAIL" =~ ^[^[:space:]\'\"@]+@[a-z0-9.-]+\.[a-z]{2,}$ ]] || die "ACME_EMAIL looks invalid"
[[ "$REPO_URL" =~ ^https://[A-Za-z0-9./_-]+$ ]] || die "REPO_URL must be an https:// git URL"
[[ "$REF" =~ ^[A-Za-z0-9._/-]+$ ]] || die "REF looks invalid"
[[ "$DEPLOYMENT_MODE" =~ ^(public|private)$ ]] || die "DEPLOYMENT_MODE must be public or private"
[[ "$ADMIN_ALLOW_IPS" =~ ^[0-9A-Fa-f:./\ ]*$ ]] || die "ADMIN_ALLOW_IPS: space-separated IPs/CIDRs only"
[[ "$BACKUP_KEEP_DAYS" =~ ^[0-9]+$ ]] || die "BACKUP_KEEP_DAYS must be a number"
[[ "$BLOCK_UK" =~ ^(true|false)$ ]] || die "BLOCK_UK must be true or false"
[[ "$DEPLOY_USER" =~ ^[a-z][a-z0-9-]*$ ]] || die "DEPLOY_USER looks invalid"

SSH_OPTS=(-o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new)
as_deploy() { ssh "${SSH_OPTS[@]}" -o BatchMode=yes "${DEPLOY_USER}@${SERVER}" "$@"; }
as_root()   { ssh "${SSH_OPTS[@]}" -o BatchMode=yes "root@${SERVER}" "$@"; }

# Variables handed to remote scripts (all validated above).
REMOTE_ENV="APP_DIR='$APP_DIR' REPO_URL='$REPO_URL' REF='$REF' APP_DOMAIN='$APP_DOMAIN' API_DOMAIN='$API_DOMAIN' ACME_EMAIL='$ACME_EMAIL' DEPLOYMENT_MODE='$DEPLOYMENT_MODE' ADMIN_ALLOW_IPS='$ADMIN_ALLOW_IPS' BACKUP_KEEP_DAYS='$BACKUP_KEEP_DAYS' DEPLOY_USER='$DEPLOY_USER' BLOCK_UK='$BLOCK_UK'"

# ------------------------------------------------------------- bootstrap --
bootstrap() {
  log "First run: setting up ${SERVER} (packages, Docker, Caddy, firewall, deploy user)"
  as_root true 2>/dev/null || die "Can't SSH in as ${DEPLOY_USER}@ or root@${SERVER}. Add your SSH key to the server (Hetzner adds it at creation if you select it)."

  as_root "$REMOTE_ENV bash -s" <<'BOOTSTRAP'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
. /etc/os-release
case "$ID" in debian|ubuntu) ;; *) echo "Unsupported OS: $ID (use Debian 12/13 or Ubuntu 24.04)"; exit 1 ;; esac

echo "--- packages"
apt-get update -q
apt-get upgrade -y -q
apt-get install -y -q ca-certificates curl gnupg git make openssl python3 sudo cron ufw unattended-upgrades

echo "--- Docker (official repo)"
install -m 0755 -d /etc/apt/keyrings
curl -fsSL "https://download.docker.com/linux/${ID}/gpg" -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/${ID} ${VERSION_CODENAME} stable" \
  > /etc/apt/sources.list.d/docker.list

echo "--- Caddy (official repo)"
curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt > /etc/apt/sources.list.d/caddy-stable.list
chmod o+r /usr/share/keyrings/caddy-stable-archive-keyring.gpg /etc/apt/sources.list.d/caddy-stable.list

apt-get update -q
apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin caddy

# Container logs are unbounded by default and would eventually fill the disk.
cat > /etc/docker/daemon.json <<'EOF'
{ "log-driver": "json-file", "log-opts": { "max-size": "10m", "max-file": "3" } }
EOF
systemctl restart docker

echo "--- automatic security updates"
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF
# Kernel/libc updates only take effect after a reboot. Every container has
# restart: unless-stopped, so the stack comes back on its own.
cat > /etc/apt/apt.conf.d/52deaddrop-reboot <<'EOF'
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "04:30";
EOF

echo "--- swap (headroom for image builds on a 4GB server)"
if ! swapon --show | grep -q .; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap -q /swapfile && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "--- firewall: SSH, HTTP, HTTPS (incl. HTTP/3) only"
# The app's containers publish only on 127.0.0.1, so Docker's habit of
# bypassing ufw for published ports doesn't expose anything here.
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow OpenSSH >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw allow 443/udp >/dev/null
ufw logging off >/dev/null   # its log records blocked IPs
ufw --force enable >/dev/null

echo "--- deploy user (${DEPLOY_USER})"
id "$DEPLOY_USER" >/dev/null 2>&1 || adduser --disabled-password --gecos "" "$DEPLOY_USER" >/dev/null
usermod -aG docker,systemd-journal "$DEPLOY_USER"   # journal: so `journalctl -u caddy` works when troubleshooting
install -d -m 700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "/home/${DEPLOY_USER}/.ssh"
install -m 600 -o "$DEPLOY_USER" -g "$DEPLOY_USER" /root/.ssh/authorized_keys "/home/${DEPLOY_USER}/.ssh/authorized_keys"

# The deploy user owns the Caddyfile and may reload Caddy — the only root
# action a deploy needs. (Docker group membership is root-equivalent anyway;
# the separate user is about not logging in as root, not containment.)
chown "$DEPLOY_USER:$DEPLOY_USER" /etc/caddy/Caddyfile
# Generated files Caddy imports (the UK IP list), writable by the deploy user.
install -d -m 755 -o "$DEPLOY_USER" -g "$DEPLOY_USER" /etc/caddy/deaddrop
echo "${DEPLOY_USER} ALL=(root) NOPASSWD: $(command -v systemctl) reload caddy" > /etc/sudoers.d/deaddrop-caddy
chmod 440 /etc/sudoers.d/deaddrop-caddy
visudo -cqf /etc/sudoers.d/deaddrop-caddy
echo "--- base setup done"
BOOTSTRAP

  # Only lock root out once the deploy user is proven to work.
  as_deploy true || die "The ${DEPLOY_USER} user can't log in. Root login is still enabled so you can investigate."
  log "Disabling root and password SSH logins"
  as_root bash -s <<'HARDEN'
set -euo pipefail
cat > /etc/ssh/sshd_config.d/01-deaddrop.conf <<'EOF'
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
EOF
sshd -t
systemctl reload ssh 2>/dev/null || systemctl restart ssh
HARDEN
  as_deploy 'touch ~/.deaddrop-bootstrapped'
}

# ---------------------------------------------------------------- deploy --
deploy() {
  if ! as_deploy 'test -f ~/.deaddrop-bootstrapped' 2>/dev/null; then
    bootstrap
  fi
  check_dns
  log "Deploying ${REF} to ${SERVER}"

  as_deploy "$REMOTE_ENV bash -s" <<'DEPLOY'
set -euo pipefail
cd ~
first_deploy=0
if [ ! -d "$APP_DIR/.git" ]; then
  git clone -q "$REPO_URL" "$APP_DIR"
  first_deploy=1
fi
cd "$APP_DIR"
git remote set-url origin "$REPO_URL"
git fetch -q --tags --prune --force origin

if git rev-parse -q --verify "refs/tags/${REF}^{commit}" >/dev/null; then
  NEW=$(git rev-parse "refs/tags/${REF}^{commit}")
elif git rev-parse -q --verify "origin/${REF}^{commit}" >/dev/null; then
  NEW=$(git rev-parse "origin/${REF}^{commit}")
else
  echo "REF '${REF}' isn't a tag or branch in ${REPO_URL}. Pushed it?"; exit 1
fi
PREV=$(git rev-parse HEAD)

# UK IP list (BLOCK_UK=true), fetched before anything changes, using the
# script from the release being deployed (git show: no checkout yet). If it
# can't be built and none exists, stop here with the old release untouched.
ranges=/etc/caddy/deaddrop/uk-ranges
if [ "$BLOCK_UK" = true ]; then
  [ -w /etc/caddy/deaddrop ] || { echo "BLOCK_UK needs /etc/caddy/deaddrop. As root: install -d -o $(id -un) -g $(id -gn) /etc/caddy/deaddrop"; exit 1; }
  if [ ! -s "$ranges" ] || [ -n "$(find "$ranges" -mtime +7)" ]; then
    git show "$NEW:scripts/uk_ip_ranges.py" | python3 - "$ranges" || [ -s "$ranges" ] \
      || { echo "Couldn't build the UK IP list and none exists yet; stopping before deploying without the block."; exit 1; }
  fi
  chmod 644 "$ranges"   # Caddy runs as its own user
fi

# --- .env: secrets generated here, once; config keys refreshed every deploy
# umask 077 only while writing secrets and dumps; reset before the checkout
# and build, or every checked-out file would become owner-only.
umask 077
set_env() {
  if grep -q "^$1=" .env; then sed -i "s|^$1=.*|$1=$2|" .env; else printf '%s=%s\n' "$1" "$2" >> .env; fi
}
if [ ! -f .env ]; then
  cp .env.example .env
  set_env DJANGO_SECRET_KEY "$(openssl rand -hex 32)"
  set_env DB_PASSWORD "$(openssl rand -hex 24)"
  set_env DJANGO_SUPERUSER_USERNAME admin
  set_env DJANGO_SUPERUSER_PASSWORD "$(openssl rand -base64 18 | tr '+/' '-_' | tr -d '=')"
  set_env DJANGO_SUPERUSER_EMAIL "admin@${API_DOMAIN}"
fi
set_env DEBUG False
set_env DEPLOYMENT_MODE "$DEPLOYMENT_MODE"
set_env ALLOWED_HOSTS "$API_DOMAIN"
set_env CORS_ORIGINS "https://${APP_DOMAIN}"
set_env VITE_API_URL "https://${API_DOMAIN}"
set_env VITE_PUBLIC_URL "https://${APP_DOMAIN}"
set_env NUM_PROXIES 1

# --- pre-deploy database dump, so `./deploy.sh rollback` can undo this
mkdir -p backups
dump=""
if docker compose ps --status running --services 2>/dev/null | grep -qx db; then
  dump="backups/pre-deploy-$(date +%Y%m%d-%H%M%S)-$(git rev-parse --short HEAD).sql"
  docker compose exec -T db pg_dump --clean --if-exists -U deaddrop deaddrop > "$dump"
  echo "Database dumped to $dump"
fi
# The rollback point moves only when the release changes. Redeploying the
# same release (e.g. after editing deploy.env) must not overwrite the way
# back to the one before it.
if [ "$first_deploy" = 1 ]; then
  rm -f .deploy-previous   # nothing deployed before this, so nothing to roll back to
elif [ "$NEW" != "$PREV" ]; then
  printf '%s\n%s\n' "$PREV" "$dump" > .deploy-previous
fi
umask 022

# --- build first (the old containers keep serving), then swap
git checkout -q --detach "$NEW"
echo "Building $(git describe --tags --always)…"
docker compose build --pull -q
docker compose up -d --remove-orphans

echo "Waiting for health…"
ok=0
for _ in $(seq 1 60); do
  if curl -fsS -o /dev/null http://127.0.0.1:8000/api/status/ && curl -fsS -o /dev/null http://127.0.0.1:5173/; then ok=1; break; fi
  sleep 3
done
if [ "$ok" != 1 ]; then
  echo "--- Not healthy after 3 minutes. Last API logs:"
  docker compose logs --tail=40 api
  echo "--- Fix forward and redeploy, or run: ./deploy.sh rollback"
  exit 1
fi

# The superuser now exists in the database. Show its password once and
# remove it from disk. Keyed on the password still being in .env, not on
# "first deploy", so a first deploy that failed health still shows it later.
admin_password=$(grep -oP '^DJANGO_SUPERUSER_PASSWORD=\K.+' .env || true)
[ -n "$admin_password" ] && set_env DJANGO_SUPERUSER_PASSWORD ""

# --- Caddy: regenerate the config from deploy.env, reload only if it changed
if [ "$DEPLOYMENT_MODE" = public ]; then max_body=1MB; else max_body="$(grep -oP '^MAX_VIDEO_SIZE_MB=\K[0-9]+' .env || echo 100)MB"; fi
admin_block=""
if [ -n "$ADMIN_ALLOW_IPS" ]; then
  admin_block="	@admin_blocked {
		path /admin*
		not remote_ip ${ADMIN_ALLOW_IPS}
	}
	respond @admin_blocked 404
"
fi
tmp=$(mktemp)
cat > "$tmp" <<EOF
# Generated by deploy.sh on every deploy — edit deploy.env, not this file.
# No access log, and the two loggers that print client IPs on errors are
# excluded: visitor IPs never reach the journal.
{
	email ${ACME_EMAIL}
	log default {
		output stderr
		exclude http.log.error http.handlers.reverse_proxy
	}
}

(common) {
	header {
		Strict-Transport-Security "max-age=31536000"
		-Server
	}
}

EOF

# UK block (BLOCK_UK=true): UK IP addresses get a 451 page on both domains.
# ADMIN_ALLOW_IPS are exempt, so the operator can reach their own instance.
uk_import=""
if [ "$BLOCK_UK" = true ]; then
  uk_import="	import ukblock"
  {
    echo "(ukblock) {"
    echo "	@uk {"
    echo "		import ${ranges}"
    if [ -n "$ADMIN_ALLOW_IPS" ]; then echo "		not remote_ip ${ADMIN_ALLOW_IPS}"; fi
    echo "	}"
    # Inline styles only: braces in the body would be read as Caddy placeholders.
    cat <<'HTML'
	handle @uk {
		header Content-Type "text/html; charset=utf-8"
		header Cache-Control "no-store"
		respond <<PAGE
			<!doctype html>
			<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Not available in the United Kingdom</title></head>
			<body style="margin:0;background:#0a0707;color:#f5e9df;font:17px/1.6 system-ui,sans-serif">
			<main style="max-width:36rem;margin:12vh auto;padding:0 1.25rem">
			<h1 style="font-size:1.4rem;line-height:1.3;color:#ffb347">Dead Drop isn't available in the United Kingdom.</h1>
			<p>The Online Safety Act 2023 imposes duties that an anonymous service like this can't meet without giving up what it's for. Rather than collect identities or weaken its privacy guarantees, Dead Drop isn't offered here.</p>
			<p>Parliament passed the Act without a single recorded vote. Any two MPs could have forced one; none did. <a href="https://bills.parliament.uk/bills/3137/stages" style="color:#ff6a3d">Check the record.</a></p>
			<p>If you think that was a mistake, <a href="https://www.writetothem.com/" style="color:#ff6a3d">tell your MP</a>.</p>
			<p style="margin-top:3rem;font-size:.8rem;color:#a68d80">Location estimated from your IP address. <a href="https://db-ip.com" style="color:#a68d80">IP Geolocation by DB-IP</a></p>
			</main></body></html>
			PAGE 451
	}
}

HTML
  } >> "$tmp"
fi

cat >> "$tmp" <<EOF
${APP_DOMAIN} {
	import common
${uk_import}
	reverse_proxy 127.0.0.1:5173
}

${API_DOMAIN} {
	import common
${uk_import}
	request_body {
		max_size ${max_body}
	}
${admin_block}	reverse_proxy 127.0.0.1:8000
}
EOF
if ! cmp -s "$tmp" /etc/caddy/Caddyfile; then
  caddy validate --adapter caddyfile --config "$tmp" >/dev/null 2>&1 || { echo "Generated Caddyfile is invalid:"; caddy validate --adapter caddyfile --config "$tmp"; exit 1; }
  cat "$tmp" > /etc/caddy/Caddyfile
  sudo systemctl reload caddy
  echo "Caddy config updated."
fi
rm -f "$tmp"

# --- housekeeping
docker image prune -f >/dev/null
# Every dump (nightly and pre-deploy) is kept BACKUP_KEEP_DAYS days. Short on
# purpose: every day kept is a day "deleted" drops still exist somewhere.
find backups -name '*.sql' -mtime +"${BACKUP_KEEP_DAYS}" -delete
( crontab -l 2>/dev/null | grep -v '# deaddrop-nightly' || true
  echo "17 3 * * * cd ~/${APP_DIR} && make -s backup >/dev/null 2>&1; find ~/${APP_DIR}/backups -name '*.sql' -mtime +${BACKUP_KEEP_DAYS} -delete # deaddrop-nightly"
) | crontab -
# Weekly UK IP list refresh while BLOCK_UK is on; reload Caddy only if the new list validates.
( crontab -l 2>/dev/null | grep -v '# deaddrop-ukranges' || true
  if [ "$BLOCK_UK" = true ]; then
    echo "41 4 * * 1 cd ~/${APP_DIR} && (umask 022; python3 scripts/uk_ip_ranges.py /etc/caddy/deaddrop/uk-ranges) >/dev/null 2>&1 && caddy validate --adapter caddyfile --config /etc/caddy/Caddyfile >/dev/null 2>&1 && sudo systemctl reload caddy # deaddrop-ukranges"
  fi
) | crontab -

echo "Deployed $(git describe --tags --always) ($(git rev-parse --short HEAD))."
if [ -n "$admin_password" ]; then
  echo
  echo "================ SHOWN ONCE — store it in your password manager now ================"
  echo "  Admin (moderation queue + kill switch): https://${API_DOMAIN}/admin/"
  echo "  user: admin    password: ${admin_password}"
  echo "==================================================================================="
fi
DEPLOY

  log "Checking the public endpoints (first deploy can take a minute while certificates are issued)"
  for i in $(seq 1 20); do
    case "$(public_check)" in
      ok) log "Live: https://${APP_DOMAIN}"; return 0 ;;
      uk) log "Live. You're seeing the UK block (HTTP 451) from where you are; check from outside the UK, or add your IP to ADMIN_ALLOW_IPS."; return 0 ;;
    esac
    sleep 6
  done
  echo "The server is healthy but https://${APP_DOMAIN} isn't answering yet. Usually DNS or the certificate is still settling: check both domains' A/AAAA records point at the server, wait a few minutes, then run ./deploy.sh status." >&2
}

# "ok", "uk" (both answer, with the UK block's 451), or "down".
public_check() {
  local a b
  a=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "https://${API_DOMAIN}/api/status/" || true)
  b=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "https://${APP_DOMAIN}/" || true)
  if [[ "$a$b" == 200200 ]]; then echo ok; elif [[ "$a$b" == 451451 ]]; then echo uk; else echo down; fi
}

# Warn (don't fail) if DNS doesn't point where we're deploying.
check_dns() {
  [[ "$SERVER" =~ ^[0-9.]+$ ]] && command -v dig >/dev/null || return 0
  for d in "$APP_DOMAIN" "$API_DOMAIN"; do
    got=$(dig +short A "$d" | tail -1)
    [[ "$got" == "$SERVER" ]] || printf '\033[1;31mWarning:\033[0m %s resolves to "%s", not %s. HTTPS certificates will fail until it does.\n' "$d" "$got" "$SERVER" >&2
  done
}

# -------------------------------------------------------------- rollback --
rollback() {
  log "Rolling back the last deploy on ${SERVER}"
  echo "This restores the previous release and the database as it was just"
  echo "before upgrading from it; anything created since then is lost."
  read -r -p "Type 'rollback' to continue: " answer
  [[ "$answer" == rollback ]] || die "Cancelled."

  as_deploy "$REMOTE_ENV bash -s" <<'ROLLBACK'
set -euo pipefail
cd ~/"$APP_DIR"
[ -f .deploy-previous ] || { echo "No previous deploy recorded."; exit 1; }
PREV=$(sed -n 1p .deploy-previous); DUMP=$(sed -n 2p .deploy-previous)
# Restore before switching code: the old code can fail to start against
# a schema the new code migrated forward.
if [ -n "$DUMP" ] && [ -f "$DUMP" ]; then
  docker compose stop api celery-worker celery-beat
  docker compose exec -T db psql -q -v ON_ERROR_STOP=1 --single-transaction -U deaddrop -d deaddrop < "$DUMP" >/dev/null
  echo "Database restored from $DUMP"
else
  echo "No pre-deploy dump (first deploy, or older than BACKUP_KEEP_DAYS) — rolling back code only."
fi
git checkout -q --detach "$PREV"
docker compose build -q
docker compose up -d --remove-orphans
for _ in $(seq 1 60); do curl -fsS -o /dev/null http://127.0.0.1:8000/api/status/ && break; sleep 3; done
curl -fsS -o /dev/null http://127.0.0.1:8000/api/status/ || { echo "Still unhealthy after rollback — check: ./deploy.sh logs"; exit 1; }
rm -f .deploy-previous
echo "Rolled back to $(git describe --tags --always)."
ROLLBACK
  echo "Set REF in deploy.env back to that release too, or the next ./deploy.sh redeploys the one you just rolled back."
}

# ---------------------------------------------------------------- extras --
backup() {
  mkdir -p backups
  local out="backups/${SERVER}-$(date +%Y%m%d-%H%M%S).sql.gz"
  log "Dumping the database to ${out}${AGE_RECIPIENT:+.age (encrypted)}"
  if [[ -n "$AGE_RECIPIENT" ]]; then
    as_deploy "cd ~/${APP_DIR} && docker compose exec -T db pg_dump --clean --if-exists -U deaddrop deaddrop" | gzip | age -r "$AGE_RECIPIENT" > "${out}.age"
  else
    as_deploy "cd ~/${APP_DIR} && docker compose exec -T db pg_dump --clean --if-exists -U deaddrop deaddrop" | gzip > "$out"
  fi
  log "Done."
}

status() {
  as_deploy "cd ~/${APP_DIR} && git describe --tags --always && docker compose ps --format 'table {{.Service}}\t{{.Status}}' && curl -fsS http://127.0.0.1:8000/api/status/ && echo"
  case "$(public_check)" in
    ok) log "Public: OK" ;;
    uk) log "Public: up (UK-blocked from your location, HTTP 451)" ;;
    *)  log "Public: NOT reachable" ;;
  esac
}

case "${1:-deploy}" in
  deploy) deploy ;;
  rollback) rollback ;;
  backup) backup ;;
  status) status ;;
  logs) ssh "${SSH_OPTS[@]}" -t "${DEPLOY_USER}@${SERVER}" "cd ~/${APP_DIR} && docker compose logs -f --tail=100 api" ;;
  ssh) ssh "${SSH_OPTS[@]}" -t "${DEPLOY_USER}@${SERVER}" "cd ~/${APP_DIR} && exec \$SHELL -l" ;;
  *) sed -n '2,20p' "$0"; exit 1 ;;
esac
