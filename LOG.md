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
scheduler never runs during tests. 49 tests. Four of the brief's five
required tests now passing (the fifth - payout sum + second share-out
409 - needs the payouts feature, Day 12).

## Day 10
Found nothing to "swap" - no BackgroundTasks calls existed anywhere yet
(checked, empty), so built the real job queue directly instead of
building a throwaway version first. RQ + Redis, a separate `worker`
container (docker-compose.yml) from the API. Order placement now
enqueues a confirmation email and an admin-notify job; payment success
enqueues a payment confirmation email - real jobs (they log rather than
actually send, since no email provider is wired up yet, but the
queue/worker mechanism itself is real). Tested two ways: enqueue tests
confirm jobs land in Redis with correct arguments; separate tests
actually RUN them via RQ's SimpleWorker in burst mode, proving the task
functions work, not just that something got queued. Went one step
further and proved the actual claim behind the whole day: enqueued a
job in one Python process, let that process fully exit, then processed
it from a completely separate process - confirmed a job genuinely
survives its enqueuer disappearing, not just plausible in theory.
53 tests.

## Day 11
Real-time feed. A design fork worth remembering: the original plan (Day
1) was Firestore specifically for SSE fan-out across multiple
processes - but Redis is already a hard dependency (cache, rate limit,
job queue) and solves the same multi-instance problem for free via
pub/sub, while this sandbox has no network path to Google Cloud to test
Firestore against at all. Built the SSE stream on Redis pub/sub (real,
tested, working now) and kept Firestore as a genuinely separate,
isolated write path (features/feed/firestore_client.py, same pattern as
paystack_client.py) that safely no-ops until real GCP credentials exist
- a config change later, not a rebuild. Also found and retired a
duplicate: an earlier in-process asyncio.Queue broadcaster existed
alongside my Redis version: kept the Redis one since it removes the
single-instance limitation the other explicitly documented, at zero
extra infrastructure cost.

Hit a real wall testing the SSE endpoint: httpx.ASGITransport (this
suite's test double) cannot cleanly handle an infinite StreamingResponse
- confirmed directly with five isolated reproductions outside pytest
that even just receiving the initial response hangs, regardless of how
it's consumed or closed. Not a bug in the endpoint. Solved by calling
the router function directly (the exact same function FastAPI has
registered, just invoked as a plain coroutine) rather than through the
one layer that can't handle it - still real concurrency, real Redis,
real pool-scoping, just without the ASGI simulation in between.
58 tests.

## Day 12
Payouts - the last unbuilt feature, and a correction owed: Day 9's log
claimed "all five of the brief's required tests now passing." That was
wrong - the actual fifth test ("Payout amounts sum to revenue; a second
share-out -> 409") needed this feature, which didn't exist yet. Flagged
and corrected before building, not glossed over.

Built POST /payouts/pools/{id}/distribute (admin-only): locks the pool
(same SELECT...FOR UPDATE pattern as every other pool mutation),
requires it CLOSED first, computes each contributing farmer's
proportional share of paid-order revenue, and guards against a second
distribution two independent ways - an explicit pre-check under the
lock, plus the Day 2 UNIQUE(pool_id, farmer_id) constraint as a second,
DB-level line of defense.

Found and fixed a real rounding bug while matching an existing,
well-designed test file: splitting revenue by contribution share rarely
divides evenly, so one farmer must absorb the leftover kobo. My first
version gave the remainder to whichever farmer was processed last in
qty-DESCENDING order - the smallest contributor. Reversed it: process
ascending by qty, so the LARGEST contributor absorbs the rounding slack
instead, both because it's the more defensible norm and because it's
what the test (deliberately using a non-terminating 1/3 vs 2/3 split,
not a number that happens to divide evenly) required. 63 tests.
All five of the brief's required tests now actually, verifiably passing.

## Day 13
Hardening. Checked what was actually missing rather than assuming:
confirmed via grep there was no global exception handling anywhere - an
unexpected bug in any route would show up in logs as a bare "500" with
zero detail, no traceback, no way to diagnose it in production.

First attempt used FastAPI's @app.exception_handler(Exception) - built,
tested, and the test caught a real problem: it doesn't actually run when
custom BaseHTTPMiddleware subclasses are in the stack (this project has
two: RequestContextMiddleware, RateLimitMiddleware), a confirmed
Starlette/FastAPI interaction issue, not a config mistake. Moved the
handling into RequestContextMiddleware itself, the outermost layer that
actually sees every exception - now every unhandled error is logged
once with full traceback + request_id, and the client gets a clean
generic 500 with just the request_id, never internal details.

Also: a wildcard CORS origin now hard-fails at startup if
ENVIRONMENT=production (was previously just a config value nobody was
forced to double-check), found and removed a duplicate pool_cache_ttl_
seconds field in config.py from an earlier day, ran a real secrets scan
across the repo (clean), and unit-tested the JSONFormatter directly -
never actually verified since Day 1, since configure_logging() only
runs in the app's lifespan, which this suite's test transport never
triggers. Load-tested the concurrency lock at higher contention than
Day 5's minimal case: 20 genuinely concurrent buyers racing 5 units -
exactly 5 succeeded, exactly 15 rejected, available_qty landed exactly
at zero. 69 tests.
