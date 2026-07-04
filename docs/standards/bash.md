# Bash / shell scripting standards reference (news-digest / `bin/` + Makefile)

> As of 2026-07, verify before relying: versions and "current" claims below were web-checked mid-2026 but re-check `shellcheck`/`shfmt` releases and the target shell's actual version before pinning or asserting.

Scope: the `bin/` scripts (`deploy`, `ci`, `ssh`, `db-clone`, `migrate`, etc.) and the Docker-driven Makefile. These are glue/ops scripts — fetch, ssh, orchestrate `docker compose`. Keep them small; the real logic lives in `newsroom/` (Python) and `circulation/` (Rust).

> **Make-alternatives (2026):** the Makefile here is a command runner, not a build DAG. If it ever gets rewritten, the modern task runners are **`just`** (Make-like recipe syntax without the tab-sensitivity, implicit-`.PHONY`, and `$$`-escaping footguns) and **`mise` tasks** (task runner bundled with the tool-version manager). Not a reason to migrate a working Makefile — but the default for a *new* command runner.

## 1. Safety baseline

- Start every script: `#!/usr/bin/env bash` then `set -euo pipefail`.
  - `-e` exit on error, `-u` error on unset var, `-o pipefail` a pipeline fails if any stage does (not just the last).
- `IFS=$'\n\t'` only if you rely on newline/tab splitting; otherwise leave IFS alone and quote everything (quoting is the real fix, not IFS games).
- `trap 'rm -rf "$tmpdir"' EXIT` for cleanup — runs on normal exit, error, and signals. Set it right after you create the temp resource, not later.
- **`set -e` has sharp edges — know them:** it does NOT trigger inside `if`/`while`/`&&`/`||` conditions, in a function called in such a context, or on the left of a pipe (without `pipefail`). `local x=$(cmd)` masks `cmd`'s exit status (the `local` succeeds) — split into `local x; x=$(cmd)`. `set -e` is a backstop, not a substitute for checking exit codes on commands that matter.
- Fail loud: send errors to stderr (`echo "..." >&2`) and `exit 1`; don't let a failed `ssh`/`docker` silently continue (matches this repo's "no silent failures" rule).

## 2. Quoting + word-splitting

- **Always** quote expansions: `"$var"`, `"$(cmd)"`, `"${arr[@]}"`. Unquoted `$var` word-splits and glob-expands — the #1 source of shell bugs.
- Build command args as **arrays**, never a space-joined string: `args=(--rm --volume "$vol:/data"); docker run "${args[@]}"`. A string breaks on any path with spaces and can't carry empty args.
- `"${arr[@]}"` (each element a word) vs `"${arr[*]}"` (one joined string) — you almost always want `[@]`.
- Never parse `ls`; use globs (`for f in ./*.json`) or `find ... -print0`. Filenames can contain spaces, newlines, and glob chars.
- Default/guard: `"${VAR:-default}"`, `"${VAR:?must be set}"` — cleaner than manual `-z` checks.

## 3. Portability

