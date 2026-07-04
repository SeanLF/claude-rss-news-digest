# Python standards (mid-2026)

> As of 2026-07, verify before relying: versions and tool status move fast — confirm with `uv python list`, `ruff version`, release notes before acting on a specific number.

This repo: `newsroom/` runs on `python:3.14-slim`, `requires-python = ">=3.14"`, managed by uv, linted/formatted by ruff, typed by mypy, tested by pytest, plus `claude-agent-sdk` + asyncio.

## 1. Version & toolchain landscape

- **CPython 3.14 is the current stable line** (released Oct 2025; 3.14.x patch releases through 2026). 3.13 is still fully supported; 3.15 is in development. This repo targets **3.14**.
- **Free-threading (no-GIL)**: as of 3.14 the free-threaded build is **officially supported, no longer experimental** (PEP 779) — but it is a *separate* build/ABI (`python3.14t`), and the default interpreter still ships with the GIL enabled. Free-threaded gives ~2-4x on multi-core CPU-bound work but ~5-10% single-thread + memory overhead. Roadmap: GIL becomes a runtime flag, default-on, over the next 2-3 releases. **Don't assume the prod image is free-threaded** — `python:3.14-slim` is the GIL build.
- **JIT**: experimental in 3.13/3.14 (opt-in build flag, not on by default). **3.15 is where it gets real** — materially improved and worth tracking as a genuine speedup, not just a tech preview; still verify per-workload before relying on it in prod.
- **uv (Astral) is the default installer/resolver/project manager.** Replaces pip + venv + virtualenv + pip-tools + pipx + poetry + pyenv in one Rust binary; 10-100x faster than pip. Uses `pyproject.toml` + `uv.lock` (commit the lock). Common: `uv sync`, `uv run`, `uv add`, `uv sync --extra eval`.
- **ruff (Astral) is the default linter + formatter.** One binary replacing flake8 + black + isort + pyupgrade + ~15 others. Config lives in `[tool.ruff]` in `pyproject.toml`.
- **Type checkers (3-way in 2026):**
  - **mypy** — the mature baseline; `--strict` is the correct production bar (FastAPI/Pydantic/SQLAlchemy use it). This repo uses mypy.
  - **pyright** (Microsoft) — 2-5x faster, ~98% spec conformance; best correctness/speed ratio, strong LSP; good default for VS Code.
  - **ty** (Astral, now under OpenAI) — Rust, 10-100x faster on cold checks, gradual-guarantee design, but **still beta** with lower spec conformance. Watch it; don't switch the repo off mypy for it yet without a reason.
- **Astral acquisition (March 2026) covers all of Astral — uv + ruff + ty, not just ty.** The whole toolchain (installer/resolver, linter/formatter, type checker) now sits under one model vendor (OpenAI). This *sharpens* the repo's uv/ruff supply-chain concern rather than isolating it to a beta type checker: pin/lockfile discipline and canary-per-version apply to the entire chain.

## 2. Prevailing philosophy / idioms

- **Modern typing (PEP 695, stable since 3.12):** use `def first[T](xs: list[T]) -> T` and `class Box[T]:` inline generics; declare aliases with the `type` statement: `type ArticleId = str`. Avoid the old `TypeVar`/`Generic`/`TypeAlias` boilerplate in new code.
- Prefer **`X | None`** over `Optional[X]`, **`list[str]` / `dict[str, int]`** over `typing.List`/`Dict`. Builtins-as-generics are standard since 3.9.
- **Structural typing** via `typing.Protocol` for duck-typed interfaces; reserve ABCs for real inheritance hierarchies.
- **`from __future__ import annotations`** is no longer needed for the common cases on 3.14 — annotations are lazily evaluated (PEP 649/749). Use `typing.get_type_hints()` / `annotationlib` if you need to resolve them at runtime.
- **Dataclasses** are the default for plain data (`@dataclass(slots=True, frozen=True)` when it fits). Reach for **Pydantic** only when you need validation/parsing (this repo validates JSON via `jsonschema`, not Pydantic). `attrs` is fine but not needed here.
- **pathlib over os.path** (ruff `PTH` is enabled). `Path(...) / "sub"`, `.read_text()`, `.exists()`.
- **f-strings** for all formatting; never `%` or `.format()` in new code. **t-strings** (PEP 750, new in 3.14) return a `Template` for safe interpolation (logging, HTML, SQL) — useful but niche; know they exist.
- **Comprehensions** for map/filter that stays readable; drop to explicit loops when there's side-effecting or multi-step logic. Generators for streaming/large data.
- `match`/`case` (structural pattern matching) is idiomatic for tagged-union dispatch.

## 3. Best practices worth defaulting to

