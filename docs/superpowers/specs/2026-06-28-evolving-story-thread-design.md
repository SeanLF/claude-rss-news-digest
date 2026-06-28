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

**Signal**: each selected story carries a CLUSTER `story` label (a discriminative sentence) plus
its articles' `original_title`s. Derive a normalized **entity/keyword signature** (set of salient
tokens) and match today's signature against each active thread's signature by **Jaccard overlap**;
best match above a tuned threshold continues that thread, else start a new one.

**Matcher choice — measure before adding an LLM.** Start deterministic (capitalized-token /
salient-noun extraction + normalization from the story label + titles). Validate on replay; the
prior-art finding (entity-bag + time = 92 BCubed F1, zero LLM) says cheap entity signals are
strong for news. Only escalate to a cheap Haiku entity extraction if deterministic under-links
true continuations or over-merges distinct stories beyond the gate below. Haiku tags for runs
204–207 (`scratch/cluster-replay/drafts/tags_haiku_*.json`) are the benchmark for that comparison.

## Components

### Schema (new migration, `migrations/<ts>_add_threads.sql`)

```sql
CREATE TABLE IF NOT EXISTS threads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT,                              -- human-readable, e.g. "iran-nuclear-deal"
    signature TEXT NOT NULL,               -- JSON array of normalized signature tokens
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

Pure functions + a thin store; no global state coupling beyond a passed connection.

- `extract_signature(story_label: str, titles: list[str]) -> set[str]` — deterministic normalized
  salient-token set (lowercase, stopword/boilerplate-stripped, capitalized-entity-biased).
- `jaccard(a: set, b: set) -> float`.
- `match_thread(signature, active_threads, threshold) -> (thread_id | None, score)` — best Jaccard
  match above threshold, else `(None, best_score)`.
- `resolve_threads(selected_stories, run_id, conn, *, threshold, dormant_after) -> list[ThreadAssignment]`
  — for each selected story: extract signature, match-or-create, record installment, touch
  `last_run_id`; age threads not seen for `dormant_after` runs to `status='dormant'` (excluded from
  matching). Returns assignments carrying `(thread_id, is_new, prior_narrative, open_questions, score)`
  for B to consume.
- Store helpers (thin SQL wrappers): `active_threads(conn)`, `create_thread(...)`,
  `record_installment(...)`, `touch_thread(...)`, `decay_threads(...)`, plus question read/write
  used by B (`open_questions(thread_id)`, `add_questions(...)`, `resolve_question(...)`).

`ThreadAssignment` is a small dataclass (the interface B depends on).

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
3. Report per-threshold continuity-recall / separation-precision; pick the threshold that maxes
   both; document the deterministic-vs-Haiku-signature comparison.

Ship A when the matcher hits the gate on replay (high separation precision is the priority — a
wrongly-merged thread is worse than a missed continuation, since B's audit can't fix a bad join's
narrative, only its facts). Unit tests (TDD) cover `extract_signature`, `jaccard`, `match_thread`,
match-or-create, and aging; the eval harness is the integration gate.

## Out of scope for A (handled by later sub-projects)

- Producing narrative / ledger content (B).
- Late-binding article subgraph (D) — A matches on the existing selected cluster.
- Any reader-facing rendering (C).
