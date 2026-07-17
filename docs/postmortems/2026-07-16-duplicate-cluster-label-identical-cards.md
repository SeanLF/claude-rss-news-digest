# Post-mortem: 2026-07-16 duplicate-cluster-label identical cards

**Status:** resolved. Fixed test-first, reviewed (adversarial ×2, silent-failure-hunter, simplifier, code-reviewer), full CI green. Not yet deployed; today's email is unrecoverable (see Impact).
**Severity:** medium. One delivered digest led with two near-identical top cards; no missed send, no data loss.

## Summary

The 2026-07-16 10:25 UTC digest delivered normally to 11 recipients, but its **top two "Must Know" cards rendered with a word-for-word identical summary** and the same `Ongoing · day 23` badge. Card 1 ("US-Iran war enters fifth day…", 16 sources) and card 2 ("Iran signals diplomatic opening…", 3 sources) differed only in headline and *why it matters*; the body paragraph was a copy.

The interesting part is not the two cards; it is the **class** of bug. The clustering stage labels each cluster with a value (`story`, the modal `primary_event`) that is **not guaranteed unique**, and the entire pipeline downstream treats that label as a **unique story identity** (`cluster_id`, thread linking, the digest's by-story thread lookup). Two clusters collided on the label, and a best-effort enrichment layer, the evolving-story-thread delta (which **replaces the summary**), was handed to both, collapsing two distinct stories into one rendered card. A non-unique key used as an identity key, silently colliding, reaching the reader.

## Impact

- One daily digest (run 235) delivered with a duplicated lead story to 11 recipients at ~10:45 UTC.
- **Email is unrecoverable:** already delivered, can't be recalled.
- The web archive (`/issues/2026-07-16`) shows the same defect and *is* re-renderable (see "Fixing today's run").
- No data loss, no missed send; the other four Must Know and all Should Know stories were correct.
- Underlying data was fine: `selections.json` held two *distinct* WRITE summaries (a CENTCOM-strikes card and a Qatar-diplomacy card). The corruption was purely at render time, where the thread layer overwrote both with one narrative.

## Timeline (UTC, 2026-07-16)

- **10:25:06** - scheduled run 235 starts; 621 articles kept.
- CLUSTER (extract→join) partitions into 249 clusters. Two of them, a 58-article US-Iran war cluster and a lone article ("Iran launches strikes on Gulf, even as FM visits Qatar", A22), independently take the **identical** modal label `"US-Iran military strikes escalation July 2026"`.
- SELECT picks both as separate Must Know stories (it sees two clusters; the shared label is invisible to it).
- Thread linking creates **two** thread rows for that one label (thread 6 continuing, thread 284 new).
- WRITE authors two distinct summaries; COHERENCE passes them.
- **10:45:18** - render: `attach_thread_context` keys the thread lookup on `cluster_id`; both cards share it, both resolve to thread 6, both summaries are replaced by the same delta. Digest saved (72,978 bytes, the week's largest, bloated by the duplicated lead) and broadcast to 11 contacts. Run 235 completes "successfully."
- **later** - user reports "article 1 and 2 almost identical"; root cause traced from the archived DB plus the run's intermediate artifacts on the prod volume.

## Root cause

`cluster_extractjoin.join_tags` derives each cluster's `story` from `Counter(primary_event).most_common(1)`, the **modal** event phrase across the cluster's members. The join separates clusters on the *full* tag bag (entities×3 + keywords + primary_event×2); `story` is a lossy derivative of that decision. Two tag-disjoint clusters can therefore land the **same** modal label. The function guarantees "every article in exactly one cluster" but has **no `story`-uniqueness invariant**, yet three downstream consumers silently assume one:

1. `merge._attach_cluster_id` sets `cluster_id = story`.
2. `threads.link_threads` matches on the label string.
3. `digest.attach_thread_context` builds `by_story = {a["story"]: a …}` (a dict that silently collapses on collision) and looks up each selection's thread **by `cluster_id`**, then the delta **replaces the summary**.

So one label collision cascaded: double-selection, a spurious second thread, and, fatally, both cards resolving to the same thread delta.

## The design questions this raised

**1. When the join and the label disagree, which wins?**
The adversarial review's sharpest point: the join clusters on the rich tag bag; the label is downstream and lossy. "Trust the label and merge" is backwards as a general rule, because it overrides the richer decision with the poorer one. This is not academic: the temporal-kernel path (`join_tags` with `published`/`sigma_hours`) *deliberately* separates same-tag recurring events (distinct daily installments) that legitimately share a modal label. Blindly folding same-label clusters would undo that precision, and did, breaking a time-decay test on the first attempt. Resolution: fold only *strays* (≤2 articles) into a same-label anchor, and only on the non-temporal (prod) path; leave substantial collisions to the render-layer guard.

**2. Should a best-effort enrichment layer be able to overwrite load-bearing content?**
The thread delta *replacing* the summary is the mechanism that turned "two similar cards" into "two identical cards." An additive, best-effort layer silently overwrote the primary field on a collision. The fix is not to remove the feature but to make the overwrite **refuse to fire ambiguously**: if two selections share a `cluster_id`, skip enrichment for both (keep their distinct WRITE summaries) and log loudly. Degrade the garnish, never the story, and never by dropping the item. (An earlier plan to *drop* a duplicate selection was rejected: it could empty `must_know` and hard-abort, the exact failure mode of the 2026-07-11 outage.)

## Fixes

Test-first, two complementary layers plus one sibling hardening surfaced by the sweep:

1. **Layer 1, fold strays at the source** (`cluster_extractjoin.py`). `_merge_same_story` folds any ≤2-article cluster into the largest same-`story` cluster, non-temporal path only. Fixes the common fragment case (A22 folds into the Iran cluster, one card). Two *substantial* same-label clusters are left separate by design.
2. **Layer 2, guard the collapse point** (`digest.py`, load-bearing, not belt-and-suspenders). `_shared_cluster_ids` flags any `cluster_id` carried by 2+ selections; `attach_thread_context` skips thread enrichment for them (logs at `error`, non-fatal) so they keep distinct summaries. Catches *both* triggers: a residual duplicate label, and the coarse `_attach_cluster_id` "first source that maps" heuristic assigning one `cluster_id` to two distinct stories.
3. **Sibling hardening, the faithfulness audit** (`thread_synthesis.py`). A same-class bug found while sweeping: `audit_whats_new` keyed on an LLM-supplied verdict `id` and silently defaulted any claim without a matching id to `supported=True`, so a 0-indexed, string, or duplicate id set could pass *every* fact unaudited, and it wasn't counted as a health failure. Now it requires an explicit verdict per claim 1..N and **raises** on a mismatch, routing into the caller's existing fail-open-**but-counted** path (its own documented contract). The anti-fabrication net now fails loud, not silent.

## Fixing today's run

- **Email:** delivered; unrecoverable.
- **Web archive:** re-renderable. Verified on the real run-235 artifacts that Layer 2 skips enrichment for both Iran cards, so a prod `--write-only` re-render (after deploy) would show two *distinct* Iran summaries instead of identical text. This is the *partial* fix: two distinct cards, not the ideal single merged card, because Layer 1 can't retroactively un-split a selection already baked into `selections.json`. Whether to re-render one day's archive is a judgment call (forward-only precedent exists); the email being out already caps the value.

## What went well

- The bug was reproduced **cheaply and offline**: the real run-235 intermediate files replayed through the fix with zero API calls, proving both the collision and the fix against production data.
- Underlying curation was never lost: WRITE's two distinct summaries survived in `selections.json`, so the defect was render-only and fully diagnosable after the fact.
- The design was pressure-tested by adversarial review *before* implementation, which caught two real flaws (the drop-selection hard-abort risk, and "trust the label" breaking time-decay) that would otherwise have shipped.

## What went wrong

- A derived, non-unique value was used as a unique identity key across three consumers, none of which validated the assumption, and the collision path was **silent** (a dict comprehension that drops on collision, an enrichment that overwrites without complaint).
- The defect shipped because the run **"succeeded"**: every stage completed, COHERENCE passed (it checks each card against its own sources, not cross-card duplication), and nothing downstream asserts that two cards aren't the same story. The pipeline had no "are these two cards actually distinct?" check.

## Action items

**Done (this incident):**
- [x] Layer 1: strays fold into a same-label anchor (non-temporal path), restoring label uniqueness for the fragment case.
- [x] Layer 2: render-layer guard skips thread enrichment on any shared `cluster_id`; keeps distinct summaries, never drops an item, logs at `error`.
- [x] Sibling: `audit_whats_new` requires an explicit verdict per claim and fails loud (counted) instead of silently passing unaudited facts.
- [x] Each fix driven test-first; both layers re-validated against the real run-235 data.

**Recommended (open):**
- [ ] Deploy the fix so the next scheduled run is correct; decide whether to `--write-only` re-render the 2026-07-16 archive.
- [ ] Treat `story`/`cluster_id` as a **non-unique namespace** explicitly. The guard is the enforcement, but a lightweight invariant or assertion (or a schema note) would stop a future consumer re-introducing the "labels are unique" assumption.
- [ ] Consider a cheap cross-card distinctness check at assembly (warn when two selected cards share a cluster_id or have near-identical summaries) so a duplicate lead can't ship "successfully" again.

## Lessons

- **A non-unique key used as an identity key is a silent collision waiting to happen.** The only durable fix is to make the collision *impossible to ignore*: uniqueness restored where it's cheap (Layer 1), and a loud refuse-to-fire guard where it isn't (Layer 2). Everything in between (a dict comprehension that drops on collision, an enrichment that overwrites) hides the failure until a reader finds it.
- **"The run succeeded" is not "the digest is good."** Every stage passed and the pipeline had no notion of cross-card redundancy. Like the 2026-07-11 preheader outage, the gap was between *mechanical success* and *product correctness*: different symptom (delivered-but-wrong vs. not-delivered), same missing check.
- **Adversarial review before code paid for itself.** It killed a fix that would have converted a cosmetic defect into a hard abort, and forced the "trust the join, not the label" reasoning that the time-decay path then confirmed empirically. The plan that shipped is materially safer than the plan that was reviewed.
- **The same bug class usually has siblings.** Sweeping for "non-unique value as identity key" after the fix found the faithfulness-audit id bug, a quieter, reader-facing instance of the exact same shape. Fixing the instance without sweeping the class would have left it live.
