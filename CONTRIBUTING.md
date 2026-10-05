# Contributing to Dead Drop

Thanks for considering it. A few things worth knowing before you do.

## The project's actual filter

"Minimal features, minimal data, maximum defensibility" (from the README)
isn't a slogan, it's the thing every change here gets measured against. A
contribution that adds tracking, adds a dependency on a third-party service
the app talks to directly, or expands scope without a clear reason will
likely get pushback regardless of how well it's written. When in doubt,
smaller and more boring is usually the right call for this codebase
specifically.

## Before you write code

- **Small, clear fixes** (typos, an obvious bug, a docs correction) — just
  open a PR.
- **Anything touching auth, encryption, retention, or the
  `DEPLOYMENT_MODE` gating** — open an issue first. These are the
  load-bearing trust decisions the whole project depends on, and they've
  each already had real back-and-forth to land where they are — see
  `README.md` for that reasoning before proposing a change to it, so
  we're not re-litigating settled ground from scratch.
- **New features** — open an issue first. Given the project's own stated
  philosophy above, most proposals should expect "why does this need to
  exist here" as the first question, not a rubber stamp.

## Code style

Match what's already there rather than introducing a new pattern:

- Concise over clever. No abstraction added "for later" — add it when a
  second real use case actually shows up.
- Comments explain *why*, not *what* — the codebase is written this way
  throughout (read a few files to get a feel for it before writing your
  own). A comment restating the code in English isn't useful; a comment
  explaining the non-obvious reasoning behind a decision is.
- If you're fixing a bug, a one-line comment on the fix explaining what
  was actually wrong is more valuable than the fix alone — it's what
  stops the same mistake happening again somewhere else.

## Tests

There's no test suite yet — a known, acknowledged gap, not an oversight.
If you're adding non-trivial logic (anything beyond a trivial fix),
including tests for it is genuinely appreciated even though nothing
currently enforces that. Don't feel obligated to also backfill tests for
unrelated existing code in the same PR.

## Security issues

**Do not open a public issue for a vulnerability.** See `SECURITY.md`.

## Running it locally

See "Running it yourself" in `README.md`.
