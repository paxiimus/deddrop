# Dead Drop

A privacy-first, open-source tool for leaving something at a location for
someone else to find. No account required to use it. No email, no PII, no
tracking. Self-hostable by anyone.

Closer in spirit to Tor, Signal, or SecureDrop than to a consumer app:
minimal features, minimal data, maximum defensibility.

## Table of contents

- [How it works](#how-it-works)
- [Architecture](#architecture)
- [Project structure](#project-structure)
- [Running it yourself](#running-it-yourself)
- [API reference](#api-reference)
- [Rate limiting and abuse protection](#rate-limiting-and-abuse-protection)
- [The kill switch](#the-kill-switch)
- [The private-access gate](#the-private-access-gate)
- [Deployment tiers and media safety](#deployment-tiers-and-media-safety)
- [Platform notes (PWA / React web)](#platform-notes-pwa--react-web)
- [Explicitly not in this app](#explicitly-not-in-this-app)
- [Funding](#funding)
- [Contributing](#contributing)
- [Security](#security)
- [License](#license)
- [Stack](#stack)

## How it works

**Anonymous drops (no account, ever).** Enter coordinates, a description,
optionally a password and an expiry. You get back a one-time `owner_secret`
— save it if you want to be able to edit or delete the drop later. There is
no recovery if you lose it. That's the trade: zero account, zero identity,
zero PII, and the server never has anything to hand over even if compelled to.

**Identities (optional, for managing several drops).** An identity is
nothing but an ed25519 public key generated on your device. There's no
signup — the first request you sign with a given key registers it
automatically. The private key never leaves your device; the server never
sees it, never stores a password, never has anything to leak. See
`identities/auth.py` for exactly how requests are signed and verified.

**Messages are opaque to the server.** The `ciphertext` field is whatever
your client's own end-to-end encryption produced. The server relays it and
cannot read it — there is no server-side decryption path, by design.

**Reports require no identity.** Flagging a drop for moderation never asks
who you are.

**Photos are automatically stripped of EXIF, on the deployments that have
media enabled at all.** A photo taken to help someone find a drop is
exactly the kind of file that carries GPS coordinates and device
identifiers in its metadata — potentially leaking far more than the
coordinates you actually chose to share. Every upload is re-encoded on save
(`drops/models.py: DropPhoto._strip_exif`) so none of that survives. See
"Deployment tiers and media safety" below for why media isn't available at
all on a public instance, and what video (also supported, private-
deployment-only) does differently here.

## Architecture

Two separately-deployed pieces talking over a REST API — not a monolith,
and neither one knows anything about the other's internals. The API
doesn't render HTML or know a route exists; the frontend doesn't touch a
database or know Django exists. Everything that crosses the boundary is
JSON, or multipart form data for uploads, over the documented endpoints
below.

- **`api/`** — Django 5 + Django REST Framework. Owns every piece of
  state: drops, messages, identities, moderation reports, site status.
  Postgres for persistent data. Redis for two things at once — the shared
  cache layer every rate limit and the private-access gate depend on
  (`deaddrop/cache.py`), and Celery's own broker, on a separate database
  index so the two never share a key namespace. Celery worker + beat run
  the two scheduled tasks that actually enforce retention
  (`drops/tasks.py`).
- **`web/`** — React 18 + Vite + TypeScript, built as a static PWA and
  served by nginx in production. The only client that exists — no mobile
  app, no other frontend. Talks to `api/` exclusively over `fetch()`, on a
  different origin in any real deployment (see "Platform notes" for what
  that implies for CORS and cookies).

Two background processes run continuously, independent of any request:
`expire_drops` (every 5 minutes) flips a drop's status once its
`expires_at` passes; `purge_old_drops` (hourly) actually deletes it — row,
photos, files on disk, messages — once it's past that same point. Reports
survive the purge (`Report.drop` is `SET_NULL`, not `CASCADE`), so a
moderation record persists even after the reported content itself is gone.

A request that mutates anything goes through, in order: the private-access
gate (if one is set), the kill switch (if cooling), CORS, then whichever
per-endpoint throttle applies, then the view itself. Each of those is a
genuinely separate concern with its own failure mode — see their own
sections below for why they're not collapsed into one mechanism.

## Project structure

```
api/
├── deaddrop/               # Project-level: settings, URL root, middleware, Celery config
│   ├── settings.py         # Single source of truth for every env-driven setting
│   ├── urls.py             # Top-level URL routing
│   ├── middleware.py       # PrivateAccessMiddleware, CoolingMiddleware
│   ├── private_access.py   # The shared private-access gate logic
│   ├── cache.py            # Fail-open Redis cache wrapper
│   └── celery.py           # Celery app definition
├── drops/                  # The core app — drops, messages, reports, site status
│   ├── models.py           # Drop, DropPhoto, DropVideo, Message, Report, SiteStatus
│   ├── views.py            # DropViewSet, upload views, StatusView
│   ├── serializers.py      # Create/update/read serializers, field validation
│   ├── permissions.py      # CanManageDrop
│   ├── throttles.py        # Every per-IP rate limit
│   ├── tasks.py            # expire_drops, purge_old_drops (Celery)
│   └── migrations/
├── identities/              # Pseudonymous, keypair-based identity — separate from drops
│   ├── auth.py             # SignatureAuthentication — how a signed request is verified
│   ├── models.py           # Identity: a public key and nothing else
│   ├── throttles.py
│   └── views.py            # MeView
├── scripts/verify.py       # Static cross-reference checker — see `make verify`
└── entrypoint.sh           # Container startup: migrate, collectstatic, then Gunicorn/Celery

web/src/
├── pages/                  # One component per route
│   ├── Home.tsx            # Map + nearby-drops discovery
│   ├── CreateDrop.tsx      # New drop, anonymous or identity-owned
│   ├── DropDetail.tsx      # A single drop — messages, collect, edit, delete
│   └── Identity.tsx        # Generate/import/export a keypair, see your own drops
├── components/             # Shared UI — Modal, LoadingButton, CoolingScreen,
│                           # PrivateAccessScreen, DropQRCode, ErrorBoundary...
├── lib/
│   ├── api.ts              # Every backend call, request signing, error extraction
│   ├── crypto.ts           # ed25519 keypair generation, signing, E2E message encryption
│   └── storage.ts          # localStorage — identity, owner secrets, message keys
└── App.tsx                 # Routing, and the top-level cooling/private-access screen swap
```

## Running it yourself

This is the quick local/dev version — full containerized stack, no domain
or TLS needed. For an actual production deployment with a real domain
(either the public instance or a single private deployment), see
`DEPLOYING.md` instead.

```bash
cp .env.example .env   # fill in DJANGO_SECRET_KEY, DB_PASSWORD, VITE_API_URL
make up                 # builds and starts db, redis, api, celery, and the web app
```

That's the whole containerized stack — API on `:8000`, web app on `:5173`.
`make help` lists every other task (migrations, backups, a Django shell,
deployment). Leave `DJANGO_SUPERUSER_USERNAME`/`_PASSWORD` blank in `.env` to
skip creating an admin account entirely — you only need one for the
moderation queue at `/admin/`.

For active frontend development, run the web app outside Docker instead —
Vite's hot reload is far faster than rebuilding an image on every change:

```bash
make web-install   # once
make web-dev        # Vite dev server on :5173 with hot reload
```

`VITE_API_URL`, `VITE_DEPLOYMENT_MODE`, and the other `VITE_MAX_*` settings
are all baked into the web app **at build time**, not read at container
startup — changing any of them means `make build` (or `make up`, which
rebuilds), not just a restart.

One-click deploy templates (DigitalOcean, Railway) are still just an idea,
not built. `DEPLOYING.md` covers a real, current production deployment —
the public instance or a single private one. Managed hosting for
organisations that would rather not run their own is also available as a
paid offering — get in touch — though the operational playbook behind
that isn't part of this repo, deliberately: AGPL protects this code from
being closed-sourced and resold, but it does nothing to protect a
business's own operational know-how, which isn't really "the software" in
the same sense at all.

## API reference

Every endpoint except `/api/status/` and `/admin/` sits under `/api/`.
This is the map, not the full detail — actual request/response shapes
live in the serializers (`drops/serializers.py`, `identities/serializers.py`)
and are worth reading directly if you're integrating against this.

| Method | Path | Auth | What it does |
|---|---|---|---|
| GET | `/api/status/` | none | Cooling / private-access gate state — always reachable, regardless of either |
| POST | `/api/status/` | none | Submit a private-access code |
| GET | `/api/drops/?lat=&lng=&radius=` | none | Discover nearby public drops |
| GET | `/api/drops/?mine=1` | signed | Your own identity-owned drops |
| GET | `/api/drops/?ids=a,b,…` | none | Up to 50 drops by ID in one request — how a device checks on the anonymous drops it holds owner secrets for |
| POST | `/api/drops/` | none, or signed | Create a drop — anonymous or identity-owned depending on whether the request is signed |
| GET | `/api/drops/{id}/` | none, or `X-Drop-Password` | Retrieve a drop — coordinates hidden until a password-protected drop's password is supplied |
| PATCH | `/api/drops/{id}/` | owner secret or signed as creator | Edit a drop |
| DELETE | `/api/drops/{id}/` | owner secret or signed as creator | Delete a drop |
| POST | `/api/drops/{id}/collect/` | none, or password in body | Mark a drop collected |
| POST | `/api/drops/{id}/report/` | none | Flag a drop for moderation — by design, never asks who you are |
| GET | `/api/drops/{id}/messages/` | none | Read a drop's message thread — ciphertext only, unreadable without the key from the share link |
| POST | `/api/drops/{id}/messages/` | none | Send an already client-encrypted message |
| POST | `/api/drops/{id}/photos/` | owner secret or signed as creator | Upload a photo — private deployments only, `MEDIA_ENABLED` gated |
| POST | `/api/drops/{id}/videos/` | owner secret or signed as creator | Upload a video — private deployments only |
| GET | `/api/identities/me/` | signed | Your identity, auto-created on its first signed request |
| PATCH | `/api/identities/me/` | signed | Set an optional display name |

"Signed" means the `X-Public-Key` / `X-Signature` / `X-Timestamp` headers
`identities/auth.py` verifies against a hash of the request body — see
`web/src/lib/crypto.ts: signRequest` for exactly what gets signed and why
the query string is deliberately excluded from it. "Owner secret" means
the `X-Owner-Secret` header, checked against the hash stored at creation
time for a fully anonymous drop.

## Rate limiting and abuse protection

Every endpoint is throttled, per IP, on Redis-backed counters shared
correctly across every Gunicorn worker. That sharing needed its own fix
partway through this project's life — a missing explicit cache setting
meant every limit below was, for a while, only ever enforced per worker
process, not globally; see `deaddrop/cache.py` for the full story and why
it now also fails open rather than closed.

| Scope | Rate | Applies to |
|---|---|---|
| `drop_create` | 20/hour | Creating a drop, any kind |
| `drop_create_anon_cap` | 1 per `MAX_DROP_LIFETIME_HOURS` | Anonymous drop creation specifically — identity-owned drops bypass this, capped instead by a DB-backed active-drops limit |
| `drop_read` | 300/hour | Baseline for everything else on a drop — list, a plain retrieve, edit, delete, collect |
| `drop_password_attempt` | 30/hour | Retrieving or collecting a password-protected drop — the actual brute-force defense on a drop's own password |
| `report` | 30/hour | Flagging a drop |
| `photo_upload` | 30/hour | Photo or video upload, one shared bucket for both |
| `message_create` | 60/hour | Sending a message — reading an existing thread is deliberately unthrottled |
| `identity_me` | 100/hour | Reading or updating your own identity |
| `private_access_attempt` | 10/hour | Submitting the private-access code, on deployments that set one |
| — | 10/hour | New identities registered from one IP (signature auth runs before view throttles) |
| — | 30 failures/hour | Wrong drop passwords on a protected drop's message thread |
| — | ⅓ of `MAX_MESSAGES_PER_DROP` | Messages one IP can post to a single drop's thread |
| — | 10/hour | Admin login attempts per IP |

None of this defends against a legitimate, distributed surge — every
individual IP staying comfortably within its own limit while the
aggregate still overwhelms the server. That's a different problem, and
it's what the kill switch, next, actually exists for.

## The kill switch

Per-IP throttling (`drops/throttles.py`) protects against one abusive
source. It does nothing against legitimate, distributed traffic that's
simply more than a donation-funded budget can absorb — a viral moment, a
front-page hit, every individual IP well within its own limit while the
aggregate overwhelms the server anyway. That needs a different tool: a
global circuit breaker, not a rate limit.

Set `cooling_until` on the `SiteStatus` row in Django admin (the same
place moderation already happens) to a future time, optionally with a
`cooling_message`. From that moment, `CoolingMiddleware` rejects every
request except `/api/status/` and `/admin/` itself with a 503, and the
frontend replaces the entire app with a "DEADDROP COOLING" screen and a
countdown. Nothing to remember to turn back on — the instant
`cooling_until` passes, `is_cooling` (a property, not a stored flag)
goes false and everything resumes automatically.

A donation-maintained, no-SLA service has no obligation to survive a
surge it can't afford — this is the honest version of that: closed for a
bit, here's when, rather than a server quietly struggling or a bill
quietly spiking.

## The private-access gate

`DEPLOYMENT_MODE=private` (below) only ever changed a feature set — media
uploads, configurable limits. It never restricted who could reach the
instance at all. This is the separate thing that does: set
`PRIVATE_ACCESS_CODE` (`.env`; `make secrets` will generate one) to a
single long, random value, and nothing — not the API, not the admin
panel — is reachable without it. Blank, the default, means no gate at
all, exactly how every deployment of this project has always worked.

One shared secret, not a keypair or a per-person credential, on purpose.
The threat model here is "keep out anyone who wasn't given access" —
scanners, crawlers, someone stumbling onto the URL — not "defend against
a targeted attacker," which is what the per-drop passwords and
end-to-end message encryption inside the app are already for. A shared
secret provides exactly the same practical guarantee an asymmetric
scheme would for that specific threat, with meaningfully less code and
therefore meaningfully less surface for the kind of subtle bug a
custom auth flow tends to invite. If you need to revoke one specific
person's access later, rotate the code and re-share the new one with
everyone else — there's no per-user token to individually invalidate,
which is a real limitation if you need one, not an oversight if you
don't.

A signed cookie, not a session — `deaddrop/private_access.py` verifies
it via `django.core.signing`, entirely in memory, with no database or
cache lookup on any request either way. The cookie encodes the code it
was granted *with*, not a bare yes/no flag, which is what makes
rotation actually work as revocation: an old cookie decodes to the old
code, which no longer matches the live setting, so access is
automatically revoked the moment the code changes — not merely disabled
until that cookie's own 30-day expiry.

Deliberately gates `/admin/` too, unlike the kill switch's own exemption
for it — there, the operator needed an escape hatch to manage cooling
without already knowing it was active. Here there's no equivalent need:
the operator already knows the access code, since they set it, so
requiring it before `/admin/` as well is genuine defense in depth, not a
risk of locking themselves out.

## Deployment tiers and media safety

One setting — `DEPLOYMENT_MODE` (`.env`, `public` or `private`) — governs
both of these together, rather than a pile of independent flags an operator
has to remember to keep consistent with each other. `public` is the safe
default; an unrecognised value fails closed to `public`, not `private`.

**Media (photos and video) doesn't exist at all on a public instance —
there's no upload path a client can reach, regardless of what it sends.**
This needed real back-and-forth to land on, worth being honest about. The
first pass required an identity for every photo upload and could ban one on
report, reasoning that accountable-but-anonymous uploads were better than
fully anonymous ones. That reasoning didn't hold up: an ed25519 keypair
costs nothing and verifies nothing to generate, so a determined bad actor
defeats identity-based banning for free, while the mechanism itself still
adds real complexity and real data collection (who uploaded what) for
essentially no protection against the thing it existed to stop. It was
removed.

The actual reason media is public-vs-private, not identity-vs-anonymous:
messages in this app are genuinely end-to-end encrypted — the server can't
read them, full stop. Media can't practically get the same treatment; the
whole point is that whoever has the link can see it, so it's stored and
served in the clear. Any platform letting anyone upload and serve images or
video publicly, with nothing scanning it, is a target for the worst kind of
abuse — not a hypothetical, it's the reason every major (and most minor)
image-hosting platform runs content scanning. **This project has no
content-scanning wired up**, and can't wire it up on anyone's behalf — that
needs an operator's own provider credentials (Thorn Safer, Google CSAI
Match, Microsoft PhotoDNA, or direct NCMEC/IWF access are the standard
options). Given that, the only honest position for an anonymous,
internet-facing public instance is not offering the risky surface at all.

**A drop's own public/private flag was never the right lever here, for a
separate reason worth being explicit about**: that flag means *unlisted*,
not *access-controlled*. A public drop is more likely to be stumbled on and
reported by a random person browsing nearby; an unlisted one is only ever
seen by whoever already has the link — the distribution shape abuse
actually wants, not the one it avoids. Restricting media to private
*drops* specifically would have made this worse, not better.

`DEPLOYMENT_MODE=private` is a different, legitimate lever, for a different
reason: not because private *drops* hide things, but because the entire
population of a private *deployment* is vetted and accountable to whoever
is running it — a real change to the risk calculus a shared anonymous
public instance can't claim. It enables media uploads (`MEDIA_ENABLED` in
`settings.py`, gating `DropPhotoUploadView`/`DropVideoUploadView`
regardless of what any client sends) and offers `CreateDrop`'s "Private
exchange" mode — a clearly labelled entry point for arranging a private,
unlisted, password-optional handoff between two people. That messaging
capability itself isn't new or gated: `is_public=false` plus the E2E
message thread already fully implements it, always has; what's gated on
`private` is the explicit framing, for the same trust-and-safety reasoning
as media.

**Video specifically has one more known gap, flagged prominently in
`DropVideo`'s docstring in `models.py`**: unlike photos, it isn't stripped
of metadata on upload. EXIF stripping for images uses Pillow, a
pure-Python library already in this project's dependencies, easy to verify
by reading the code. The video equivalent needs an external tool (ffmpeg)
invoked as a subprocess — more moving parts, and not something to claim as
done without actually running and verifying it, which this project has no
way to do. If a private deployment expects device-recorded video from a
population where embedded location/device metadata matters, that's a real
prerequisite to close, not a nice-to-have.

**Two operational things worth knowing before relying on video uploads at
100MB:** Gunicorn's request timeout defaults to 300s specifically to give
this room — 100MB in 120s (the old default) needs ~6.7Mbps sustained
upload throughput, not a safe assumption on a mobile connection, and video
documenting a physical drop location is exactly a mobile-upload use case.
Separately, if you ever put a reverse proxy (nginx, Caddy) in front of the
API for TLS, its default body size limit is usually far smaller than
100MB (nginx defaults to 1MB) and will reject the upload with a 413
*before it ever reaches Django*, completely invisible to anything in this
codebase. Remember `client_max_body_size 100m;` (or whatever you set
`MAX_VIDEO_SIZE_BYTES` to) when that day comes — see `Caddyfile.example`
and `DEPLOYING.md` for the reverse proxy this project actually documents.

## Platform notes (PWA / React web)

- **CORS.** `X-Public-Key`, `X-Signature`, `X-Timestamp`, and `X-Owner-Secret`
  are non-standard headers that a browser blocks at preflight unless
  explicitly allowed — see `CORS_ALLOW_HEADERS` in `settings.py`. Cookies
  (for the private-access gate) need `CORS_ALLOW_CREDENTIALS = True`
  alongside `credentials: "include"` on the client — without both, a
  cross-origin cookie is silently never sent back at all, not an error,
  just a request that looks like it's missing the grant it was just
  given.
- **CSRF.** Not a concern — every DRF view in this project is CSRF-exempt
  by default (`APIView.as_view()` wraps itself in `csrf_exempt`), and
  CSRF only gets manually re-enabled inside `SessionAuthentication`,
  which this project never uses anywhere. True for the private-access
  gate's cookie too, even though it's the first cookie this app has ever
  set — the exemption is unconditional, not tied to whether a cookie is
  involved.
- **HTTPS is required in production for identity-signed actions specifically.**
  `crypto.subtle` (used to hash the request body before signing — see
  `identities/auth.py`) only exists in a secure context: HTTPS, or
  `localhost` during development. `localhost` counts as secure even over
  plain HTTP, so this won't bite during local dev — only a real deployment
  without TLS. Note this is `crypto.subtle` specifically, not
  `crypto.getRandomValues` (used for key generation), which has no such
  restriction — anonymous drops and key generation work fine either way;
  it's signed requests (managing an identity-owned drop, "my drops") that
  need it.
- **Image URLs are already absolute**, not relative — DRF's `ImageField`
  calls `request.build_absolute_uri()` automatically whenever `request` is in
  the serializer context, which it is everywhere in this codebase.

## Explicitly not in this app

No escrow, no listings/categories, no ratings, no in-app payments, no
per-drop fee, no biometrics. These are the features that turn "coordination
tool" into "marketplace" — kept out deliberately, both on principle and
because that shape carries real legal and platform risk that a neutral,
general-purpose tool doesn't.

## Funding

There's no fee to create a drop and there never will be — that's the actual
promise. If you want to support hosting costs, as many ways as possible:

**Crypto: Coming Soon**

- Bitcoin (BTC): `<address>`
- Ethereum (ETH): `<address>`
- Monero (XMR): `<address>`
- Tether (USDT, <network>): `<address>`
- Solana (SOL): `<address>`
- BNB (BNB Smart Chain): `<address>`
- XRP: `<address>` — destination tag: `<tag, if required>`

**PayPal:** `<PayPal.me link>`

## Contributing

See `CONTRIBUTING.md` — the short version: small clear fixes can just be a
PR, anything touching auth/encryption/retention/deployment-mode gating or
adding new scope should be an issue first.

## Security

Found a vulnerability? See `SECURITY.md` — please don't open a public
issue for it.

## License

AGPL-3.0. If you run a modified version as a network service, you must make
your source available to its users — see `LICENSE`.

Copyright (C) 2026 Paxiimus (Kejon Fubler)

## Stack

Django 5 / DRF backend, Postgres, Redis + Celery for scheduled expiry.
React + Vite + TypeScript PWA in `web/` — the only client. No mobile app.
Map is Leaflet + OpenStreetMap tiles (no Google Maps dependency, matches the
project's ethos). Theme is dark — midnight background, sunset-orange/amber/
pink accents — defined once as CSS variables in `web/src/styles.css`. The
whole stack — including the web app, built and served via nginx — runs
through `docker-compose.yml`, wrapped by the `Makefile` for day-to-day use.
