# FarmLink — Daily Log

## Day 1
Project scaffolding: FastAPI skeleton, Docker Compose (Postgres + Redis + API),
`pydantic-settings`-based config (all secrets from environment, nothing
hardcoded), async SQLAlchemy engine, structured JSON logging, the
request-id/timing middleware from the flowchart, Alembic scaffolding, CI
pipeline with real Postgres + Redis service containers. 3 passing smoke
tests.

## Day 2
Core data model: all 8 tables (`users, pools, contributions, orders,
allocations, payments, payouts, processed_events`) as SQLAlchemy models,
split across feature folders. First Alembic migration, verified with a
full downgrade/upgrade round-trip against a real Postgres. Fixed a real
bug: cross-feature relationships need every model module imported into
one registry before SQLAlchemy resolves them (`app/core/model_registry.py`).
Fixed a real test-flakiness bug: a shared connection pool doesn't survive
across pytest's per-test event loops - solved with `NullPool` outside
production.

## Day 3
Auth: JWT issue/verify, password hashing, `get_current_user` (401),
`require_role` (403). Redis-backed token bucket rate limiter (429 +
Retry-After), implemented as an atomic Lua script. Dropped `passlib`
(unmaintained, breaks against modern `bcrypt`) for `bcrypt` directly.
Fixed a Redis-equivalent of Day 2's event-loop bug. 12 tests.

## Day 4
Pools (admin-only writes, public reads) and contributions CRUD. First
real use of `SELECT ... FOR UPDATE` on the pools table - a rehearsal for
Day 5's harder version. Two of the brief's five required tests passing:
closed pool rejects contributions with 409, and a second close attempt
on an already-closed pool also 409s. 22 tests.

## Day 5
**The hard problem.** Order placement with `SELECT ... FOR UPDATE`
locking the pool row for the whole transaction. Three of the five
required tests proven with real concurrency (`asyncio.gather`, not
sequential awaits): two buyers racing for the last 40 bags -> one 201,
one 409; exact-fit succeeds, over-order 409, allocations never exceed
contributions; closed pool rejects orders with 409. Also added an
`Idempotency-Key` header on order placement, enforced by a DB-level
partial unique index. Ran the concurrency test 5x back-to-back to rule
out a lock that only sometimes works. 29 tests.

## Day 6
Cache-aside on pool listings (`GET /pools`, `GET /pools/{id}`), Redis,
invalidated on every write that touches a pool rather than relying on
TTL alone. Proven with tests that mutate the DB directly and confirm the
cache is actually being read (not silently bypassed) and actually
invalidated (not just expiring). Tuned the rate limiter: a separate,
stricter bucket (5 per 5 minutes) for `/auth/login` and `/auth/register`
specifically, since the general 100/min limit does nothing against
credential stuffing. 33 tests.

### Mid-week addition (after Day 6)
Added `pools.unit` (free-text: "bag", "tuber", "basket", etc.) and
`GET /contributions/pool/{pool_id}/summary` - a server-side `GROUP BY`
returning each farmer's total contribution to a pool, not raw
per-contribution rows. Scoped entirely to `app/features/pools/` and
`app/features/contributions/`, confirming the vertical-slice structure
does what it's supposed to. 37 tests.

## Day 7
Review pass, no code changes. Checked for stray TODOs across the
codebase - the only ones remaining were the expected Day-1 stubs for
features not yet built (`payments`, `feed`, `payouts`). Nothing to fix.
(This entry exists specifically because a review day with zero diff
would otherwise leave no trace it happened.)

## Day 8
Paystack integration. `POST /payments/initialize` (ownership + status
checked - can't pay for someone else's order or re-pay a paid one).
`POST /payments/webhook` - verifies `x-paystack-signature`
(HMAC-SHA512 over the raw request body, not a re-serialized copy),
every event recorded in `processed_events` in the same transaction as
its effect. Tested directly against the brief's `mock_payment_provider.py`
four scenarios (valid, duplicate, bad signature, orphan reference), all
passing against the real signature scheme. Caught a test-hygiene bug:
hardcoded event IDs collided with leftover rows from an earlier run
against the same persistent dev database - fixed with unique IDs per
test run. 45 tests.

## Day 9
Scheduled sweep job (APScheduler, its own clock, never triggered by
request traffic): releases unpaid orders after 24h and gives
`available_qty` back. The brief's fifth and final required test:
sweep releases a 25-hour-old unpaid order, pool quantity goes back up.
Found and fixed a real race while building it: a payment succeeding at
the exact moment the sweep decides the same order is expired could
interleave with the sweep's check. Fixed by locking the ORDER row
(`SELECT ... FOR UPDATE`) in both the sweep and the webhook handler -
whichever acquires the lock first wins outright, the other correctly
sees the already-updated status. Verified directly that this test
suite's ASGI transport never triggers FastAPI's lifespan, so the real
scheduler never runs during tests. 49 tests. All five of the brief's
required tests now passing.
