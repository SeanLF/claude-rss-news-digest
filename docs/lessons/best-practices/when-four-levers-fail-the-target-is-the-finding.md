---
title: When a failure resists model, thinking, prompt and effort, stop tuning — the target is telling you it is not a tuning problem
date: 2026-08-31
category: best-practices
module: coherence
problem_type: best_practice
severity: medium
applies_when:
  - A specific detector miss has survived several independent configuration changes
  - You are about to try one more knob on a case that has never once been caught
  - You want to know when to stop spending on config and start questioning the task
tags: [coherence, effort, eval, prompt-engineering, quantifier, measurement]
---

# Four levers, one case, zero movement

`coherence_faithful` idx 3 is a headline reading **"50% tariffs on most Canadian goods"** where the
cited sources say **"some"** (A24) and **"~5 per cent of Canadian exports"** (A149). A quantifier
overstatement, in a headline, against two sources that state the real scope.

What has been tried against it, each measured through the production SDK path:

| lever | result |
|---|---|
| **Model sweep** — 4 models, 27 archived runs | **0/27** |
| **Thinking config** — disabled -> adaptive | **0/11** this session |
| **Targeted prompt rewrite** (audit hunk F2, "list EVERY specific") | **0/6**, and the ablation showed F2 inert |
| **`effort: xhigh`** on Sonnet 5, 5 runs | **0/5**, ~33% more wall clock, no recall change |

**Zero catches in roughly fifty attempts across four independent dimensions.**

Probe 1 has contained the rule the whole time, verbatim:

> if sources say "some" or "many" and the story says "most", that FAILS

## The point

A miss that moves under *any* of these is a tuning problem. A miss that moves under *none* of them
is a statement about the task, and continuing to turn knobs is the expensive way to learn that.

The three cases that DID move this session moved for legible reasons: idx 0 and idx 4 fell to F3,
which added probe coverage where **none existed**. Nothing else in the sweep produced a single
catch anywhere. So the checker is responsive to coverage and unresponsive to intensity.

The plausible readings for idx 3, none yet tested:

- **Quantifier scope may not be checkable field-locally.** Every other planted error is refutable by
  finding or failing to find a token. "most" vs "some" needs the model to hold the source's
  quantifier and the story's side by side and compare their *extents*, which is a different
  operation from presence/absence.
- **The headline may be the wrong unit.** idx 3 is a headline; the summary carries a softer version
  ("a broad range of Canadian imports") that the fixture labels only BORDERLINE. The checker may be
  reading the pair as internally consistent and stopping.
- **It may need a different mechanism entirely** — a deterministic quantifier-pair check, not a
  probe. Cheap to build, and unlike another config sweep it would either work or fail visibly.

## The rule

**Before spending on another knob, count the dimensions already tried.** Model, thinking, prompt
and effort are close to independent. When a case survives all four, the next experiment should test
a *representation* of the task, not another setting of it.

`effort: xhigh` was still worth running — Anthropic's own guidance says raise effort rather than
prompt around shallow reasoning, and the prompting route had just been shown to fail here, so it
was the documented next move. Running it is what turned "we have not tried everything" into "we
have," and made the null informative rather than an open question.

## Also settled: effort is not a lever for this stage

xhigh matched default effort exactly on recall (4.60), idx 0 (4/5), idx 3 (0/5) and false-drops
(0/35), for ~33% more wall clock. On a pipeline that runs 16-22 minutes under a 5h systemd ceiling,
latency on the most expensive stage is not free. `run_usage.effort` stays NULL by choice, not by
oversight — and that choice is now measured.

Not swept elsewhere, deliberately: RECAP (Haiku, 9.9s, $0.06/run, precision 0.938) and
CLUSTER-extract (extracts what IS present, single-turn) have no shallow-reasoning symptom to fix,
and WRITE/SELECT/repair are graded BY coherence, so tuning them against it is circular. Apply the
same predictive test used for prompt hunks: **does a documented failure exist that this lever
plausibly reaches?**
