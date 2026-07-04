# Rust standards reference (news-digest / `circulation`)

> As of 2026-07, verify before relying: versions and "current" claims below were web-checked mid-2026 but the ecosystem moves every 6 weeks. Re-check `cargo`/crate releases before pinning or asserting.

Scope: the `circulation/` Axum + tokio web server (SQLite via rusqlite, clippy `-D warnings` + rustfmt enforced in `bin/ci`). This repo is already on `edition = "2024"`, `axum = "0.8.8"`, `tokio = "1"`.

## 1. Toolchain + Edition 2024 (mid-2026)

- **Stable ~1.95** (beta 1.96), 6-week cadence. No repo-pinned `rust-toolchain.toml` — CI floats to installed stable; pin only if a build breaks.
- **Edition 2024** shipped in **Rust 1.85 (Feb 2025)**; now the mainstream default. Editions are per-crate opt-in, fully interoperable — no ecosystem split.
- Edition 2024 highlights: **async closures** (`async || {}` + `AsyncFn/AsyncFnMut/AsyncFnOnce`); `Future`/`IntoFuture` in the prelude; RPIT lifetime-capture changes (`impl Trait` now captures all in-scope lifetimes — use `+ use<>` to opt out); stricter `if let`/tail-expr temporary drop scopes; **`unsafe extern` blocks** + `unsafe` attributes; `env::set_var`/`remove_var` now `unsafe`; Cargo resolver respects `rust-version`; rustfmt **style editions**.
- **Async landscape**: `tokio` 1.x (stable, no 2.0 on the horizon) is the default runtime; `axum` **0.8.x** is current stable (0.9 unreleased on main — don't target it). `axum` 0.8 uses `{param}` path syntax (not `:param`) and native async-trait handlers.
- **async fn in traits**: stable since 1.75 for static dispatch. Still no built-in `dyn` support — use `#[trait_variant]`/`dynosaur` or the `async-trait` crate only when you need trait objects.

## 2. Philosophy / idioms

- Lean on ownership + borrowing: prefer `&T`/`&str`/`&[T]` params, return owned. Don't `.clone()` to dodge the borrow checker — restructure.
- **Newtypes for invariants**: wrap primitives (`struct ArticleId(String)`, `Slug`, `Lang`) so illegal values can't be constructed; validate in the constructor.
- **Make illegal states unrepresentable**: model with `enum`s + data-carrying variants instead of bool flags / `Option` soup; exhaustive `match` (avoid catch-all `_` when adding a variant should force a compile error).
- **`?` + typed errors** over `match`-and-return ladders; propagate, convert at boundaries via `From`.
- **Iterators over index loops**: `map`/`filter`/`collect`/`fold`; avoid `for i in 0..len` indexing.
- **`impl Trait`** in arg and return position for zero-cost abstraction; box (`Box<dyn ...>`) only when you need heterogeneity or object safety.
- Prefer `&str` params, `String` fields; borrow at call sites (`AsRef<str>`/`impl Into<String>`) where it reads clearly.

## 3. Defaults worth adopting

- **Errors**: apps/binaries → `anyhow` or `eyre` (context via `.context(...)`, `?` everywhere). Library-ish/shared modules with a stable API → `thiserror` enums. `circulation` is an app: `anyhow` at handler edges is fine; give recurring domain failures a real type.
- **Axum**: use typed extractors (`Path<T>`, `Query<T>`, `State<T>`, `Json<T>`) — don't hand-parse. Return `impl IntoResponse`; map errors to an `IntoResponse` error type (don't `unwrap` in handlers → that aborts the worker). Share state via `State`, not globals.
- **rusqlite**: keep it off the async executor — wrap blocking DB calls in `tokio::task::spawn_blocking` (or a sync pool). rusqlite is synchronous; calling it directly in an async handler blocks the runtime.
- **Clippy**: CI runs `-D warnings`. Consider `#![warn(clippy::pedantic)]` per-crate and `#[allow]` the noisy few (`module_name_repetitions`, `must_use_candidate`) rather than all-or-nothing. `clippy::unwrap_used`/`expect_used` as warn is a good prod guard.
- **rustfmt**: default config, no bikeshedding; CI enforces.
- **`#[must_use]`** on functions returning a value that's a bug to ignore (builders, `Result`-likes, pure computations).
- **Modules**: prefer `foo.rs` + `foo/` (2018+ style), no `mod.rs`. Keep `main.rs` thin; route constants centralized (this repo uses a `routes` module).
- **`unsafe`**: essentially none in this codebase — keep it that way. If unavoidable, isolate + `// SAFETY:` comment justifying each invariant.
- Derive liberally: `Debug`, `Clone`, `serde::{Serialize,Deserialize}`, `PartialEq/Eq/Hash` where cheap.
- **The 2026 async-native default stack is axum + tokio + `sqlx` + `tracing`** (sqlx = compile-time-checked queries against a real DB). This repo's rusqlite + `spawn_blocking` is a **deliberate choice** (embedded SQLite, no async pool needed, one fewer moving part), not a dated one — don't "modernize" it to sqlx reflexively.

