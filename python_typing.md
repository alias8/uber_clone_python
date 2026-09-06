# Python typing: practical tools

Python's type hints are optional and unenforced by the interpreter itself — unlike Kotlin,
where the compiler rejects a type mismatch before the code ever runs. Closing that gap in a
real project means combining tools at two different points: **compile-time** (static analysis,
before running) and **runtime** (checked as the code actually executes).

## Compile-time / static

Catches bugs in your editor or CI, before the code runs.

| Tool | What it does | Notes |
|---|---|---|
| `mypy` | The original, most complete static type checker | Already used in this repo (`pyproject.toml`, `--strict`). Standard default choice. |
| `pyright` | Microsoft's checker; powers VSCode's Pylance | Faster than mypy, arguably better inference in some cases. Worth trying alongside mypy if it ever feels slow or misses something. |
| `ruff` | Linter, not a type checker | Already used here for style/import/bug-pattern rules — pairs with mypy as the standard "fast linter + slow type checker" combo. |
| `pre-commit` | Git hook runner | Wires `mypy`/`ruff` to run automatically before every commit, not just when remembered manually or in CI. |

## Runtime

Catches bugs when the code actually executes — especially valuable at boundaries with
external/untrusted data, or with third-party libraries that have weak or missing type stubs
(e.g. `aiokafka`, which ships no stubs at all).

| Tool | What it does | Notes |
|---|---|---|
| `pydantic` | Full runtime validation via `BaseModel` | Already used here through FastAPI (`schemas.py`). The right tool for *external* input — API request bodies, config, anything crossing a trust boundary. Rejects bad data immediately (`422`) instead of letting it flow through as `Any`. |
| `beartype` | Fast, decorator-based runtime type checking for plain functions | Good for *internal* function boundaries where you want an immediate, precisely-worded failure at the call site — regardless of whether the function body would eventually crash usefully, crash confusingly, or (worst case, via duck typing) not crash at all and silently produce wrong output. |
| `jaxtyping` | Shape/dtype-aware type hints for tensors, pairs with `beartype` | For ML/tensor code specifically (e.g. `Float[Tensor, "batch features"]`), not applicable to a web backend like this one. Checks that named dimensions actually agree across arguments. |
| `typeguard` | Older, simpler runtime checker | Generally superseded by `beartype` today — mentioned for completeness, not a strong recommendation. |

## The pattern for containing `Any` at library boundaries

When a third-party library has weak/missing stubs, convert its untyped return value into a
typed shape **once, at the boundary**, so `Any` doesn't spread further into the codebase:

- **`TypedDict` + `cast()`** — for library calls that return plain dicts (e.g. `redis-py`'s
  pub/sub messages).
- **`Protocol` + `cast()`** — for library objects accessed via attributes rather than dict keys
  (e.g. `aiokafka`'s `ConsumerRecord`).

Note that `cast()` itself is purely static — it has zero runtime effect (its actual
implementation is just `return val`). It doesn't guard against the library's shape changing in
a future version; that's what test coverage against the real dependency is for (this repo tests
against real Postgres/Redis/Kafka via testcontainers rather than mocks, for exactly this
reason).

## What this repo already has vs. what could be added

- Already in place: `mypy --strict`, `ruff`, `pydantic` (via FastAPI), and the
  `TypedDict`/`Protocol` + `cast()` pattern at the Redis/Kafka boundaries.
- Not yet added: `pre-commit` (would enforce `ruff`/`mypy` locally on every commit, not just
  when run manually), `beartype` (would be useful if internal function boundaries ever need
  stronger runtime guarantees than static typing alone provides).
