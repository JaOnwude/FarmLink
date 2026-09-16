# Contributing to FarmLink

## Project structure — Architecture by Feature (vertical slicing)

Code is grouped by **business domain**, not by technical layer. Everything
about orders lives in `app/features/orders/`, not spread across a global
`routers/`, `models/`, `schemas/` folder.

```
app/
  main.py                 # wires settings, middleware, and every feature's router together
  core/                   # cross-cutting only - things EVERY feature depends on
    config.py             # env-based settings
    database.py            # async SQLAlchemy engine/session, Base
    logging_config.py      # structured JSON logging
    security.py            # JWT + password hashing (Day 3)
  middleware/
    request_context.py     # X-Request-ID, timing, logging
  features/
    auth/                  # Day 3
    pools/                 # Day 4
    contributions/         # Day 4
    orders/                # Day 5 - the pooled-stock concurrency problem
    payments/               # Day 8-9 - Paystack
    payouts/                # Day 12
    feed/                   # Day 11 - Firestore SSE
      router.py            # HTTP layer only - request/response wiring, status codes
      schemas.py            # Pydantic request/response models
      models.py             # SQLAlchemy ORM models for this feature's tables
      service.py            # business logic - the router calls into this, not the other way around
```

### The rule for where new code goes

- Does **every** feature need it (settings, DB session, logging, JWT decode)?
  → `app/core/`
- Does it belong to **one** business domain (orders, payments, pools...)?
  → that feature's folder, in the matching file (`router.py` for endpoints,
  `service.py` for logic, `models.py` for tables, `schemas.py` for
  request/response shapes)
- A brand-new domain that doesn't exist yet? → create
  `app/features/<name>/` with the same four files.

Tests mirror this: feature tests go in `tests/features/<name>/test_*.py`
once a feature has real logic to test (the Day 5 concurrency tests will be
the first ones here). Cross-cutting tests (like the Day 1 health check)
stay at `tests/`.

Keep `router.py` thin. If you're writing an `if` statement that decides
business outcome (can this order be placed, is this pool still open),
that belongs in `service.py`, not the router.

## Repo and branches

- `main` — always deployable, protected. No direct pushes.
- `develop` — integration branch; merge into `main` at the end of each week.
- Feature branches: `feat/day5-order-locking`, `feat/day8-paystack-init`,
  `fix/rate-limiter-off-by-one`. One branch per day's task, or per logical
  piece of it.

## Commits

Small and frequent, with real messages:

```
feat(orders): add SELECT FOR UPDATE lock on pool row
fix(payments): verify x-paystack-signature before trusting webhook body
test(orders): add two-concurrent-buyers 409 test
docs: update CONTRIBUTING with payouts feature scope
```

Prefix with the type (`feat`, `fix`, `test`, `docs`, `chore`) and the
feature folder it touches.

## Pull requests

- Every change lands via a PR, even solo work — the other collaborator
  reviews and approves before merge. This is also what makes GitHub's
  Insights → Contributors tab reflect who actually did what.
- CI (`.github/workflows/ci.yml`) must pass before merge — this is a
  branch protection rule on `main`/`develop`, not optional.
- PR description: which day/task from the plan this covers, and which
  feature folder(s) it touches.

## Daily rhythm

1. Check the GitHub Project board (one card per day/task from the
   two-week plan). Assign yourself a card before starting.
2. Branch off `develop`, work inside the relevant `features/<name>/`
   folder.
3. Open a PR when the day's task is done (or end-of-day even if
   unfinished — a draft PR is fine, better than a day of untracked work).
4. Review each other's PRs before merging — this is also how you both
   stay familiar with the whole codebase, not just your own half.
5. Short async standup message: what you did today, what's next, what's
   blocked.

## Environment

Never commit `.env` or real Paystack keys. `.env.example` documents every
variable that's needed; copy it and fill in your own local `.env`, which
is gitignored. Test-mode Paystack keys (`sk_test_...`) for all local and
CI work; live keys only ever live in the production host's secret
manager.