## 3a. Dev workflow / tooling

- **`tracing`** (+ `tracing-subscriber`, optionally `tracing-opentelemetry`) is the observability standard for an axum + tokio server — structured, span-based, async-aware; prefer it over `log`/`println!` for request-path instrumentation. `#[tracing::instrument]` on handlers; `RUST_LOG`/`EnvFilter` for levels.
- **`cargo-nextest`** — faster, better-isolated test runner (per-test process, cleaner output). The repo's `bin/ci` uses bare `cargo test`; nextest is a drop-in upgrade.
- **`cargo-audit` + `cargo-deny`** — supply-chain guards. `cargo-audit` scans for RUSTSEC advisories; **the repo's `make ci-full` already runs it**. `cargo-deny` adds license/duplicate/source policy on top — worth adding if dependency governance tightens.
- **`cargo-machete`** — finds unused dependencies (trims `Cargo.toml` bloat).
- **`bacon`** — background watch loop (check/clippy/test on save) for fast local feedback.

## 4. Notable last-~2-years changes (verified)

- **Edition 2024** (1.85, Feb 2025) — see §1.
- **async fn in traits** + **return-position `impl Trait` in traits** stable (1.75, Dec 2023).
- **async closures** stable (1.85).
- **`let ... else`** stable (1.65) — use for early-return guards instead of nested `if let`.
- **`gen` blocks / generators**: reserved keyword in 2024; `gen {}` iterator blocks still nightly/in-progress mid-2026 — don't rely on them in stable.
- Ongoing: better `dyn` for async traits, precise capturing (`use<>`) — track before adopting bleeding-edge patterns.

## 5. Pitfalls / anti-patterns to avoid

- **`unwrap()`/`expect()`/`panic!` in request paths** — a panic aborts the tokio worker (this repo builds with `panic = "abort"`). Return `Result`; reserve `expect` for genuinely-unreachable startup invariants with a message.
- **Blocking in async**: sync SQLite, `std::fs`, `reqwest::blocking`, or CPU loops on the async executor stall all tasks. Use `spawn_blocking` / async I/O.
- **Over-cloning / `.to_string()` spam** to appease the borrow checker — usually a design smell; borrow or restructure lifetimes.
- **Stringly-typed data**: raw `String` for ids/langs/slugs/states — use newtypes/enums so the compiler catches misuse.
- **`.clone()` on `Arc` misunderstood**: cloning `Arc` is cheap (refcount) and correct for sharing state across handlers — that's fine; cloning the inner data is the waste to watch.
- Swallowing errors (`let _ = ...`, `.ok()`, empty `Err(_) =>`) — matches the repo's silent-failure rule; log + propagate.
- `#[allow(clippy::all)]` blanket suppressions — allow the specific lint with a reason.
- Holding a `MutexGuard`/`RwLockGuard` across an `.await` — deadlock/starvation risk; drop before awaiting or use `tokio::sync` locks deliberately.
- Unbounded channels / unbounded concurrency against external services — bound them.

Sources: [releases.rs](https://releases.rs/), [Rust 1.85 / Edition 2024 announcement](https://blog.rust-lang.org/2025/02/20/Rust-1.85.0/), [async fn + RPIT in traits](https://blog.rust-lang.org/2023/12/21/async-fn-rpit-in-traits/), [axum releases](https://github.com/tokio-rs/axum/releases).
