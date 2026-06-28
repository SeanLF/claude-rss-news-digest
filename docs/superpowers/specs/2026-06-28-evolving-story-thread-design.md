# Evolving story-thread — design spec (2026-06-28)

Make the digest a *living* product: an ongoing story is tracked as a persistent thread across
days, framed as "what's new today" against the running narrative, with an open-question ledger
that raises questions and marks them resolved as the story develops. Replaces today's
disconnected daily snapshots of the same story.

Validated in PoC (`scratch/cluster-replay/evolving_thread.py`; see
`docs/2026-06-28-synthesis-forward-ideas-pocs.md` and `docs/2026-06-27-graph-synthesis-direction.md`):
on a 4-day Iran thread (runs 204–207) it carried the narrative forward and resolved 13 open
questions — categorically better than 4 disconnected summaries. Faithfulness was the catch
(22.5% unsupported when memory bled into "today's facts") and the fix is proven: **wall MEMORY
(running narrative + ledger, may reference prior days) from FACTS (`whats_new`, must cite a
TODAY source)** → 8.4%, with the residual mopped up by the existing audit→drop (COHERENCE) layer.

## Decomposition (build order)

Built as independent spec→plan→build cycles. Each is testable on the replay DB (runs 207–215
have `clusters.json` + `selected.json` + `selections.json` archived in `run_artifacts`).

```
A. Thread substrate    →   B. Threaded synthesis   →   C. Thread rendering
   (identity + state)        (delta + ledger +           (email/web treatment)
   THIS SPEC                  audit-drop)
                                  ↑
                        D. Late-binding subgraph (optional enhancer to B's input)
```

- **A — Thread substrate** (this spec): detect that today's selected story continues an existing
  thread; persist thread identity + carried state (running narrative + open-question ledger).
  Deterministic Python, no LLM. The foundation B and C consume.
- **B — Threaded synthesis + ledger**: the EVOLVE prompt with the MEMORY/FACTS wall, producing
  the daily installment (`whats_new` / `resolved` / `new_questions` / `still_open` /
  `updated_narrative`); audit→drop wired to COHERENCE. Fills the state A persists.
- **C — Thread rendering**: surface continuity + what's-new + still-open ledger in the email/web
  without bloating the compact format.
- **D — Late-binding subgraph** (later): replace "use the existing hard cluster as the story's
  article set" with the entity-soft neighbourhood that pulls the full story across cluster
  boundaries (+16–20pp coverage length-controlled in PoC). Enhancer to B's input; B works without it.

Everything ships behind a flag (`THREADS_ENABLED`, default off) until C makes it reader-visible.

---

# Sub-project A — Thread substrate (identity + persistence)

## Goal

Given today's **selected** stories (the clusters that made `must_know`/`should_know`), for each
one determine whether it continues an existing thread, assign a stable `thread_id`, persist the
thread, and expose the thread's carried state (narrative + open questions) for B. Pure
infrastructure — no reader-facing change yet.

## The hard problem: thread identity

The PoC cheated (`"iran" in story`). Production needs "is today's story a continuation of an
existing thread?" across days, where stories split, merge, and get re-labelled. The candidate set
is *small* (a handful of active threads, not 700 articles), so the matching problem is far easier
than clustering — a coarse signal suffices because we match one selected story against a few
active threads, not partition everything.

**Signal**: each selected story carries a CLUSTER `story` label (a discriminative sentence).
Match today's labels against the active threads and decide, per story, continue-or-new.

**Matcher choice — measured, then escalated (deterministic FAILED; the LLM linker WON).** Per
"measure before tuning," a deterministic token matcher was built and validated on the replay
first (`thread_matcher_variants.py`, runs 207–215): **it does not work.** Bag-of-tokens
Jaccard/overlap on labels+titles caught only 1 of ~9 obvious multi-day threads — the shared
signal (e.g. "heatwave", "Europe") drowns under facet-specific and article-specific tokens, and
synonyms/rewording ("Swiss"≠"Switzerland", "Europe"≠"European", "Zelensky"≠"Zelenskyy", daily
re-labelling) never match. Every variant either left the obvious European-heatwave thread at 7→7
separate threads, or — pushed low enough to merge it — started fusing unrelated stories
(over-merges 4–8). Thread identity is a **semantic** judgment, not a lexical one.

So A escalates to a **cheap Haiku semantic linker** (`thread_linker_haiku.py`, validated on the
same replay): each run, show Haiku the active threads (id + one-line description) and today's
selected story labels; it maps each story to a thread id or NEW. The candidate set is small
(~16 stories × ~38 active threads), so one Haiku call/run is cheap and architecturally
consistent (the pipeline already runs Haiku for RECAP/COHERENCE; no heavy local model, unlike
MiniLM at 642MB on the 4GB CX23). **Measured result: 94 threads, 2 over-merges (~98% separation
precision); every unambiguous multi-day story collapses correctly (heatwave 7→1, Starmer 6→1,
Lebanon 6→1, Venezuela 4→1, Ebola/Colombia 3→1, Hormuz 2→1); 18 coherent multi-day threads.**
It even makes the *correct* sub-story splits — "Iran" fragments into diplomacy / Hormuz shipping /
military strikes (distinct ongoing stories sharing an entity), which deterministic matching
cannot do. On LLM/parse failure the linker returns all-NEW (no threading that run) so the digest
never crashes and recovers next run.

## Components

### Schema (new migration, `migrations/<ts>_add_threads.sql`)

