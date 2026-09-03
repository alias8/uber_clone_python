# ride-service

The Python/FastAPI sibling of [`uber_clone`](../uber_clone) — the same ride-hailing domain (rider requests, driver dispatch, surge pricing, ratings), rebuilt with a production-style Python stack instead of Kotlin/Spring.

## Why this exists

`ai-agent-demo-python` proved out the pattern for one domain (an LLM tool-calling agent); this repeats it for a much larger one — a stateful, multi-actor backend with real-time dispatch, pricing, and a ride state machine — to make the Python backend claim broader than a single small service. Same production bar as that project: `mypy --strict`, tested, CI-checked.

The Kotlin original also has an honest gap worth calling out: it has exactly one test file (pure Haversine/ETA math) and no Docker or CI at all — its own README says service-level tests are "the next thing to add." This port treats full test coverage, Docker, and CI as first-class deliverables from milestone 1 rather than a stretch goal, so the domain finally gets that coverage here, not just a language swap.

## A deliberate simplification: no gRPC

`uber_clone` splits fare/surge quoting into a standalone gRPC `pricing-service`, specifically to practice a synchronous, must-answer-now service boundary. This port drops that split — fare/surge quoting is a plain in-process module (`pricing.py`) called directly from the ride flow, not a second deployable service. The formulas and constants are still ported exactly (see `pricing.py`'s docstring), so a known pickup/dropoff pair and a known pending-rides/available-drivers ratio produce the same numbers as the Kotlin `PricingGrpcService`. This is a scope decision, not a missing piece — see the plan this was built from for the reasoning.

## Project layout

```
src/ride_service/
  config.py          Settings via pydantic-settings (JWT, cookies, rate limits, DATABASE_URL)
  models.py           Domain dataclasses: User, Driver, Ride, Rating, Role, RideStatus
  db/
    tables.py           SQLAlchemy 2.0 ORM tables — column-for-column port of the V1-V3 SQL
    engine.py            Async engine/session-factory seam (see "Database" below)
  repositories/        Postgres-backed repos (async, via db/engine.py), one module per
                        repository (user.py/driver.py/ride.py/rating.py) — driver location and
                        availability live in Redis instead (see repositories/driver.py)
  redis_client.py       Async Redis client seam (see "Redis" below)
  geo.py               Haversine distance + ETA (ported from GeoUtils.kt)
  pricing.py            Fare + surge formulas; surge cache is Redis (see above)
  dispatch.py            Nearby-driver search via Redis GEOSEARCH + the availability set, plus
                          fanout_to_nearby_drivers() — the ride-requested dispatch fan-out
  rate_limit.py           `limits`-backed rate limiting against Redis
  kafka_producer.py        Publishes ride-requested/accepted/completed/cancelled
  kafka_consumer.py        Consumes those four topics (see "Kafka" below)
  stale_ride_retry.py       Background job re-publishing stuck REQUESTED rides
  sse.py                     In-process SSE registry (see "SSE" below)
  ride_offer_listener.py      Redis pub/sub -> SSE bridge for driver ride offers
  services/                RideService / DriverService / RatingService, one module each — the
                            domain logic, async
  state.py                  Process-wide singletons wiring repos + services together
  auth/
    jwt.py                   JWT encode/decode (ported from JwtUtil.kt)
    cookies.py                HttpOnly cookie issuance (ported from JwtCookieService.kt)
    dependencies.py            FastAPI auth + require_role() dependencies (ported from JwtFilter.kt)
  routers/
    auth.py, rides.py, driver.py   One router per original Spring controller — rides.py has
                                    GET /{id}/location, driver.py has GET /offers (both SSE)
  main.py                    FastAPI app — lifespan starts/stops the Kafka consumer, retry job,
                              and the ride-offer listener
migrations/                 Alembic — three revisions mirroring uber_clone's V1/V2/V3 SQL exactly
tests/                       pytest — testcontainers spins up a real throwaway Postgres, Redis,
                              AND Kafka broker per test session (Docker must be running)
```

## Ported faithfully vs. reimplemented

Kept exact (same constants, same formulas, ported test cases prove numeric parity): Haversine + 30 km/h ETA; the surge formula (`clamp(pending/available, 1.0, 3.0)`, ~1km grid cache, 30s TTL); the fare formula (`$2.00 + $1.50/km`, HALF_UP rounding to 2dp); rate-limit thresholds (5 ride-requests/min/rider, 10 auth-attempts/15min/IP); the ride state machine and every guard condition; the JWT dual-role design (DB `role` = permanent capability, JWT `role` claim = active mode, reissued on `/auth/switch-mode`); the Postgres schema itself — same tables, columns, types, indexes and constraints as `V1__baseline_schema.sql`/`V2__ride_indexes.sql`/`V3__rating_count.sql`, via three matching Alembic revisions; the Redis design — same `drivers:locations` geo-index and `drivers:available` set keys, same `surge:{lat}:{lng}` cache key format and 30s TTL, as `DriverService.kt`/`PricingGrpcService.kt`; the Kafka pipeline — same four topic names and bare-ride-id wire format, same `dispatched:{rideId}` key + 5-minute TTL and `ride_offers:{driverId}` pub/sub channel + camelCase JSON payload as `DispatchService.kt`, same 60s/2-minute stale-retry timing as `StaleRideRetryJob.kt`; the SSE design — same event names (`driver_location`, `driver_eta_to_pickup`, `offer_cancelled`, `ride_offer`), same registry keying (ride id for the location stream, driver id for the offers stream), and deliberately the same single-instance limitation as `EmitterRegistry.kt` (see "SSE" below).

Genuinely reimplemented, where idiomatic Python differs enough to be worth naming: JPA `@Version` optimistic locking → an `asyncio.Lock` around the accept-ride check-then-write sequence (see `services/ride.py`); Bucket4j → the `limits` package against Redis, fixed-window rather than a true token bucket; `@PreAuthorize` → FastAPI `Depends()` role guards; `GEORADIUS` (deprecated in Redis 6.2+) → `GEOSEARCH` (Kotlin still uses the deprecated command); the gRPC `pricing-service` → the in-process `pricing.py` module described above; Flyway → Alembic, run as an explicit `alembic upgrade head` step rather than on app boot; Spring Kafka's `@KafkaListener`s → a plain `asyncio.create_task` consume loop in `kafka_consumer.py`; `@Scheduled(fixedDelay=...)` → an `asyncio.sleep`-loop task in `stale_ride_retry.py`; Spring's `SseEmitter` → an `asyncio.Queue` per connection in `sse.py`, read by a `StreamingResponse` generator.

## Database

Postgres via SQLAlchemy 2.0's async engine (`asyncpg`) + Alembic. `DATABASE_URL` (env var, see
`config.py`) defaults to `postgresql+asyncpg://jameskirk:password@localhost:5432/uber_clone_python`
— mirroring `uber_clone`'s own default Postgres user/password, against a separate database.

