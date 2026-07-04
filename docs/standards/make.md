# Make / Makefile standards reference (news-digest — the repo's single front door)

> As of 2026-07, verify before relying: the `just`/`mise` status and "modern norm" claims below were web-checked mid-2026 but re-check tool releases and GNU Make behaviour before pinning or asserting.

Scope: the central `Makefile` is this repo's **single front door** — `make ci`, `make test`, `make deploy`, `make migrate`, `make db-clone`, etc. It is a **task launcher, not a compilation build-graph**: targets shell out to `docker compose` / `bin/` scripts, they don't produce the files they're named after. Read that framing (section 4) before "improving" it into a real build graph.

## 1. Safety / correctness header (put at the very top)

- `SHELL := bash` — the default `/bin/sh` (dash/BusyBox on CI images) has no `pipefail`; force bash so recipes get the same safety baseline as `bin/` scripts (see [bash.md](bash.md) §1).
- `.SHELLFLAGS := -euo pipefail -c` — `-e` exit on error, `-u` unset-var is an error, `-o pipefail` a pipeline fails if any stage fails. **Keep the trailing `-c`** — it's how Make hands the recipe to the shell; drop it and every recipe breaks.
- `.DELETE_ON_ERROR:` — if a recipe fails, delete the (real) target file it was building, so a re-run never picks up a half-written/corrupt artifact. Harmless on phony targets; keep it on as a blanket safety net.
- `.ONESHELL:` — run each recipe as **one** shell invocation instead of one shell per line. Needed for any multi-line recipe that carries state (a `cd`, a shell `var=`, a loop, an `if`). Caveat: with `.ONESHELL` + `-e`, an early line failing aborts the whole recipe (usually what you want); and `@`/`-` line prefixes only apply to the first line.
- Without `.ONESHELL`, **each line is a fresh shell** — the classic state-loss footgun (section 5).

## 2. Target hygiene