- **Packaging = PEP 621 `[project]` table in `pyproject.toml`** (already the layout here). One file is the source of truth for deps, ruff, mypy, pytest config.
- **src / package layout** keeps import paths honest (this repo uses `newsroom/src/`).
- **Type public APIs.** Annotate function signatures and module-level constants; internal locals can rely on inference. Run mypy in CI (`make ci`).
- **asyncio structured concurrency:** prefer **`asyncio.TaskGroup`** over bare `asyncio.gather` — a failing child cancels siblings and errors surface as an `ExceptionGroup` (catch with `except*`). Use **`asyncio.timeout()`** context manager for deadlines rather than hand-rolled `wait_for` wrapping. `asyncio.Runner` / `asyncio.run()` for entry points.
- **Reliability libs over hand-rolled:** `tenacity` for retries, stdlib `asyncio.timeout` for deadlines (matches this repo's canary-workaround rule).
- **`logging` over `print`** in library/pipeline code; structured/leveled, configurable. `print` only for genuine CLI stdout.
- Ruff config to default to (this repo already does): select `E,W,F,I,B,C4,UP,SIM,PTH,RUF`, let the formatter own line length, `target-version` matched to `requires-python`.
- Commit **`uv.lock`**; pin `requires-python`; keep `target-version` in ruff and `python_version` in mypy in sync with the runtime image.
- Prefer `subprocess.run([...], check=True)` (list args, no `shell=True`); `bandit` (in dev deps) flags the unsafe patterns.
- **Testing plugins for an asyncio-heavy codebase:** `pytest-asyncio` (or `anyio`'s pytest plugin) for `async def` tests; `hypothesis` for property-based tests on parsers/dedup/ID-resolution; `pytest-cov` for coverage.

## 4. Notable changes, last ~2 years

- **PEP 695** inline generics + `type` aliases (3.12) — now the norm.
- **PEP 649/749** deferred annotation evaluation (3.14) — annotations no longer eagerly evaluated; `from __future__ import annotations` largely obsolete.
- **PEP 750 t-strings** (3.14) — template-string literals.
- **PEP 779** free-threading officially supported (3.14).
- **Improved error messages + color tracebacks** (3.13/3.14); a new **REPL** (3.13).
- **`asyncio` introspection** (3.14): `python -m asyncio ps <PID>` / `pstree`, call-graph capture — debug hung tasks in prod without code changes.
- Ecosystem: uv + ruff went from "new" to **default**; ty entered the type-checker field; black/flake8/isort/pip effectively legacy for greenfield.

### Trajectory (forward-looking)

- **CPython 3.15 ships Oct 1 2026** (feature freeze now, in beta). What's landing:
  - **PEP 810 explicit lazy imports** — a `lazy` soft keyword deferring module load until first use; reported ~50-70% faster startup on import-heavy programs. Directly relevant to this per-run subagent pipeline (short-lived processes paying import cost every invocation).
  - **PEP 686 UTF-8 mode on by default** — UTF-8 for file/stdio/pipe encoding regardless of locale; opt out with `PYTHONUTF8=0`. Mostly a Windows concern; harmless on the Linux prod image.
  - **Materially improved JIT** — see §1; 3.15 is the inflection point.
  - **Free-threaded stable ABI** — the no-GIL build gets a stable ABI, easing C-extension support (still a separate build; default stays GIL).

## 5. Pitfalls / anti-patterns to avoid

- **Don't assume no-GIL.** `python:3.14-slim` is the GIL build; threads still don't parallelize CPU-bound Python there. Use processes / async I/O accordingly.
- **Mutable default args** (`def f(x=[])`) — still a classic footgun; use `None` + assign inside.
- **Bare `except:` / `except Exception: pass`** — silent failure; this repo actively hunts these (`silent-failure-hunter`). Catch narrowly, log, re-raise or handle deliberately.
- **`asyncio.gather` without `return_exceptions` awareness** — one failure leaves siblings running and can swallow errors; prefer `TaskGroup`.
- **Blocking calls inside async** (sync HTTP, `time.sleep`, heavy CPU) freeze the event loop — offload with `asyncio.to_thread` / executors.
- **`os.path` string-munging** where `pathlib` is clearer (ruff `PTH`).
- **`typing.List`/`Optional`/`Union`** in new code — use builtins and `|`.
- **`shell=True` / f-string-built shell commands** — injection risk; pass arg lists.
- **Not committing `uv.lock`** or letting ruff/mypy/runtime Python versions drift apart.
- **Over-reaching `# type: ignore`** without a code — `warn_unused_ignores` (on here) will flag stale ones; scope ignores to a specific error code.
- **Catching `ExceptionGroup` with plain `except`** — use `except*` when consuming `TaskGroup` failures.
