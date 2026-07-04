# Productionizing extract→join as the CLUSTER stage — build plan (2026-07-01)

## Why (quality, not cost)
Judged on REAL output, the holistic Sonnet CLUSTER stage is competent but has one recurring,
reader-visible defect: it **over-splits big stories**, and SELECT surfaces the pieces as
near-duplicates (published digest today: Venezuela ×2 in must_know; gate digests: SCOTUS ×2,
heatwave ×2). Extract→join produces cleaner, more distinct, less repetitive digests (fewer
`internal_dups` all 3 gate days; visibly broader spread), and is **deterministic** (no
day-to-day cluster drift). Justification is the combination — fewer dups + determinism +
reusable structured `{entities, event}` tags that can later sharpen the threading stage — NOT
cost. Cost is a bonus. Validated equivalent-or-better by the 3-day task-grounded gate
(`docs/2026-07-01-graph-gate-preregistration.md`).

## Scope (deliberately minimal + reversible)
`_STAGES[0]` (cluster) becomes extract→join outright — **no runtime flag** (Sean: "don't need
flags, just ship it; we can revert to previous image"). SELECT/WRITE/COHERENCE/threads unchanged
— they consume `clusters.json` in the identical schema regardless of how it's produced.
**Rollback = revert the image / git revert.**

- **Routing:** `orchestrate_selections` special-cases the `cluster` label to call
  `run_extractjoin_stage` unconditionally; the old `cluster.md` agent is no longer invoked.
- **Extraction model:** `CLUSTER_EXTRACT_MODEL` (default `claude-sonnet-4-6` — the A/B showed
  Sonnet extraction keeps source-diversity flat vs Haiku's ~10% dip, still cuts dups). A tuning
  knob, not a rollout flag; Haiku remains selectable.
- **Join:** scikit-learn (TF-IDF + agglomerative, threshold 0.80). Added to prod deps (Sean OK'd;
  ~40MB, far lighter than the ONNX deps the 4GB box already avoids — verify RSS in a dry run).

## Files
- NEW `newsroom/src/cluster_extractjoin.py`:
  - `build_extract_prompt(batch, arts)` / `parse_extract_items(text)` — pure, unit-testable
    (ported from the validated `scratch/cluster-replay/extract_tags.py`).
  - `join_tags(article_ids, tags, threshold=0.80) -> list[cluster]` — pure (given tags), the
    deterministic join (ported from `join_eval.build_docs` + agglomerative). Unit-testable
    without any model call; this is the TDD core.
  - `async run_extractjoin_stage(claude_input_dir, *, model, cwd) -> usage_row` — reads
    articles_*.csv, runs batched extraction via `claude_cli.run_agent`, joins, writes
    clusters.json, returns a `run_usage`-compatible row (so per-stage cost still records).
- EDIT `newsroom/src/orchestrate.py`: in `orchestrate_selections`, when `config.CLUSTER_MODE ==
  "extractjoin"` and `label == "cluster"`, call `run_extractjoin_stage` instead of `run_stage`;
  validate with the existing `validate_clusters`; append its usage row. Everything else identical.
- EDIT `newsroom/src/config.py`: add `CLUSTER_MODE`, `CLUSTER_EXTRACT_MODEL`, `CLUSTER_JOIN_THRESHOLD`.
- EDIT `newsroom/pyproject.toml`: add `scikit-learn`.
- NEW `newsroom/tests/test_cluster_extractjoin.py`.

## TDD sequence
1. **Join (pure, no SDK) FIRST** — the deterministic core:
   - all-distinct tags → all singletons (no false merges);
   - identical tags for two articles → one cluster (merges same-story);
   - degenerate guard: empty tags → singleton, never one blob;
   - output schema `{"clusters":[{"story","article_ids"}]}` + every article appears exactly once
     (the invariant SELECT depends on);
   - threshold monotonicity (higher threshold ≥ as many clusters).
2. **Extraction parsing (pure)** — `parse_extract_items` tolerates fenced/prose-wrapped JSON,
   drops unknown article_ids, title-only fallback keeps 100% coverage.
3. **Stage integration** — mock `claude_cli.run_agent` to return canned tags; assert
   `run_extractjoin_stage` writes schema-valid clusters.json, `validate_clusters` passes, usage
   row shape matches `run_stage`'s.
4. **Flag routing** — `orchestrate_selections` with `CLUSTER_MODE=extractjoin` calls the new path
   for the cluster label and the agent path for the rest (mock both).

## Validation before it's "done"
- `make test` green.
- A real `--no-email --no-record --force` dry run with `CLUSTER_MODE=extractjoin`: confirm
  clusters.json is produced, the full chain completes, and the digest renders. Inspect the
  actual clusters + digest by eye (over-split? junk clusters? the Venezuela-double gone?).
- Check container RSS with sklearn imported (4GB box headroom).
- Flag stays `agent` in prod config; flip is a separate, explicit deploy decision.

## Explicitly NOT in scope (holding the line — no flip-flop)
- Time-decay join, persistent networkx article-graph — deferred; threads.py already provides
  cross-day persistence, and the gate cleared WITHOUT time-decay.
- Any change to SELECT/WRITE/COHERENCE/threads.
- Flipping the flag on in prod (that's Sean's call after the dry-run inspection).
