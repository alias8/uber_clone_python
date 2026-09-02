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
  config.py          Settings via pydantic-settings
  models.py           Domain dataclasses: User, Driver, Ride, Rating, Role, RideStatus
  repositories.py      In-memory repos — swapped for SQLAlchemy 2.0/Postgres in M2
  geo.py               Haversine distance + ETA (ported from GeoUtils.kt)
  pricing.py            Fare + surge formulas, in-process (see above)
  dispatch.py            Nearby-driver radius search — in-memory now, Redis GEOSEARCH in M3
  rate_limit.py           In-memory fixed-window limiter — Redis-backed `limits` in M3
  services.py              RideService / DriverService / RatingService — the domain logic
  state.py                  Process-wide singletons wiring repos + services together
  auth/
    jwt.py                   JWT encode/decode (ported from JwtUtil.kt)
    cookies.py                HttpOnly cookie issuance (ported from JwtCookieService.kt)
    dependencies.py            FastAPI auth + require_role() dependencies (ported from JwtFilter.kt)
  routers/
    auth.py, rides.py, driver.py   One router per original Spring controller
  main.py                    FastAPI app
tests/                       pytest — unit tests against the in-memory stores for now,
                              testcontainers-backed integration tests arrive with Postgres/Redis/Kafka in M2-M4
```

## Ported faithfully vs. reimplemented

Kept exact (same constants, same formulas, ported test cases prove numeric parity): Haversine + 30 km/h ETA; the surge formula (`clamp(pending/available, 1.0, 3.0)`, ~1km grid cache, 30s TTL); the fare formula (`$2.00 + $1.50/km`, HALF_UP rounding to 2dp); rate-limit thresholds (5 ride-requests/min/rider, 10 auth-attempts/15min/IP); the ride state machine and every guard condition; the JWT dual-role design (DB `role` = permanent capability, JWT `role` claim = active mode, reissued on `/auth/switch-mode`).

Genuinely reimplemented, where idiomatic Python differs enough to be worth naming: JPA `@Version` optimistic locking → a lock around the accept-ride check-then-write sequence (see `services.py`); Bucket4j → an in-memory fixed-window limiter for now, `limits`+Redis from M3; `@PreAuthorize` → FastAPI `Depends()` role guards; `GEORADIUS` (deprecated in Redis 6.2+) → `GEOSEARCH` once M3 adds Redis; the gRPC `pricing-service` → the in-process `pricing.py` module described above.

## Running it

```
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

uvicorn ride_service.main:app --reload
# docs at /docs
```

## Checks

```
ruff check .      # lint
mypy              # strict type check
pytest -q         # unit tests, all against in-memory stores at this milestone
```

All three run in CI on every push/PR (`.github/workflows/ci.yml`).

## Status

This is milestone 1: FastAPI skeleton, JWT-cookie auth with the dual-role design, the full ride state machine, in-process fare/surge quoting, and driver registration/dispatch — all backed by in-memory repositories, all covered by tests, `mypy --strict` and `pytest` clean. Not yet built:

- **Milestone 2** — Postgres (SQLAlchemy 2.0 async + Alembic), replacing the in-memory repos
- **Milestone 3** — Redis: driver geo-index (`GEOSEARCH`), availability set, `limits`-backed rate limiting, surge cache
- **Milestone 4** — Kafka async dispatch pipeline (`ride-requested`/`accepted`/`completed`/`cancelled`) + stale-ride retry
- **Milestone 5** — SSE endpoints (`GET /rides/{id}/location`, `GET /driver/offers`)
- **Milestone 6** — Docker + docker-compose + GitHub Actions CI running against real infra
- **Milestone 7 (stretch)** — port the multi-region AWS deployment doc, optionally a live deploy
