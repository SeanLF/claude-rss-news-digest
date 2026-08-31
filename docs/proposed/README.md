# Rescued from gitignored scratch/ — 2026-08-30

Deliverables from the 2026-08-30 measurement session produced under the **gitignored** `scratch/`
tree, which would otherwise be lost. Unapplied unless a section says otherwise. Full context:
`docs/2026-08-30-health-check-and-clustering-sota.md`.

## ~~`repair_index_merge.diff` + `test_repair_multifield_collapse.py`~~ — APPLIED, files removed

Both landed in the repair commit on 2026-08-31. `_merge_repaired_by_article_ids` is in
`newsroom/src/repair.py` and the cases are in `newsroom/tests/test_repair.py`. The two files here
were deleted so nobody re-applies a diff already in the tree.

One correction to what they claimed: the verdict path is NOT left last-wins. Contradictory
verdicts delete the key, so the story falls through the missing-verdict path.

## `2026-08-30-prompt-audit-report.md` + `.diff`

Prompt audit per the `claude-api` skill's method. **The diff does NOT apply as one patch** — 27
hunks across 11 files, and `.claude/agents/coherence.md` is patched twice, each hunk written
against the pristine file, so the second collides. The five `newsroom/src` patches apply under
`--3way`. Apply per-file and sequenced.

Its two load-bearing findings are already summarised in the main doc: the global
`thinking: disabled` rests on a void premise, and two COHERENCE blind spots (0/27 across every
model) trace to *"the single least-supported specific"* being stated twice against *"every
specific"* once.

## `coherence-planted/` — the only non-circular COHERENCE labels that scale past 45 items

Rescued 2026-08-31. Every "hand adjudication" in the junk-citation work was model-generated
(`docs/2026-08-30-health-check-and-clustering-sota.md`, "THE LABELS ARE NOT GROUND TRUTH"). These
survive that finding: a planted fabrication is true by CONSTRUCTION, not by a rater's opinion.

- `planted274_key.json`, `planted278_key.json` — 8 hard positives + 24 clean fields each. Each
  plant is verified absent from or contradicted by that story's own cited sources.
- `build_planted.py`, `build_planted274.py` — the reproducers. Each plant is an explicit
  `(idx, field, old, new, type, why)` tuple.
- `pooled.py` — the pooled-recall scorer used for the held-out sets.
- `lang.py` + `rel.py` + `rows.json`, `rows_mmnilm.json` — reproducer for the non-English cut rate
  41.0% -> 3.3%, with the module and inputs it resolves relative to its own directory. Runs as
  `python3 docs/proposed/coherence-planted/lang.py`.
- `pooled.py`, `build_planted*.py` hardcode `scratch/` paths and do NOT run from here as-is; they
  are kept as the record of method, not as runnable scripts.

**Recall only.** The 48 `clean_fields` are model-passed, not human-cleared, so the false-positive
rate computed against them is an upper bound. `planted278_key.json`'s own `_doc` says so. Anything
optimising against these must not treat the clean side as ground truth.