- **macOS ships bash 3.2** (frozen at the last GPLv2 release; Apple won't ship GPLv3). Stock interactive shell is **zsh** since Catalina (2019). Linux/CI containers run **bash 5.x**. So a script that works in your terminal via `brew`'s bash 5 can break on a bare macOS or a minimal container.
- **`#!/usr/bin/env bash`** (not `/bin/bash`) so it picks up brew's bash 5 when present — but don't *rely* on 5-only features unless every target has them.
  - bash-4+ only (absent on stock macOS): `mapfile`/`readarray`, associative arrays (`declare -A`), `${var,,}`/`${var^^}` case conversion, `&>>`, `;;&`. If you need these, either mandate bash 5 explicitly or drop to Python.
- **POSIX `sh` vs bash:** `#!/bin/sh` runs dash/BusyBox on many Linux/Docker images — no `[[ ]]`, no arrays, no `local` (technically), no `$'...'`. If you write bashisms, use a bash shebang; don't write `#!/bin/sh` and then use `[[`.
- **GNU vs BSD coreutils** (a real macOS-vs-Linux trap this repo hits): `sed -i` needs an arg on BSD (`sed -i ''`) but not GNU; `date`, `readlink -f`, `grep -P`, `stat`, `xargs -r` all differ. On this Mac, gnubin is on PATH (`sed`/`find`/`grep`→GNU-ish, `ggrep`/`gsed` for the real thing) but **CI containers use whatever the base image ships** — write to the common subset or gate on `uname`.
- `grep` here is a shell function → ugrep; scripts should call `command grep`/`rg`/`ggrep` explicitly so they don't inherit interactive aliasing.

## 4. Tooling (gate these in `bin/ci`)

- **ShellCheck** (current: **v0.11.0**, binaries dated Jan 2026) — static analysis; catches quoting, unset vars, `set -e` foot-guns, useless `cat`. Run with all checks; treat findings as errors in CI. Fix or explicitly `# shellcheck disable=SCxxxx` with a reason. shellcheck.net mirrors latest git if you want a quick paste-check. **`hadolint`** wraps ShellCheck to lint the shell inside Dockerfile `RUN` bodies — so the same checks cover the repo's Dockerfiles, not just `bin/`.
- **shfmt** — the `gofmt` of shell; formatting only (pairs with shellcheck's correctness). Repo convention: `shfmt -i 2 -ci -s` (2-space indent, indent switch cases, simplify). Run `-l -d` in CI to fail on unformatted, `-w` to fix.
- **`bash -n script`** = free syntax check (no execution) as a first gate.
- Non-trivial scripts with branching → **Bats** for tests. But by the time you want Bats, ask whether it should be Python (see §6).

## 5. Idioms

- `[[ ... ]]` over `[ ... ]` — no word-splitting inside, supports `&&`/`||`/`=~`/`<`. (`[[` is bashism; fine here, we target bash.)
- `$(cmd)` over backticks — nestable, readable.
- Arithmetic: `(( x++ ))`, `$(( a + b ))` — not `expr`.
- `mktemp` / `mktemp -d` for temp files+dirs; pair with a `trap ... EXIT` cleanup. Never hardcode `/tmp/foo`.
- `printf '%s\n' "$x"` over `echo` for anything with backslashes, leading `-`, or variable content — `echo`'s behaviour varies across shells/flags.
- `find ... -print0 | xargs -0` (or `find -exec ... +`) for filename-safe iteration; plain `find | xargs` breaks on spaces.
- Functions with `local` vars; `return` codes small, output on stdout. Group related scripts' shared helpers in `bin/lib/` (already present here).
- `readonly`/`declare -r` for constants; `${var//find/replace}` for substitution over piping to `sed` for simple cases.

## 6. Anti-patterns + when to STOP and use Python

- Anti-patterns: unquoted expansions; parsing `ls`/`find` output line-by-line; `cmd; if [ $? ...]` (test the command directly); UUOC (`cat file | grep`); building SQL/JSON by string-mashing (use `jq`, or Python); silent `|| true` that swallows real failures; growing a pipeline of `sed`/`awk`/`cut` you can't read next week.
- **Stop writing bash and reach for Python (`newsroom/`) when any of these is true:**
  - You're **looping over structured data** (rows, JSON, records) or need **data structures** (maps, nested lists) — `declare -A` in bash is a portability + readability dead end.
  - **Non-trivial arithmetic / floats** — bash is integer-only; `bc`/`awk` shims are a smell.
  - **JSON/CSV parsing** beyond a single `jq` filter, or any API response handling.
  - **Error handling that needs to distinguish cases**, retry with backoff, or clean rollback (this repo already prefers `tenacity` in Python over hand-rolled shell retry loops).
  - The script is **past ~50 lines**, or you've written a second function, or you reached for `set -e` foot-gun workarounds — that's the signal it wants real types, tests, and a debugger.
- Good bash stays as a **thin orchestration layer**: parse a couple of flags, set env, invoke `docker compose` / `ssh` / `make`, check the exit code, clean up. Everything with logic belongs in Python or Rust.
- **The modern "this outgrew bash" answer is a PEP 723 single-file `uv run` script** — not a new bash function and not a full `newsroom/` module. Shebang `#!/usr/bin/env -S uv run --script` plus an inline `# /// script` block declaring `requires-python` and `dependencies` gives you one self-contained executable file: uv auto-installs the right interpreter and the pinned deps into an ephemeral env on first run — no `venv`, no `requirements.txt`, no `pip install` step. This is the sweet spot for a `bin/` helper that needs one library (an HTTP call, JSON shaping, a bit of logic) but doesn't warrant living in the package.

## Sources

- [koalaman/shellcheck](https://github.com/koalaman/shellcheck) — static analysis tool, all-checks recommendation
- [Microsoft Engineering Playbook — Bash Code Reviews](https://microsoft.github.io/code-with-engineering-playbook/code-reviews/recipes/bash/) — shellcheck + `shfmt -i 2 -ci -s` as pre-commit gate
- [ECMWF IFS Shell Standard — shellcheck & shfmt](https://sites.ecmwf.int/docs/ifs-arpege-coding-standards/shell/guidelines/tools.html)
- [Demystifying Bash and Zsh on Mac](https://dev.to/spencerlepine/demystifying-bash-and-zsh-on-mac-4dgc) — zsh default since Catalina, bash for portable scripting
