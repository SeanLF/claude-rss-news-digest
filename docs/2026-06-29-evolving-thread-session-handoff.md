# Evolving story-thread — session handoff (2026-06-29)

> **SUPERSEDED for launch/cost/next-steps** by `docs/2026-06-29-evolving-thread-e2e-validation.md`
> (live e2e result, corrected cost ~$0.25/run, turn-key launch runbook) and
> `docs/2026-06-29-thread-relinking-recall-fix.md` (the linker recall fix). The `$0.13/run` and
> "adapt thread_backfill.py" notes below are stale — use `bin/seed-threads`. Kept for history.

Resume point for the evolving-thread feature. Design spec:
`docs/superpowers/specs/2026-06-28-evolving-story-thread-design.md` (A/B/C/D design + gate
results). This doc captures the LATER work (delta rework, deltas-as-memory, deployability) and
what to do next. All work committed on `main`, **unpushed** (≈18 commits ahead of origin).

## What the feature is

An ongoing news story is tracked as a persistent **thread** across daily runs. A returning
reader sees, on a continuing story, an "ONGOING · day N" badge and a summary that is **today's
verified what's-new** (the *delta*) instead of a generic re-description. Everything is behind
flags (`THREADS_ENABLED`, `THREAD_LATEBIND`), both **default off**.

## Current state (all committed)

| piece | what | commit |
|---|---|---|
| A — identity | Haiku semantic linker matches today's selected stories to threads (deterministic token-matching was tried first and FAILED, 1/9; Haiku = 94 threads / 2 over-merges / ~98% sep precision) | `9be1cf5` |
| B — synthesis | per continuing thread, synthesize `whats_new` facts (+ resolved/new questions), per-fact audit→drop | `ae90fa8` |
| C — rendering | badge + delta-as-summary | `d721e7c`, `0725986`, `07a521f` |
| D — late-binding | widen seed cluster to entity-soft neighbourhood (IDF hub-strip; +29% coverage) | `6aae24d` |
| audit health signal | audit fail-open is counted + recorded (`thread_runs`) + alerts | `6183d25` |
| usage attribution | B's Sonnet calls emit `run_usage` rows (`thread_synthesis`/`thread_audit`) | `420caf3` |
| delta rework + memory | **faithful-by-construction delta; deltas-as-memory (narrative dropped)** | `07a521f` |
| integration test | `run._process_story_threads` wired end-to-end with faked LLM | `d7f79bc` |

**Pipeline seam:** `run.py:_process_story_threads()` runs after `archive_run_artifacts`, gated on
`THREADS_ENABLED` + `db.is_recording()`. It reads `clusters.json`+`selected.json`, links threads
(A), synthesizes continuing threads (B, + late-binding if `THREAD_LATEBIND`), records usage +
health, writes `thread_assignments.json`+`thread_installments.json`. Then `write_digest` →
`digest.attach_thread_context` → `render.render_article` applies the badge + delta.

## Key design decisions (DO NOT re-litigate — these were hard-won with Sean)

1. **Matcher = Haiku semantic linker, not deterministic.** Token matching drowns the shared
   signal under facet/article noise + synonyms. Measured + proven on replay. (`threads.link_threads`)
2. **Delta = top verified `whats_new` facts joined — faithful BY CONSTRUCTION.** Earlier tries
   (structured "Now answered/Still tracking" ledger; a separate `whats_new_summary` prose gated by
   an all-or-nothing audit) were **brittle** — the gate blanked the richest threads. The fix:
   build the delta from facts that already each passed the per-fact audit, so there's nothing to
   re-gate; a dropped fact just doesn't appear. The EVOLVE prompt writes each fact as a clean,
   self-contained, most-important-first sentence so the top 3 read as prose. (`threads.delta_from_facts`)
   Replay: 7/7 facts grounded, 0 dropped, reads well.
3. **Memory = recent deltas; the maintained narrative was DROPPED.** It was internal-only
   (readers read past digests) and re-summarizing the whole story daily is the *wrong*, lossy
   memory. The recent deltas ARE what was reported — exactly what "compute what's new" needs.
   Dropped `updated_narrative` (prompt), the `threads.narrative` column, and `set_narrative`. The
   linker matches on the story label (~98% precise on labels alone). (`threads.recent_deltas`)
4. **Graph (D) and memory are orthogonal.** The graph gives breadth (which articles cover the
   story today); memory gives history (what was already reported). The graph is rebuilt from
   today's articles so it CANNOT tell you what's new — memory is still required.
5. **Reader footprint is deliberately small:** badge + delta. No question ledger shown (only
   ~16% of questions resolve in-digest → mostly stale). Ledger kept as internal synthesis state.
6. **Faithfulness discipline everywhere:** per-fact audit→drop; audit fails OPEN but is counted +
   alertable; `apply_installment` is atomic (one `store.transaction()`).