- `.PHONY:` **every** non-file target (`ci test deploy help …`). Phony = "always run, never a filename". Skip it and a stray file named `test` in the repo root makes `make test` silently no-op ("`test` is up to date"). In a task-runner Makefile, essentially every target is phony.
- `.DEFAULT_GOAL := help` — bare `make` should print help, not fire the first target (which might be `deploy`). Alternatively put `help` first; `.DEFAULT_GOAL` is explicit and reorder-proof.
- Self-documenting `help` target — annotate targets with `## comment` and grep them out, so help never drifts from reality:
  ```make
  help: ## Show this help
  	@grep -hE '^[a-zA-Z0-9_-]+:.*?##' $(MAKEFILE_LIST) \
  	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-18s\033[0m %s\n",$$1,$$2}'
  ```
  Then `deploy: ## Full deploy pipeline` documents itself. (Note the `$$1` — escaping awk's `$1`, section 3.)
- **Order-only prerequisites** (`target: normal-deps | order-only-deps`): the part after `|` must exist but its timestamp doesn't retrigger the target. Real use here is a directory guard — `foo: | data/` with a `data/:` mkdir rule — so `data/`'s mtime doesn't rebuild everything. Mostly a build-graph tool; rarely needed in a pure task-runner.
- Group related targets and keep names verb-ish and predictable (`migrate`, `migrate-status`) so `make <tab>` completion is legible.

## 3. Variables & the tab footgun

- `?=` sets **only if not already set** — use for anything a caller/env may override: `REGISTRY ?= ghcr.io/...`, `COMPOSE ?= docker compose`. Lets `make deploy REGISTRY=…` or an env var win without editing the file.
- `:=` **simple/immediate** expansion (evaluated once, now) vs `=` **recursive** (re-evaluated on every use). Default to `:=` — predictable, and avoids re-running a `:= $(shell …)` on every reference. Use `=` only when you deliberately want late/deferred evaluation.
- `SHA := $(shell git rev-parse --short HEAD)` runs the shell **at parse time** for every `make` invocation — fine, but know it runs even for `make help`; guard expensive `$(shell …)` behind `=` or a target if it matters.
- **`$$` escapes a literal `$`** for the shell: `echo $$HOME`, `for f in $$(ls)`, awk's `$$1`. A single `$` is Make's expansion — `$HOME` becomes `$H` + `OME`. This is the #2 confusion after tabs.
- **Recipe lines must start with a real TAB, not spaces** — the original, still-unfixed Make footgun. A space-indented recipe fails with `*** missing separator. Stop.` Configure the editor to keep hard tabs in Makefiles (`.editorconfig`: `[Makefile] indent_style = tab`). This tab-only rule is the single biggest reason people reach for `just`/`mise` (section 4).

## 4. Framing: this repo uses Make as a **task-runner**, not a build graph

Make's original job is a **dependency DAG** on files — rebuild `x.o` when `x.c` is newer. This repo uses **none** of that: targets are verbs (`ci`, `deploy`, `migrate`) that orchestrate Docker/`bin/` scripts and produce no target file. That's a legitimate, extremely common use — but it means the tab/`.PHONY`/`$$` ceremony above is pure overhead we pay for a job Make wasn't built for. Purpose-built command-runners exist as of 2026:

- **`just`** — the default modern command runner. Make-like `justfile` syntax **without** the tab-only rule, without `.PHONY` (every recipe is a command, never a file), clean built-in argument passing, cross-platform (Linux/macOS/Windows). No `$$` escaping surprises. Prefer it for a **greenfield** task-runner, or if the tab/phony footguns keep biting.
- **`mise` tasks** — bundles **tool-version pinning + env management + a parallel task runner** in one tool (successor to asdf). Its edge: a task automatically inherits the project's pinned tool versions and env vars when it runs. Prefer it if the repo **already uses `mise`** to manage tool versions, so tasks and toolchain live in one `mise.toml`.

**When to prefer them:** greenfield or a rewrite where the runner is pure command orchestration → reach for `just` (or `mise` if you already pin tools with it). **Stay on Make here** — the existing `Makefile` works, is universally installed with zero bootstrap, and everyone/CI already knows `make ci`. Don't churn a working front door for syntax taste; the migration cost outweighs the footgun savings on an established file.

## 5. Common pitfalls / anti-patterns

- **Shell-per-line state loss** — without `.ONESHELL`, `cd foo` on one line does **not** persist to the next; `x=1` is gone next line. Chain with `&&`/`;` on one logical line, or enable `.ONESHELL`. Silent, confusing failures otherwise.
- **Unguarded phony targets** — forgetting `.PHONY` (section 2): a matching filename makes the target a no-op. In a task-runner where nothing is a real file, treat missing `.PHONY` as a bug.
- **Recursive-make sprawl** — `$(MAKE) -C subdir` fan-out ("Recursive Make Considered Harmful"): fragments the dependency picture, breaks `-j` parallelism, duplicates variables. Here it'd just be indirection over `docker compose`; keep the Makefile **flat** and let the container/`bin/` script own the real work.
- **Non-idempotent targets** — a task-runner target should be safe to re-run. `migrate` must be re-run-safe (yoyo tracks applied migrations), `deploy` should verify-then-switch (see the zero-downtime rule). A target that corrupts state on a second run is a latent outage.
- **Silent shell-per-line failure** — without the section-1 header, a failing middle line in a non-`.ONESHELL` recipe can be masked (only the last line's exit status counts in some setups; pipes hide failures without `pipefail`). The `.SHELLFLAGS` header is what makes recipe failures loud — matches this repo's "no silent failures" rule.
- **`$(shell …)` for control flow** — running shell at parse time to branch Make logic is fragile; push conditionals into the recipe (real shell, with `.ONESHELL`) or into a `bin/` script.
- **Overriding built-in vars by accident** — `SHELL`, `MAKEFLAGS`, `CURDIR` are special; know before reusing the name. And remember env vars leak in unless you `:=` pin them — `make CC=…` and `$CC` from the environment can both surprise you.

## References

- Web-checked mid-2026: `just` remains the default modern command runner; `mise` bundles tool-versioning + tasks; the `SHELL`/`.SHELLFLAGS`/`.DELETE_ON_ERROR`/`.ONESHELL` preamble is the consensus "strict-mode Makefile" header.
- Related repo standards: [bash.md](bash.md) (the `-euo pipefail` baseline recipes inherit), [docker.md](docker.md) (what most targets actually shell out to).
