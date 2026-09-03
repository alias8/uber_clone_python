# Milestone notes

Working notes from building milestones 2-5 with Claude Code — what shipped, what broke along
the way, and what got fixed. `README.md` is the source of truth for current architecture; this
file is the session-by-session history behind it; `claude.md` carries the standing conventions
this history produced.

## Milestone 2 — Postgres

- Persistence layer: `db/tables.py` (SQLAlchemy 2.0 ORM, column-for-column port of the Kotlin
  `V1`/`V2`/`V3` migration SQL), `db/engine.py` (async engine/session-factory seam),
  `repositories.py` rewritten from in-memory dicts to async Postgres access.
- Alembic: three revisions mirroring `V1__baseline_schema.sql`/`V2__ride_indexes.sql`/
  `V3__rating_count.sql` exactly — verified with a real `alembic upgrade head` against a local
  Postgres, schema diffed column-for-column against the Kotlin migrations.
- Converted the whole call chain (`services.py`, routers, `auth/dependencies.py`) to
  `async def`; `RideService`'s accept-ride lock became `asyncio.Lock` (from `threading.Lock`).
- Tests: `tests/conftest.py` spins up a real Postgres via testcontainers per session, truncates
  tables between tests. Found and fixed two real bugs during this: `migrations/env.py` was
  unconditionally overwriting the test DB URL with the configured default (tests were silently
  hitting the wrong database); `main.py`'s `dispose()` was nulling the shared SQLAlchemy engine
  on every `TestClient` teardown, breaking every test after the first.
- Manually verified end-to-end against a real local Postgres: register → driver register → go
  online → request ride → accept → start → complete → rate, then restarted the server and
  confirmed the data survived — proof it's no longer in-memory.
- Follow-up cleanup: split `repositories.py` and `services.py` into one-module-per-class
  packages (`repositories/{user,driver,ride,rating}.py`, `services/{driver,ride,rating}.py`),
  matching `uber_clone`'s one-file-per-repository/-service Kotlin layout — routers were already
  split that way.
- 55 tests passing at the end of this milestone.

## Milestone 3 — Redis

- Driver geo-index (`drivers:locations`, `GEOSEARCH`) and availability set
  (`drivers:available`) replace the in-memory lat/lng overlay from M2 — `dispatch.py` does the
  reads, `repositories/driver.py`'s new `set_location()`/`clear_location()` do the writes.
- Surge cache (`pricing.py::SurgeCache`) and rate limiting (`rate_limit.py`, via the `limits`
  package) both moved from in-memory to real Redis.
- **Real bug caught before it shipped**: originally bundled the geo-index write into the same
  `DriverRepository.save()` call that handles `is_available` — but a plain availability toggle
  (`mark_available_by_id`/`mark_unavailable_by_id`) always re-fetches the driver first, with no
  location info, so that bundling would have silently wiped a driver's position out of Redis on
  every `go_online`/availability change. Fixed by keeping location entirely separate
  (`set_location()`/`clear_location()`), never touched by `save()`.
- **Real bug caught by the test suite**: both the raw Redis client and `limits`' Redis storage
  bind their connections to the asyncio event loop that created them. `TestClient` gives each
  instance its own event loop, and tests juggle several `TestClient`s per test (rider + driver
  + …) — sharing one client/storage object across them crashed with "attached to a different
  loop." Fixed by handing back one client/strategy **per running event loop** instead of a
  single process-wide singleton, in both `redis_client.py` and `rate_limit.py`.
- Removed code that became dead once Redis owned this state: `Driver.lat`/`lng` fields,
  `DriverRepository.all()`/`.clear()`, the never-called `count_nearby_available_drivers`,
  `PricingService.clear_cache()`, `RateLimiter.clear()`.
- Manually verified: nearby-driver search correctly filters by both radius and live
  availability; rate limiting 429s past the real Redis-backed threshold; driver geo/availability
  state survives a full app restart.
- 55 tests passing (same count as M2 — this milestone was a backing-store swap, not new
  endpoints).

## Milestone 4 — Kafka

- `kafka_producer.py` publishes the bare ride id (no key, no envelope — same wire format as
  `KafkaTemplate<String, String>.send(topic, rideId)`) on `ride-requested`/`-accepted`/
  `-completed`/`-cancelled`, wired into every transition in `services/ride.py`.
