---
title: A position a model counts is not an identifier; resolve it by content everywhere, or the next consumer repeats the bug
date: 2026-09-03
category: logic-errors
module: write_fanout, cohesion, select
problem_type: logic_error
severity: high
applies_when:
  - a model emits an index, ordinal or position into a list it was shown
  - a new stage consumes a field an older stage already stopped trusting
  - a "known stray" in a story turns out not to be in the story's cluster at all
tags: [select, cluster_index, drift, write, fan-out, resolution, threads, citations]
---

# A position a model counts is not an identifier

## The lesson

SELECT writes `cluster_index`, a 0-based position into a list of three hundred clusters. A
model counting into a long list drifts, and it drifts quietly: the index is always in range
and always names *a* cluster, just sometimes the neighbour. Across 79 archived runs, 182 of
1,291 selected stories (14%) name a cluster holding none or few of their own citations,
mostly off by one to three. The citations are the content; the index is a claim about the
content. Anything that needs the cluster must derive it from the citations, and it must do
so in one place, because the second consumer to read the raw index repeats the bug.

## What happened

In July, threads keyed a story's cluster on `cluster_index` and run 247 rendered 7 of 12
should_know entries against a cluster containing none of their articles. The fix was
`utils.cluster_for_articles`: the cluster holding the most of a story's distinct citations,
with a docstring explaining why. Threads and merge switched to it.

On 2026-09-01 the per-story WRITE fan-out was built. It needed each story's cluster to give
WRITE its context, and it read `cluster_index` from `selected.json` directly, unioned with
the citations. The union hid the bug: WRITE always saw the cited articles, plus whatever the
neighbouring cluster held. On run 285 that neighbour was a lone Treasury-yields article
(index 234; the story's five citations all live in 235), and the SCO brief opened with it.
The join PoC two days later noticed the "stray" had never been in the story's cluster.

Two more consumers had already been written against the raw index before it was noticed:
the cohesion gate's grouping and its verdict keying. Both were fixed the same day
(99f6ed5, ee1f0fa): the citations decide, ties to the earliest cited, the index only when
no citation lands anywhere, and a verdict is keyed by the story's position, not its cluster.

## The shape

- The model is asked for a position because positions are cheap to emit and cheap to join
  on. They are also the one thing a model cannot verify about its own output.
- A field one stage stopped trusting is still in the file. The next author sees a field with
  a plausible name and reads it. The docstring that says "do not" is on a function they did
  not call.
- The symptom hides under a union or a fallback. Nothing fails; something is slightly wrong
  in 14% of cases, and it looks like the model's editorial judgement.

What to do instead: when a model emits a position, resolve it by content at the boundary
where the file is read, keep the model's value in the artifact for audit, and put the
resolver in the lowest module every consumer can import. Then grep for the raw field and
make every read go through the resolver. The measurement that proves the rate is one query
over the archive; run it before deciding the bug is rare.

## Related

- [[a-per-run-label-is-not-a-key]] -- the same class one level out: a per-run identifier used
  across runs.
- [[unit-tests-at-both-ends-of-a-seam-pass-while-the-seam-is-broken]] -- why the fan-out's
  tests did not see this: they built `selected.json` by hand with correct indices.
