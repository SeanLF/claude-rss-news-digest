# Dead Code Audit -- newsroom/src/

Audited 2026-04-03. No code was modified.

---

## 1. run.py Mode Map

| Flag | Purpose | Exposed in Makefile? | Verdict |
|---|---|---|---|
| *(no flags)* | Full pipeline: fetch, select, render, email, record | Via `docker compose run --rm digest-newsroom` | Active |
| `--dry-run` | Full pipeline minus email and DB record; truncates to 20 articles | Documented in README, CLAUDE.md, learnings.md | Active |
| `--no-email` | Full pipeline, records to DB, skips email | Referenced in CLAUDE.md, learnings.md, test-watchdog | Active |
| `--no-record` | Full pipeline, sends email, skips DB | Referenced in CLAUDE.md, learnings.md, test-watchdog | Active |
| `--select-only` | Fetches + runs Claude selection, stops before render | Referenced in docs/superpowers plans | Active |
| `--write-only` | Skips fetch/select, re-renders from existing selections.json | Referenced in CLAUDE.md, learnings.md | Active |
| `--send-only` | Sends the latest rendered digest file; useful for retry | Referenced in run.py epilog only | Low use -- no make target, no bin script, no doc reference beyond the argparse help |
| `--force` | Overrides the "run already completed today" guard | Referenced in CLAUDE.md, learnings.md, test-watchdog | Active |
| `--preview` | Opens latest digest HTML in browser (noop in Docker) | No | Low use -- dev convenience, no make target |
| `--test-email EMAIL` | Sends a Resend test email and exits | Referenced in README | Active (testing tool) |
| `--validate` | Fetches all RSS feeds and reports status | No | Active -- wraps feeds_cli; useful operationally |
| `--json` | JSON output modifier for `--validate` | No | Active (modifier for --validate) |
| `--health-check` | Calls Claude with "respond with 'ok'", checks auth | No | Active (operational check) |
| `--limit N` | Limits article count (like dry-run truncation but with recording) | No | Active -- used in bin/test-watchdog |
| `--model MODEL` | Overrides Claude model | No | Active -- used in bin/test-watchdog and test_prompt.py |

**Note on `--send-only`:** This mode exists for retry-after-failure scenarios but has no make target, no bin script wrapper, and no mention in CLAUDE.md or learnings.md. It is the only mode with no external reference beyond its own argparse help text. Low-risk to document; low-priority to remove since retry scenarios are real.

**Bug in test-watchdog:** The echo on line 55 of `bin/test-watchdog` says `--article-limit 5` but the actual docker run command on line 64 correctly uses `--limit 5`. The argparse flag is `--limit`; there is no `--article-limit` flag. The echo is misleading but harmless.

---

## 2. Potentially Dead Files

### `newsroom/src/feeds_cli.py`

**What it does:** Feed validation CLI -- fetches all RSS sources, reports article counts, date ranges, persistent failures. Has both a human-readable and JSON output mode.

**Evidence of use:**
- Imported in `run.py` line 40: `from feeds_cli import validate_feeds_cli`
- Called via `run.py --validate` and `run.py --validate --json`
- Has a `__main__` block for direct invocation

**Verdict:** Active. Not dead. The file is correctly isolated from runtime code (no runtime imports), which is the intended design per its own docstring. Keep as-is.

---

### `newsroom/src/test_prompt.py`

**What it does:** Full prompt experimentation platform -- snapshot management, running Claude on historical inputs, comparing runs with TF-IDF overlap scoring.

**Evidence of use:**
- Called by `bin/test-prompt`, which is invoked by `make prompt NAME=<name>`
- Referenced extensively in `.claude/learnings.md` with actual invocation examples
- 31.4 KB -- the largest file in newsroom/src/

**Evidence of issues:**
1. **Stale `--context headlines` path:** `prepare_context()` at line 334 writes `recent_headlines.csv` to the snapshot dir. This was the old dedup mechanism, replaced by `recent_rss_titles.csv` + the RECAP subagent. The `--context headlines` flag in `bin/test-prompt` injects this stale file. The production pipeline no longer reads `recent_headlines.csv`. Using `--context headlines` in experiments would add a file that subagents ignore.

2. **Duplicate TF-IDF implementation:** `test_prompt.py` contains its own `STOPWORDS`, `tokenize()`, and `TfidfMatcher` class (lines 40-261), copied verbatim from an older version of `run.py`. The production implementation now lives in `dedup.py`. The `test_prompt.py` copy is slightly older -- fewer stopwords (missing contractions like `don`, `didn`, etc. that `dedup.py` includes). These serve different purposes (prompt comparison vs dedup), but the duplication is notable.

3. **Syntax error in `list_runs()`:** Line 602 uses bare `except json.JSONDecodeError, KeyError, FileNotFoundError:` which is Python 2 syntax. In Python 3 this should be `except (json.JSONDecodeError, KeyError, FileNotFoundError):`. This would cause a `SyntaxError` if `list_runs()` is called. The `cmd_runs` subcommand is broken.

**Verdict:** Active tool, but has real bugs. The `list_runs` syntax error means `bin/test-prompt runs` crashes. The stale `--context headlines` mechanism produces misleading experiment results.

---

### `newsroom/src/claude_cli.py`

**What it does:** Reusable wrapper around `claude --print` with sync and async APIs.

**Evidence of use:**
- `claude.py` imports `run_sync` and `stream_sync` (the sync API)
- The async API (`run`, `stream`) is never imported anywhere in the codebase; it exists only in the docstring example comment at lines 7-8

**Verdict:** Partially active. Sync API (`run_sync`, `stream_sync`) is the live production path. Async API (`run`, `stream`) is dead code -- ~90 lines that go unused. It was likely written speculatively for a future web server use case.

