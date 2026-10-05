# Security policy

## Reporting a vulnerability

**Do not open a public GitHub issue for a security vulnerability.** Email
`pax@paxiimus.com` with a description and, if you have one, a
reproduction.

This is a solo-maintained project. There's no guaranteed response-time SLA
— that wouldn't be honest to promise — but security reports get priority
over everything else in the queue. You'll get an acknowledgement, and
credit in the fix's changelog/commit unless you'd rather stay anonymous.

## What's actually in scope

The properties this app claims, and where to look if you think one of them
doesn't hold:

- **Messages are end-to-end encrypted** — the server never has the key
  (it lives in the URL fragment, never sent in any request) and cannot
  decrypt ciphertext it relays. See `lib/crypto.ts` (client) and
  `MessageSerializer`/`MessageListCreateView` (server — it only ever
  touches opaque ciphertext).
- **Anonymous drops carry no identity** — no account, no email, no PII.
  See `identities/auth.py` for how signed requests work without any of
  that.
- **Photos are stripped of EXIF** (device/GPS metadata) on upload — see
  `DropPhoto._strip_exif` in `drops/models.py`.
- **IPs are never persisted** — they only ever touch Redis-backed
  throttle caches, never the database. See `drops/throttles.py`.
- **The public instance has no media upload path at all** — not rate
  limited, not gated by identity, genuinely absent. See "Deployment tiers
  and media safety" in `README.md` for the full reasoning, including an
  approach that was tried and deliberately removed for not actually
  working.

A report that one of these doesn't hold as described is a real
vulnerability report. Please include enough detail to reproduce it.

## What's expected behavior, not a vulnerability

Worth knowing before reporting, so effort doesn't get spent on intended
behavior:

- **A drop is reachable by anyone who has its link.** "Private" means
  *unlisted*, never *access-controlled* — this is stated explicitly and
  repeatedly in the README, not an oversight. If you can think of a way to
  discover a drop's link *without already having it* (guessing UUIDs,
  enumerating IDs, leaking a link through some other channel), that's a
  real report. Simply "I have the link and can see the drop" is the
  system working as designed.
- **Video isn't stripped of embedded metadata**, unlike photos — a known,
  documented, currently-unaddressed gap (see `DropVideo`'s docstring in
  `models.py`), not something that needs reporting again.
- **Losing an `owner_secret` means permanently losing management access
  to that drop.** No recovery, by design — there's nothing to reset it
  against, since there's no account behind it.
- **Content-scanning for uploaded media isn't wired up.** Also documented,
  also known, also not a new report — see the README section above.

## Supported versions

This project doesn't currently maintain multiple release branches — fixes
land on the latest version only.
