# Forward-idea PoCs on the synthesis direction (2026-06-28)

After validating the core synthesis direction (`2026-06-27-graph-synthesis-direction.md`),
explored three forward ideas as PoCs on real data (runs 204/205). Harness:
`scratch/cluster-replay/temporal_thread.py`, `late_bind.py`, `self_repair.py`.

## #1 Late-binding / soft-edge graph synthesis — WINNER

Don't commit to a hard partition; build a soft article graph (entity overlap), pull a seed
story's neighbourhood *across* hard-cluster boundaries, synthesize at a granularity chosen at
synthesis time. Test: the Iran story is SPLIT by hard clustering into ~9 clusters (deal / oil /
war-powers / reactions / geopolitical analysis). Seed = the 11-article Iran-deal cluster.

| entity-Jaccard threshold | neighbourhood | facts (vs hard 11) | coherent | unsupported |
|---|--:|--:|:--:|--:|
| 0.18 (loose) | +54 arts / 30 clusters | 46 | **False** | 3 |
| **0.35** | +20 arts / 9 clusters | **29** | **True** | **0** |
| 0.50 | +18 arts / 9 clusters | 28 | True | 2 |

**At a reasonable threshold (0.35) it works:** the soft neighbourhood pulls the *genuine* Iran
facets the hard partition scattered (war-powers vote, oil moves, reactions, geopolitical
analysis, Hormuz) into ONE coherent, fully-faithful synthesis (29 facts vs hard's 11) — exactly
the promise: **decide granularity at synthesis time** instead of being locked by the partition.
**The loose threshold proves the guardrail:** at 0.18 the hub-entity problem (Trump/US connect
everything) over-pulls 54 articles spanning unrelated stories -> the synthesis returns
`coherent_event=False` ("Multiple Unrelated News Events") and refuses to fabricate.

**Honest meta-finding:** late-binding does NOT dissolve the edge-quality problem — it RELOCATES
it from "partition" to "edge weights" (naive entity-overlap inherits the same hub-entity failure
the clustering work hit). But unlike hard clustering, when it errs the synthesis *catches it*,
and at a tuned threshold it buys real editorial control over granularity. The open challenge is
the threshold/edge definition (run/story-dependent; would need tuning or event-level edges, not
raw entity-bag). This is the one forward idea that does something the current pipeline can't.

## #2 Temporal threading — real UX win, velocity-dependent dedup

A digest story as the DELTA on an ongoing thread, not a fresh re-summary. The Iran deal evolves
across consecutive runs 204-208 (scrutiny -> MoU -> Swiss talks). Synthesized run 205's Iran
story (a) THREADED — given run 204's coverage as prior, asked for the update only; vs (b)
STANDALONE.

The threaded item is genuinely better-shaped: leads with the new development, a real continuity
note ("following yesterday's congressional pressure..."), and a `still_unresolved` section
tracking open threads — something the current digest doesn't do. BUT the dedup metric was small:
threaded 13 new / 3 repeat vs standalone 20 new / 3 repeat (both barely repeat yesterday,
because the Iran story moved fast day-to-day). So the value is **format/UX (continuity +
unresolved-tracking), not a big dedup win** — the dedup benefit would show on a *slow* story.
Conflated bonus: threading's update-focus trades coverage (13 facts vs standalone's 20).

## #3 Self-repair loop — weak, redundant

synthesize -> audit -> repair flagged facts -> re-audit. On run 205's flagged facts: 3 -> 2
unsupported (within audit cross-family noise), 0 facts removed. Some flagged facts are unfixable
(truncated sources) and the model kept-and-edited rather than removed. Reduces to the existing
COHERENCE drop-on-fail logic — the audit->drop loop already does this. Skip.

## Ideas that emerged

- **Open-question ledger** — promote threading's `still_unresolved` into a persistent per-thread
  ledger: carry unresolved questions forward, mark resolved when they close. Cheap, novel-ish,
  high reader value for a thread-follower.
- **Unified late-binding + threading** — a *persistent soft graph that accretes across days*: an
  ongoing story IS a growing subgraph, synthesized as "what's new in this evolving story-graph."
  The ambitious version, and the only thing here with a genuine research flavour.

## THE UNIFIED IDEA — evolving story-thread (the "even better" change) — VIABLE

Combined the two winners into a persistent story-graph that accretes across days
(`evolving_thread.py`): each day late-binding scopes the Iran subgraph, threading frames the
delta, and an OPEN-QUESTION LEDGER is carried forward. Ran across 4 consecutive days (runs
204-207: scrutiny -> MoU signed -> ceasefire -> Swiss talks cancelled).

**Qualitatively it's a categorically better reader experience** — the story EVOLVES: the running
narrative is carried forward and **13 open questions are raised then RESOLVED across the thread**
("what are the deal's terms?" -> resolved when the MoU text is released; "will the Switzerland
talks happen?" -> resolved when they're cancelled). Nothing in the current pipeline does this;
today it's 4 disconnected daily Iran summaries.

**The PoC found a real failure mode, then fixed it:**

| evolving-thread (4-day) | unsupported | facts | resolved |
|---|--:|--:|--:|
| first cut (memory bleeds into facts) | **22.5%** | 80 | 8 |
| strict grounding (memory != facts) | **8.4%** | 95 | 13 |

The first cut cratered faithfulness to 22.5% (vs 3.6% single-day): carrying state across days +
late-binding's bigger bundles made the model weave accumulated-narrative facts into "today's
news" with today's citations, which then don't trace. The fix — explicitly walling off the
running narrative/ledger (MEMORY, may reference prior) from `whats_new` (must cite a TODAY
source, nothing carried) — **more than halved it to 8.4%** while producing MORE facts and
resolving MORE questions. Residual ~8% concentrates on the most complex day and is exactly what
the existing audit->drop (COHERENCE) layer mops up. So the unified idea is **viable**: the
best-experience digest, faithful-enough with a grounding-discipline prompt + audit-drop.

## Recommendation

**Build the evolving story-thread** — it's the "even better" change: it subsumes #1 (late-binding
scopes each day) and #2 (threading delta + continuity), adds the open-question ledger, and is the
only thing here that makes the digest a *living* product rather than daily snapshots. Faithfulness
is solved by memory/fact separation + the existing audit-drop. Remaining build work: event-level
edges for late-binding (vs entity-bag), per-story thread state persisted across runs (the digest
already has `shown_narratives` + `weekly_recap` to build on), and the audit-drop wired into the
thread. Fallback/keep: plain late-binding (#1) and the continuity framing (#2) are useful even
without full threading.

(Original single-idea recommendation, now superseded by the unified thread:)
Push **#1 late-binding** — it's the one with real new capability (granularity at synthesis time).
Next step: replace raw entity-Jaccard edges with event-level edges (the same discrimination the
clustering work showed is the crux) and tune the threshold, then measure coverage-vs-faithfulness
across more seeds/runs. **#2 threading** is a cheap, shippable UX upgrade (the `still_unresolved` +
continuity framing) worth doing regardless. **#3** is subsumed by COHERENCE.