---

### `newsroom/src/dedup.py`

**What it does:** TF-IDF matcher for headline deduplication.

**Evidence of use:**
- Imported in `prepare.py` line 24: `from dedup import TfidfMatcher`
- Imported in `newsroom/tests/test_run.py` line 12: `from dedup import TfidfMatcher, tokenize`

**Verdict:** Active. Not dead.

---

## 3. Stale Code Findings

### No TODO/FIXME markers found
Running `grep -rn "TODO|FIXME|HACK|XXX|deprecated|unused|dead"` across all `newsroom/src/*.py` returned zero matches. The codebase is comment-clean.

### Commented-out blocks
None found. No multi-line commented code blocks in any source file.

### Duplicate STOPWORDS / TF-IDF implementations
Two copies of essentially the same TF-IDF logic exist:

| File | Lines | Purpose |
|---|---|---|
| `dedup.py` | 1-216 | Production dedup (more complete stopwords) |
| `test_prompt.py` | 40-261 | Prompt comparison scoring (older, fewer stopwords) |

The `test_prompt.py` version is missing ~25 stopwords that `dedup.py` has (contractions, prepositions). This divergence is a latent correctness issue in experiment comparisons.

### `prepare_context()` in test_prompt.py (stale path)
Lines 334-345 in `test_prompt.py` write `recent_headlines.csv` as an optional experiment context. This file is no longer part of the production pipeline (replaced by `recent_rss_titles.csv` since 2026-02-06 per MEMORY.md). The `--context headlines` option in `bin/test-prompt` silently injects a file that subagents do not read.

### Async API in claude_cli.py (dead code)
Lines 176-269 (`run()` and `stream()` async functions) are never imported or called. They add ~90 lines of untested dead weight.

---

## 4. Quick Wins List

Ranked by safety and impact (safest + highest impact first).

### 1. Fix `list_runs()` Python 2 syntax in test_prompt.py (HIGH IMPACT, SAFE)
**File:** `/Users/sean/Developer/news-digest/newsroom/src/test_prompt.py`, line 602
**Issue:** `except json.JSONDecodeError, KeyError, FileNotFoundError:` is Python 2 syntax; crashes in Python 3.
**Fix:** Change to `except (json.JSONDecodeError, KeyError, FileNotFoundError):`.
**Risk:** Zero -- it is a pure syntax correction. `bin/test-prompt runs` is currently broken.

### 2. Fix misleading echo in test-watchdog (LOW IMPACT, TRIVIAL)
**File:** `/Users/sean/Developer/news-digest/bin/test-watchdog`, line 55
**Issue:** Echo says `--article-limit 5` but the flag is `--limit 5`. Misleading in logs.
**Fix:** Change echo to say `--limit 5`.
**Risk:** Zero -- documentation-only.

### 3. Remove async API from claude_cli.py (MEDIUM IMPACT, SAFE)
**File:** `/Users/sean/Developer/news-digest/newsroom/src/claude_cli.py`, lines 176-269
**Issue:** `run()` and `stream()` async functions are never imported or used. They also pull `asyncio` into the import.
**Fix:** Delete lines 176-269 and the `from collections.abc import AsyncGenerator` import (line 18 -- also unused once async code is gone).
**Risk:** Low -- no imports in any production file. Verify with grep before deleting.

### 4. Fix or remove `--context headlines` in test_prompt.py (MEDIUM IMPACT, SAFE)
**File:** `/Users/sean/Developer/news-digest/newsroom/src/test_prompt.py`, lines 313-345
**Issue:** `prepare_context()` writes `recent_headlines.csv`, which the production pipeline no longer reads. Experiments using `--context headlines` are silently broken.
**Options:**
  - (a) Remove `prepare_context()` and the `--context` flag entirely if no longer used in experiments.
  - (b) Update `prepare_context()` to write `recent_rss_titles.csv` in the format that RECAP subagent reads, which would make the flag meaningful again.
**Risk:** Low -- only affects `bin/test-prompt`, not the production pipeline.

### 5. Consolidate TF-IDF implementation (LOW IMPACT, MEDIUM EFFORT)
**Issue:** `test_prompt.py` has its own older copy of `STOPWORDS` / `tokenize()` / `TfidfMatcher`.
**Fix:** Import from `dedup.py` instead. The signatures are identical. The comparison scoring in `test_prompt.py` would benefit from the more complete stopword list.
**Caveat:** `test_prompt.py` runs in Docker with `sys.path` manipulation -- confirm `dedup.py` is on the path before switching. It is (`newsroom/src/` is on path).
**Risk:** Low -- purely additive stopword change, small improvement to comparison accuracy.

### 6. Document `--send-only` in CLAUDE.md (NO CODE CHANGE, TRIVIAL)
**Issue:** `--send-only` is the only mode with no external reference. It is a useful retry mechanism after broadcast failure.
**Fix:** Add a one-liner to CLAUDE.md's mode list. No code change needed.
**Risk:** Zero.

---

## Summary

| Finding | File | Severity |
|---|---|---|
| Python 2 syntax bug -- `list_runs()` crashes | test_prompt.py:602 | Bug |
| Stale `--context headlines` mechanism | test_prompt.py:334-345 | Correctness |
| Async API dead code | claude_cli.py:176-269 | Cleanup |
| Duplicate TF-IDF with older stopwords | test_prompt.py:40-261 | Minor divergence |
| Misleading echo in test-watchdog | bin/test-watchdog:55 | Cosmetic |
| `--send-only` undocumented | run.py | Documentation gap |

No source files are safe to delete outright. All files in `newsroom/src/` are either imported by `run.py` or by tests. `test_prompt.py` is the only file with active bugs.