```sql
CREATE TABLE IF NOT EXISTS threads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT,                              -- human-readable, e.g. "iran-nuclear-deal"
    label TEXT NOT NULL,                    -- latest story label (shown to the linker next run)
    narrative TEXT,                        -- running summary (filled by sub-project B)
    status TEXT NOT NULL DEFAULT 'active', -- active | dormant | closed
    first_run_id INTEGER,
    last_run_id INTEGER,
    created_at DATETIME DEFAULT (datetime('now','utc')),
    updated_at DATETIME DEFAULT (datetime('now','utc')),
    FOREIGN KEY (first_run_id) REFERENCES digest_runs(id),
    FOREIGN KEY (last_run_id) REFERENCES digest_runs(id)
);
CREATE INDEX IF NOT EXISTS idx_threads_status ON threads(status);

CREATE TABLE IF NOT EXISTS thread_questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id INTEGER NOT NULL,
    question TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',    -- open | resolved
    raised_run_id INTEGER,
    resolved_run_id INTEGER,
    resolved_how TEXT,
    created_at DATETIME DEFAULT (datetime('now','utc')),
    FOREIGN KEY (thread_id) REFERENCES threads(id)
);
CREATE INDEX IF NOT EXISTS idx_thread_questions_thread ON thread_questions(thread_id, status);

CREATE TABLE IF NOT EXISTS thread_installments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id INTEGER NOT NULL,
    run_id INTEGER NOT NULL,
    cluster_story TEXT,                     -- the cluster label that matched this run
    matched_score REAL,                    -- Jaccard at match time (NULL for new threads)
    created_at DATETIME DEFAULT (datetime('now','utc')),
    FOREIGN KEY (thread_id) REFERENCES threads(id),
    FOREIGN KEY (run_id) REFERENCES digest_runs(id)
);
CREATE INDEX IF NOT EXISTS idx_thread_installments_run ON thread_installments(run_id);
```

`narrative` and `thread_questions` are written by B; A creates the tables and the
identity/installment rows. `thread_installments` is the per-run audit trail of what matched what.

### Module `newsroom/src/threads.py`

A thin store + an injectable LLM linker; no global state beyond a passed connection.

- `link_threads(active, today_labels, *, model) -> list[int | None]` — one Haiku call mapping each
  of today's labels to an active `thread_id` (continuation) or `None` (new). Returns all-`None`
  on any LLM/parse failure so the digest proceeds (no threading that run) rather than crashing.
  Injectable so unit tests pass a deterministic fake (no LLM in CI).
- `resolve_threads(selected_stories, run_id, store, *, dormant_after, linker=link_threads) -> list[ThreadAssignment]`
  — decay threads not seen for `dormant_after` runs to `status='dormant'` (excluded from matching),
  load the active set, call the linker, then per story create-or-continue, record an installment,
  and touch `last_run_id`. Returns assignments carrying `(thread_id, is_new, prior_narrative,
  open_questions)` for B.
- Store helpers (thin SQL wrappers): `active_threads(...)`, `create_thread(...)`,
  `touch_thread(...)`, `record_installment(...)`, `decay_threads(...)`, plus the narrative/ledger
  seam B writes through (`open_questions(thread_id)`, `set_narrative(...)`, `add_questions(...)`,
  `resolve_question(...)`).

`ActiveThread` (what the linker sees: id + label + narrative) and `ThreadAssignment` (the
interface B depends on) are small dataclasses.

### Integration seam (run.py)

A deterministic call between `generate_selections()` (`run.py:395`) and `assemble_selections()`
(`run.py:400`), gated on `THREADS_ENABLED`. Reads `selected.json` + `clusters.json` from
`CLAUDE_INPUT_DIR`, resolves selected cluster labels + article titles, calls
`resolve_threads(...)`, writes `thread_assignments.json` to `claude_input/` for B (and for
inspection). Recording (the thread store writes) is gated like other DB writes — skipped under
`--dry-run`/`--no-record` (reuse the existing `skip_record` derivation, `run.py:273-275`). On
`--dry-run` the matching still runs (so it's observable) but does not persist.

## Failure handling

- Thread matching must NEVER crash the digest. Wrap `resolve_threads` so any exception logs and
  falls through to "no threads this run" — the digest proceeds exactly as today. (Mirrors
  `merge._load_cluster_map` best-effort pattern.)
- Empty/odd signatures (story label missing, no titles) → new thread, low score, logged.
- Over-merge guardrail: B's `coherent_event=false` / audit→drop is the safety net if A links two
  stories that aren't really the same — synthesis refuses to fabricate a join (proven in PoC).

## Validation gate (offline, on replay DB — "good enough" bar)

Harness `scratch/cluster-replay/thread_identity_eval.py`: replay selected stories for runs 207→215
in order through `resolve_threads`, then check against a hand-labelled continuity key:

1. **Continuity recall**: known multi-day stories (Iran, and any other story spanning ≥2 of
   207–215) collapse to ONE stable `thread_id` across their days.
2. **Separation precision**: distinct stories never share a `thread_id`; no thread accretes
   unrelated stories (spot-check each thread's installment labels read coherently).
3. Report continuity-recall / separation-precision; high separation precision is the priority — a
   wrongly-merged thread is worse than a missed continuation, since B's audit can't fix a bad
   join's narrative, only its facts.

**Gate result (PASSED):** the Haiku linker scored 94 threads / 2 over-merges (~98% separation
precision), every unambiguous multi-day story collapsed to one thread, and Iran correctly split
into diplomacy / Hormuz / strikes. The deterministic baseline FAILED the gate (1/9 threads) and
is not used. Unit tests (TDD) cover the store CRUD, create-or-continue, aging, and the
`resolve_threads` orchestration with an injected fake linker; the replay harness
(`thread_linker_haiku.py`) is the live integration gate.

## Out of scope for A (handled by later sub-projects)

- Producing narrative / ledger content (B).
- Late-binding article subgraph (D) — A matches on the existing selected cluster.
- Any reader-facing rendering (C).
