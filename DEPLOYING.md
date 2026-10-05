# Deploying Dead Drop

For running *one* real instance — with a real domain and TLS — either the
donation-funded public instance, or a single private deployment for your
own org.

Steps 1–4 are identical either way. Step 5 is where public and private
actually diverge — one setting, `DEPLOYMENT_MODE`, same as everywhere
else in this project.

## Prerequisites

- A server with a public IP — any VPS provider, or your own hardware with
  a port forwarded. See "Server sizing" below for actual numbers, not a
  guess.
- A domain you control, and access to its DNS.
- Docker and the Docker Compose plugin installed:

  ```bash
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker $USER   # log out/in once for this to take effect
  ```

## Server sizing

2 vCPU / 4GB RAM / any modern VPS's default disk allocation. Not a guess
— here's where it actually goes for the full stack (Postgres, Redis,
Gunicorn's 3 workers, Celery worker + beat, the frontend's nginx, OS and
Docker overhead):

| Component | Rough RAM |
|---|---|
| Postgres | ~250MB |
| Redis (cache + Celery broker) | ~75MB |
| Gunicorn, 3 workers | ~300MB |
| Celery worker + beat | ~150MB |
| Nginx (frontend container) | ~20MB |
| OS + Docker overhead | ~300MB |
| **Total baseline** | **~1.1GB** |

4GB leaves real headroom above that for request spikes and OS page
cache — 2GB technically runs, but leaves uncomfortably little margin for
a first real launch where you don't yet know your actual traffic shape.

Disk is close to a non-issue regardless of provider: `DEPLOYMENT_MODE=public`
means zero media storage, and the 2-week retention ceiling bounds the
database to *current* usage, never accumulation since launch. Whatever
your provider's smallest useful tier includes is almost certainly
generous enough.

**Don't size up for a hypothetical viral moment** — that's specifically
what the kill switch (see "The kill switch" in `README.md`) exists to
handle gracefully. Start at the numbers above, watch real usage, resize
later if sustained growth actually demands it.

**If you're on Hetzner** (a genuinely good fit for this project —
unmanaged compute, no managed database to fight with, matches how this
stack is already built to run its own Postgres/Redis): CPX22 — 2 vCPU /
4GB / 80GB NVMe / 20TB traffic, roughly €8/month as of 2026. Their ARM
line (CAX) is typically cheaper for equivalent specs, and every base
image this stack uses (`postgres:16-alpine`, `redis:7-alpine`,
`python:3.12-slim`, `node:20-alpine`, `nginx:alpine`) has native ARM64
support, so it's a real option, not just a theoretical one. One thing
worth knowing, not a reason to avoid them: reviews this year cluster
complaints around account suspensions and abuse-flag false positives
more than anything else, and an anonymous drop-creation tool is exactly
the shape of service that can occasionally trip automated abuse
detection at *any* provider. Run `make backup` on an actual schedule and
keep copies somewhere other than the same account — then losing the
account isn't the same as losing the data.

## 1. DNS

Two subdomains, both pointed at your server's IP — the app is a
separate-origin SPA + API, same as every deployment of this project (see
`VITE_API_URL` / `CORS_ORIGINS` in `.env.example`):

```
app.your-domain.example   A   <your server IP>
api.your-domain.example   A   <your server IP>
```

## 2. Clone and configure

```bash
git clone <this repo's URL> deaddrop-privacy
cd deaddrop-privacy
cp .env.example .env
```

Generate real secrets rather than leaving the unsafe defaults in place:

```bash
make secrets
```

Paste the output into `.env`, replacing the `DJANGO_SECRET_KEY` and
`DB_PASSWORD` lines.

Set the rest of `.env`:

```
ALLOWED_HOSTS=api.your-domain.example
CORS_ORIGINS=https://app.your-domain.example
VITE_API_URL=https://api.your-domain.example
```

## 3. Public or private

**Public instance** (anonymous, donation-funded, no media uploads — see
`README.md`'s "Deployment tiers and media safety" for why that last part
is deliberate, not a limitation to work around):

```
DEPLOYMENT_MODE=public
```

Nothing else to set — every cap (`MAX_DROP_LIFETIME_HOURS` etc.) is fixed
regardless of what's in `.env` when `DEPLOYMENT_MODE=public`, by design.

**Private deployment** (for a known, vetted population — media enabled,
every cap actually configurable):

```
DEPLOYMENT_MODE=private
MAX_DROP_LIFETIME_HOURS=336
MAX_ACTIVE_DROPS_PER_IDENTITY=30
MAX_ANON_DROPS_PER_IP=1
MAX_PHOTOS_PER_DROP=2
MAX_PHOTO_SIZE_MB=5
MAX_VIDEOS_PER_DROP=1
MAX_VIDEO_SIZE_MB=100
MAX_MESSAGE_LENGTH=20000
```

(Values shown are this project's own defaults — raise or lower any of
them for what this specific deployment actually needs.)

**Gating who can reach it at all** is a separate setting from either of
the above — see README.md's "The private-access gate" for the full
reasoning. In short: blank (the default) means anyone can reach the
instance, same as always; set one to require it before anything —
including `/admin/` — is reachable at all:

```
PRIVATE_ACCESS_CODE=
```

`make secrets` generates a suitable value alongside your other two
secrets. Share it with whoever you want to have access, through whatever
channel you'd trust with the rest of this — a cookie set once lasts 30
days, so this isn't something people need to re-enter constantly. To
revoke one specific person later, rotate this value and re-share the new
one with everyone else; there's no individual per-person credential to
invalidate instead.

## 4. TLS / reverse proxy

`Caddyfile.example` in this directory is a minimal single-instance
config — copy it, fill in your domains, done. Caddy issues and renews
Let's Encrypt certificates automatically; nothing else to configure for
TLS.

```bash
sudo apt install caddy   # or see caddyserver.com for other distros
cp Caddyfile.example /etc/caddy/Caddyfile
# edit the two domain lines in it
sudo systemctl reload caddy
```

## 5. Go live

```bash
make up
make check       # Django's own system checks
make superuser   # for the moderation queue at /admin/ — leave blank in .env to skip this entirely
```

Confirm the API healthcheck is passing (`make ps` should show `api` as
healthy), then visit `https://app.your-domain.example`.

## 6. Before telling anyone it exists

```bash
make check-placeholders
```

Catches the mechanical stuff — LICENSE present, secrets actually changed,
no leftover `your-domain.example`-style placeholders in the docs. It
doesn't catch everything worth checking (there's no automated check for
"did you actually pick donation addresses" or "does the domain in your OG
tags match reality") — treat a clean run as a floor, not a finish line.
