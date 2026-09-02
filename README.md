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
                        repository (user.py/driver.py/ride.py/rating.py) — driver lat/lng stay
                        an in-memory overlay until M3's Redis geo-index (see package docstring)
  geo.py               Haversine distance + ETA (ported from GeoUtils.kt)
  pricing.py            Fare + surge formulas, in-process (see above)
  dispatch.py            Nearby-driver radius search — in-memory now, Redis GEOSEARCH in M3
  rate_limit.py           In-memory fixed-window limiter — Redis-backed `limits` in M3
  services/                RideService / DriverService / RatingService, one module each — the
                            domain logic, async
  state.py                  Process-wide singletons wiring repos + services together
  auth/
    jwt.py                   JWT encode/decode (ported from JwtUtil.kt)
    cookies.py                HttpOnly cookie issuance (ported from JwtCookieService.kt)
    dependencies.py            FastAPI auth + require_role() dependencies (ported from JwtFilter.kt)
  routers/
    auth.py, rides.py, driver.py   One router per original Spring controller
  main.py                    FastAPI app
migrations/                 Alembic — three revisions mirroring uber_clone's V1/V2/V3 SQL exactly
tests/                       pytest — testcontainers spins up a real throwaway Postgres per test
                              session (Docker must be running); Redis/Kafka integration tests
                              arrive with M3/M4
```

## Ported faithfully vs. reimplemented

Kept exact (same constants, same formulas, ported test cases prove numeric parity): Haversine + 30 km/h ETA; the surge formula (`clamp(pending/available, 1.0, 3.0)`, ~1km grid cache, 30s TTL); the fare formula (`$2.00 + $1.50/km`, HALF_UP rounding to 2dp); rate-limit thresholds (5 ride-requests/min/rider, 10 auth-attempts/15min/IP); the ride state machine and every guard condition; the JWT dual-role design (DB `role` = permanent capability, JWT `role` claim = active mode, reissued on `/auth/switch-mode`); the Postgres schema itself — same tables, columns, types, indexes and constraints as `V1__baseline_schema.sql`/`V2__ride_indexes.sql`/`V3__rating_count.sql`, via three matching Alembic revisions.

Genuinely reimplemented, where idiomatic Python differs enough to be worth naming: JPA `@Version` optimistic locking → an `asyncio.Lock` around the accept-ride check-then-write sequence (see `services.py`); Bucket4j → an in-memory fixed-window limiter for now, `limits`+Redis from M3; `@PreAuthorize` → FastAPI `Depends()` role guards; `GEORADIUS` (deprecated in Redis 6.2+) → `GEOSEARCH` once M3 adds Redis; the gRPC `pricing-service` → the in-process `pricing.py` module described above; Flyway → Alembic, run as an explicit `alembic upgrade head` step rather than on app boot.

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
pytest -q         # tests — spins up a real Postgres via testcontainers, so Docker must be running
```

All three run in CI on every push/PR (`.github/workflows/ci.yml`); GitHub's `ubuntu-latest`
runners have Docker available by default, so testcontainers needs no extra CI setup.

## Status

Milestones 1-2 are done: FastAPI skeleton, JWT-cookie auth with the dual-role design, the full
ride state machine, in-process fare/surge quoting, driver registration/dispatch, and now real
Postgres persistence (SQLAlchemy 2.0 async + Alembic) behind the same repository interfaces —
all covered by tests running against a real database via testcontainers, `mypy --strict` and
`pytest` clean. Driver `lat`/`lng` are the one thing still in-memory (not part of the Kotlin
schema either — that's a Redis geo-index in the real system, M3 here). Not yet built:

- **Milestone 3** — Redis: driver geo-index (`GEOSEARCH`), availability set, `limits`-backed rate limiting, surge cache
- **Milestone 4** — Kafka async dispatch pipeline (`ride-requested`/`accepted`/`completed`/`cancelled`) + stale-ride retry
- **Milestone 5** — SSE endpoints (`GET /rides/{id}/location`, `GET /driver/offers`)
- **Milestone 6** — Docker + docker-compose + GitHub Actions CI running against real infra
- **Milestone 7 (stretch)** — port the multi-region AWS deployment doc, optionally a live deploy