## Deployability (verified 2026-06-29)

**Safe to deploy / safe for any scheduled prod run, because flags are off:**
- `THREADS_ENABLED` + `THREAD_LATEBIND` default false → all thread code is gated and never
  executes → the digest is byte-identical to before.
- The thread migrations are purely additive (`CREATE TABLE IF NOT EXISTS` + `ADD COLUMN`).
- Deploying = a behavioral no-op + empty new tables. Enabling threads is a separate flag flip.

## OPEN — what the next session should weigh / do

### 1. Regenerate thread/delta data locally (needed to experiment further)
The local `data/digest.db` was **re-cloned to clean prod** (runs 1-215, NO thread tables/data).
To see threads/deltas again you must re-apply migrations + backfill:
- `scratch/cluster-replay/thread_backfill.py` replays A+B over archived runs 204-215 into the
  local DB (uses the CURRENT production functions, so it produces the new faithful-by-construction
  deltas). Run in Docker with `-v "$PWD/newsroom/src:/app/src"` + OAuth from 1Password. ~$1.3.
- Then `scratch/cluster-replay/delta_eval.py` shows a synthesized delta + renders a compare.
- Cost of B per run measured ~$0.13 (A linker $0.004 + ~6 threads × $0.021).

### 2. Should we enable the flags in prod? (Sean's product call)
Pros: the delta is a genuinely better returning-reader experience, validated faithful. Cons:
~$0.13/run (~4-6% cost bump); only replay-validated so far (small n); never seen by a subscriber.
Recommendation: do a full live e2e first (below), eyeball several real threads, THEN decide. If
enabling: deploy code, run the backfill against PROD (adapt thread_backfill to the prod DB path),
then set `THREADS_ENABLED=true` (and optionally `THREAD_LATEBIND=true`) via terraform tfvars.

### 3. Full e2e local trial by PULLING LIVE ARTICLES (the missing validation)
Every trial so far used ARCHIVED run-215 data. A true e2e runs the whole pipeline live:
fetch RSS → CLUSTER → SELECT → WRITE → COHERENCE → threads (A+B) → render, with flags ON.
- Caveat: live data has NO thread history → continuations would be thin/zero. So **backfill the
  local DB first** (step 1), then run live so today's stories can continue the backfilled threads.
  (Backfill runs go up to 215; a live run is "today" ≈ run 216+, which links against them.)
- How: `THREADS_ENABLED=true THREAD_LATEBIND=true ... docker compose run --rm digest-newsroom
  .venv/bin/python src/run.py --no-email --force` (recording must be ON for threads to persist —
  do NOT use --dry-run/--no-record). Costs a full curation (~$2.5) + threads (~$0.13). Inspect the
  rendered HTML in `data/output/` (inject CSS for standalone view; see `trial_render.py` pattern)
  and the `thread_runs`/`run_usage` rows.
- This is the definitive confidence check before enabling in prod.

## Gotchas (cost real time this session)
- **`except ValueError, AttributeError:` in threads.py is VALID Python 3.14 (PEP 758)** and
  ruff-formatter-enforced. code-reviewer agents flag it as a "Py2 SyntaxError" EVERY time — it is
  a FALSE POSITIVE. CI is green; do not "fix" it (ruff reverts it anyway).
- **`make db-clone` raw-cats the prod DB over SSH and TRUNCATES to 0 bytes if the transfer fails**
  (e.g. tailscale down). Always verify integrity after: `page_count*page_size == file size` and
  `PRAGMA integrity_check`. Re-run if short. (Backup: `data/digest-211.db`.)
- The digest OAuth token (`NEWS_DIGEST_CLAUDE_OAUTH_TOKEN` in 1Password, item "seanfloyd.dev")
  rate-limits under heavy use; a flurry of replay/backfill calls can exhaust it for a while.
- Scratch harnesses mount `-v "$PWD/newsroom/src:/app/src"` because the image predates the thread
  modules. The digest-newsroom container is one-shot.

## Scratch harness inventory (`scratch/cluster-replay/`, gitignored)
- `thread_backfill.py` — replay A+B into the local DB (regenerate threads/deltas).
- `delta_eval.py` — synthesize one thread, show the delta + faithfulness, render a compare.
- `thread_identity_eval.py` / `thread_linker_haiku.py` / `thread_matcher_variants.py` — A validation.
- `thread_synthesis_eval.py` — B faithfulness on replay.
- `latebind_eval.py` / `latebind_coverage.py` — D (neighbourhood quality + coverage).
- `thread_cost_estimate.py` — real $ cost per run.
- `trial_run.py` (live wiring) / `trial_render.py` (render with backfilled data) — e2e trials.
- `thread_render_preview.py` — render a sample digest for visual QA.