- `kafka_consumer.py`: a background `asyncio` task started in `main.py`'s lifespan.
  `ride-requested` dispatches to nearby drivers via new `dispatch.py::fanout_to_nearby_drivers`
  (Redis `SADD`+`EXPIRE` on `dispatched:{rideId}`, then `PUBLISH` per driver to
  `ride_offers:{driverId}` with a camelCase JSON payload matching Kotlin's wire format exactly).
  `ride-accepted` clears that key. `ride-completed`/`-cancelled` were left as stubs with the
  SSE-dependent lines commented out (filled in during M5).
- `stale_ride_retry.py`: a 60-second background loop re-publishing any ride still `REQUESTED`
  after 2 minutes — same timing as `StaleRideRetryJob.kt`.
- **Real bug, third instance of the M3 cross-loop issue**: the Kafka **producer** is called
  synchronously from request handlers (same as Redis), so it needed the same per-running-loop
  client cache as `redis_client.py`. The **consumer** didn't need it — it's a fire-and-forget
  background task tied to whichever loop started it, reading from a real external broker rather
  than in-process state, so it doesn't matter which loop produced the message it's consuming.
- 8 new tests in `tests/test_dispatch.py`, including one end-to-end test that lets the real
  lifespan-started consumer do the dispatching rather than calling the handler function
  directly. 63 tests passing.
- Manually verified against real local Postgres+Redis+Kafka: a requested ride landed in Redis's
  `dispatched:` set with the correct driver and ~5-minute TTL purely through the async Kafka
  round-trip; accepting cleared it; the stale-retry job's real 60-second loop republished a
  backdated ride, confirmed by consuming the actual Kafka topic.
- Environment note: James's local machine has a native Postgres/Redis already listening on the
  standard ports (used by the Kotlin `uber_clone` project) — Docker's containers for the same
  ports silently lose the port race on this machine, so manual smoke tests during M2-M4 ended up
  exercising that native Redis/Postgres instead of the throwaway Docker container. Not a bug in
  the port, just a local environment quirk worth knowing about when smoke-testing manually.

## Milestone 5 — SSE

- `sse.py`: an in-process `asyncio.Queue`-per-connection registry ported from
  `EmitterRegistry.kt`, keyed by ride id or driver id. `GET /rides/{id}/location` and
  `GET /driver/offers` are real `StreamingResponse` SSE endpoints with the same guards as
  Kotlin (403/409 checks fire before a stream is ever created).
- `ride_offer_listener.py`: a background task bridging Redis's `ride_offers:*` pub/sub (written
  by M4's dispatch fan-out) into a driver's own SSE stream — ported from `RideOfferListener.kt`.
- Filled in the four `# deferred until M5` spots from M4: `services/driver.py::update_location`
  now emits `driver_location` (and `go_offline` closes the driver's own offer stream);
  `kafka_consumer.py::handle_ride_accepted` now emits `driver_eta_to_pickup` (via new
  `dispatch.py::get_driver_location`, Redis `GEOPOS`) and `offer_cancelled` to every other
  dispatched driver before clearing the dispatched-drivers key; `handle_ride_completed`/
  `handle_ride_cancelled` now close the ride's stream.
- **Real environment constraint, confirmed before writing any tests**: `TestClient`'s httpx
  transport buffers a response's full body before returning control, so it hangs against a
  genuinely open-ended SSE stream — there is no way to read a live SSE stream through
  `TestClient` in this environment. `tests/test_sse.py` (11 tests) verifies the registry and
  every `emit()`/`complete()` call site directly instead, plus the router guard-rejection paths
  (which return an ordinary complete response before a stream is ever created, so they're safe).
- **Real bug in the test suite itself** (not the app): a test called `handle_ride_completed` on
  a ride that was never actually completed. The handler's status guard (correctly matching
  Kotlin's `if (ride.status != RideStatus.COMPLETED) return`) no-op'd as designed, so
  `sse.complete()` was never called — and the test's `await queue.get()` had no timeout, so it
  blocked forever instead of failing. Cost a genuinely long debugging detour (an isolated repro
  script was needed to find it) before landing on the fix: complete the ride for real before
  exercising the handler, and add `asyncio.wait_for(..., timeout=...)` around every queue read
  in this test file that crosses an await boundary.
- 74 tests passing (63 existing + 11 new).
- Manually verified the entire live pipeline with real `curl -N` streams against the running
  app: a driver's `/driver/offers` stream received a real `ride_offer` event through the full
  Redis → Kafka → listener path; a rider's `/rides/{id}/location` stream received a real
  `driver_location` event after the driver posted a location update; the location stream closed
  on its own the moment the ride was completed.

## Standing scope note

Confirmed with James: neither `uber_clone-python` nor the Kotlin `uber_clone` is ever deployed
to real infrastructure — this is a repo-only project. Milestone 6's "GitHub Actions CI running
against real infra" means real Postgres/Redis/Kafka *containers* via docker-compose, still
running inside Docker and GitHub's own runners — no cloud provisioning, no hosted environment.
Milestone 7 (stretch) is porting the AWS deployment *design doc*, not standing up a live
deployment.

## What's left

- **Milestone 6** — `Dockerfile` for the app, `docker-compose.yml` wiring up app + Postgres +
  Redis + Kafka, and a CI update to build the image and run against that compose stack (not
  just the testcontainers-backed `pytest` suite, which already covers real infra at the
  library level).
- **Milestone 7 (stretch)** — port `uber_clone`'s multi-region AWS deployment doc.