Run a local Postgres however you like (a native install, or one `docker run` line, matching
`uber_clone`'s own assumption of an already-running local Postgres — no compose file here
either, that's M6):

```
docker run -e POSTGRES_USER=jameskirk -e POSTGRES_PASSWORD=password \
  -e POSTGRES_DB=uber_clone_python -p 5432:5432 postgres:16-alpine
```

Then apply migrations once before starting the app — this is a separate, explicit step, not
run automatically on app boot (matching how Flyway is a distinct concern from app startup in
the Kotlin original's `ddl-auto=validate`):

```
alembic upgrade head
```

`migrations/versions/` has three revisions mirroring `uber_clone`'s `V1__baseline_schema.sql`,
`V2__ride_indexes.sql` and `V3__rating_count.sql` exactly.

## Redis

`REDIS_URL` (env var, see `config.py`) defaults to `redis://localhost:6379/0` — mirroring
`uber_clone`'s `spring.data.redis.host=localhost`/`port=6379` defaults. Backs three things:

- **Driver geo-index + availability set** (`drivers:locations`, `drivers:available`) —
  written by `repositories/driver.py::save()`/`set_location()`/`clear_location()`, read by
  `dispatch.py`'s `GEOSEARCH`-based nearby-driver search. `is_available` is still also a
  Postgres column (dual-write, same as `uber_clone`) — Redis just makes the "who's nearby and
  free right now" query fast without scanning the whole `drivers` table.
- **Surge cache** (`surge:{lat}:{lng}`, 30s TTL) — `pricing.py`'s `SurgeCache`.
- **Rate limiting** — `rate_limit.py`, via the `limits` package.

Run a local Redis the same way as Postgres — one `docker run` line, no compose file:

```
docker run -p 6379:6379 redis:7-alpine
```

## Kafka

`KAFKA_BOOTSTRAP_SERVERS` (env var, see `config.py`) defaults to `localhost:9092` — mirroring
`uber_clone`'s `spring.kafka.bootstrap-servers`. Four topics, one per ride-lifecycle event
(`ride-requested`/`-accepted`/`-completed`/`-cancelled`), each carrying just the bare ride id
as its value (no key, no envelope) — same wire format as `KafkaTemplate<String,
String>.send(topic, rideId)`.

`services/ride.py` publishes on every ride state transition; `kafka_consumer.py`'s background
loop (started in `main.py`'s lifespan) reacts to `ride-requested` by dispatching to nearby
drivers (`dispatch.py::fanout_to_nearby_drivers` — Redis `SADD`/`EXPIRE` on `dispatched:
{rideId}`, then `PUBLISH` to each driver's `ride_offers:{driverId}` channel) and to
`ride-accepted` by clearing that dispatched-drivers key. `ride-completed`/`ride-cancelled` are
wired up but nearly empty — in `uber_clone` those two exist mostly to drive SSE
(`EmitterRegistry`), which doesn't exist in this port yet (M5); the SSE lines are commented the
same way this project has deferred every prior milestone's out-of-scope pieces. A background
`stale_ride_retry.py` task re-publishes any ride still `REQUESTED` after 2 minutes, checking
every 60s — same timing as `StaleRideRetryJob.kt`.

Run a local broker the same way as Postgres/Redis — one `docker run` line:

```
docker run -p 9092:9092 apache/kafka:3.8.0
```

## SSE

`GET /rides/{id}/location` (RIDER, must be that ride's rider, 409 unless the ride is
`MATCHED`/`IN_PROGRESS`) and `GET /driver/offers` (DRIVER) are real Server-Sent Events streams,
backed by `sse.py` — an in-process registry (`asyncio.Queue` per connection, keyed by ride id
or driver id) ported from `EmitterRegistry.kt`. Four event types, matching the Kotlin names
exactly: `driver_location` (from `services/driver.py::update_location`, to the rider tracking
that driver's active ride), `driver_eta_to_pickup` and `offer_cancelled` (from
`kafka_consumer.py`'s `ride-accepted` handler), and `ride_offer` (from `ride_offer_listener.py`,
a background task bridging the `ride_offers:*` Redis pub/sub channel — written by
`dispatch.py::fanout_to_nearby_drivers` — to a driver's own stream).

This keeps `EmitterRegistry.kt`'s single-instance limitation deliberately: a stream only
receives events pushed on the same process holding its connection, same as the Kotlin original.
Not something this port "fixes," since the Kotlin source doesn't do it either and nothing in
the milestone list asks for multi-instance-safe SSE.

`TestClient` can't exercise a live, open-ended stream in this environment (its httpx-based
transport buffers the full response body before returning, which just hangs against a
never-ending generator) — `tests/test_sse.py` covers the registry and every emit/complete call
site directly instead. See a real stream working with `curl -N <url>` (cookies from a prior
login/register call) against the running app.

## Running it

```
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

alembic upgrade head
uvicorn ride_service.main:app --reload
# docs at /docs
```

## Checks

```
ruff check .      # lint
mypy              # strict type check
pytest -q         # tests — spins up real Postgres + Redis + Kafka via testcontainers, so Docker must be running
```

All three run in CI on every push/PR (`.github/workflows/ci.yml`); GitHub's `ubuntu-latest`
runners have Docker available by default, so testcontainers needs no extra CI setup. The Kafka
broker (ZooKeeper-based `confluentinc/cp-kafka` under testcontainers) is the slowest part of the
suite to start — budget ~30-45s of one-time session startup on top of Postgres/Redis.

## Status

Milestones 1-5 are done: FastAPI skeleton, JWT-cookie auth with the dual-role design, the full
ride state machine, fare/surge quoting, driver registration/dispatch, Postgres persistence
(SQLAlchemy 2.0 async + Alembic), Redis (driver geo-index, availability set, surge cache, rate
limiting), the Kafka dispatch pipeline + stale-ride retry, and now SSE for live driver
location/ETA and ride offers — all covered by tests running against real Postgres, Redis, and
Kafka via testcontainers, `mypy --strict` and `pytest` clean. Not yet built:

- **Milestone 6** — Docker + docker-compose + GitHub Actions CI running against real infra
- **Milestone 7 (stretch)** — port the multi-region AWS deployment doc, optionally a live deploy
