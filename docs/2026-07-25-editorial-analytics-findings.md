# Editorial analytics — what survived, and the one live defect

**2026-07-25.** Five questions run against a read-only prod clone. Nothing was modified.
This file exists because the findings were about to be lost: the session that produced them
recorded its *epistemics* (`docs/lessons/best-practices/a-per-source-metric-measures-feed-configuration-until-proven-otherwise.md`)
but not its *evidence*. Recovered from the session transcript rather than re-derived.

Scorecard: **one actionable defect, one definitive cleanup, one mixed, two measurement
artifacts.** The artifacts are already written up as a lesson; only the survivors are here.

## 1. Null-delta thread continuations — the one thing worth fixing

A story ships, then ships again days later under a headline that says the same thing,
tagged **"Ongoing · day N"**. The card promises a development and the headline delivers
yesterday's.

**Method.** Each shipped story compared against the prior 7 days using the pipeline's own
`dedup.tokenize` + TF-IDF. Story level — distinct `(run_id, headline)`, 11,063 stories.

> **Method trap, load-bearing:** `shown_narratives` stores **one row per source**, not per
> story (mean 2.10, max 48). Rate-per-row overstates repetition ~2x *and* distorts
> month-over-month, because multi-source rows only begin in March. Any future query here
> must aggregate to story level first.

**Rate over time** — exact-duplicate share of stories:

| | Feb | Mar | Apr | May | Jun | Jul |
|---|---|---|---|---|---|---|
| rate | 9.5% | 2.61% | **0.00%** | 0.05% | 0.24% | 1.05% |

The Feb→Apr collapse is `449fc60` (2026-03-08), which passes yesterday's *editorial*
headlines to SELECT. That fix shipped on A/B evidence; this is its first production
confirmation.

**The TF-IDF pre-filter is not what prevents repetition.** When its threshold went
0.35 → 0.80 (2026-07-03) it fell from filtering 179 → 8 articles/run — near-inert — and
duplicates did *not* explode. Dedup is not the lever here.

**Complete census** of null-delta re-ships at similarity ≥0.75 since April, all verified
present in the broadcast HTML actually sent to 11 recipients:

| sim | gap | first | then |
|---|---|---|---|
| 0.835 | +1d | 2026-05-01 King Charles's US visit prompts tariff concession on Scotch whisky | 05-02 King Charles navigates Trump state visit with tariff concession on Scotch whisky |
| 0.789 | +3d | 2026-06-02 UK blocks entry of left-wing U.S. commentators Cenk Uygur and Hasan Piker | 06-05 UK bans left-wing streamers Hasan Piker and Cenk Uygur from entry |
| 0.766 | +3d | 2026-06-03 Kosovo holds third election in 16 months amid political deadlock | 06-06 Kosovo heads to its third election in 16 months amid Serbia deadlock |
| 0.791 | +3d | 2026-06-07 Pentagon raises Israel espionage threat to highest level amid Iran talks | 06-10 Pentagon reportedly raises Israel espionage threat level to highest category |
| 0.780 | +1d | 2026-07-10 Wildfire kills at least 12 in southern Spain, 19 remain missing | 07-11 Wildfire kills at least 12 in southern Spain, 23 missing |
| 0.942 | +3d | 2026-07-12 Former Qatar emir Sheikh Hamad bin Khalifa Al Thani dies at 74 | 07-15 Qatar's former emir Sheikh Hamad bin Khalifa Al Thani dies |
| 0.964 | +1d | 2026-07-17 Andy Burnham named Labour leader, set to become UK prime minister on Monday | 07-18 Andy Burnham named Labour leader and will become UK prime minister on Monday |
| 0.806 | +1d | 2026-07-21 Nicaragua's Ortega declares elections will never be held again | 07-22 Nicaragua's Ortega declares there will be no more elections |

**8 is a floor, not a count.** An independent `cluster_id` check found a true duplicate
scoring 0.486, well below the cut. The detector under-counts near the boundary.

**Scale.** 143 "Ongoing" cards across 27 digests (~5.3/digest, about a third of output).
So this is roughly 3% of continuations — the thread feature mostly works.

### Root cause — structural, and it is not dedup

Verified in code, and it is *not* "the headline restates a stale `whats_new` fact". The
headline never sees `whats_new`:

- `newsroom/src/run.py:109` — `_process_story_threads()` runs inside
  `_archive_run_and_threads`, i.e. **after** assembly. Thread identity does not exist yet
  when WRITE runs.
- `.claude/agents/write.md` — WRITE receives no thread context, no prior installment and no
  prior headline. It is blind to what shipped yesterday.
- `newsroom/src/render.py:344-349` — the thread delta **replaces the summary** and leaves
  the headline untouched.

So the pipeline resolves continuity strictly downstream of the sentence that most needs it.
SELECT knows yesterday's headlines (`449fc60`) and correctly decides to re-ship an ongoing
story; WRITE then writes the headline with nothing telling it what was already said; the
renderer stamps "Ongoing · day 3" above it. For the Burnham case the delta genuinely
existed — first speech, day-one Iran bases decision, a Gaza break with party line — and the
headline carried none of it.

**The fix belongs upstream of WRITE, not in dedup.** It requires an ordering decision (move
thread linking before WRITE, or carry SELECT's prior-headline signal into WRITE); that
choice is open, see `.claude/tasks/todo.md`.

## 2. `story_feedback` — dead table, bot traffic

Not "too few rows to be useful". **Unusable in kind.**

- All 15 targets have duplicated identical votes; gaps cluster tightly at 81–121 min
  (median 101). Three targets got the full up-down-up-down pattern. That is scheduled
  re-crawling — link prefetch, mail security scanners — not readers. Audience is 11–13.
- Lifecycle: shipped `9f26b0a` 2026-07-01, reverted to mailto `66283c7` 2026-07-04. Rows run
  07-02 to 07-05 and stop. `circulation/src/main.rs:283` says it outright: *"`story_feedback`
  was dropped here when the per-story vote was removed product-wide."*
- 37 rows (an earlier count of 16 was stale).

**No N fixes this at 11 recipients.** Working instrumentation would need bot filtering
(reject HEAD/prefetch, POST-with-token rather than GET) and an audience 2–3 orders of
magnitude larger. Treat the table as dead.

## 3. Coverage composition — one solid, one real, one retracted

Validated against the pipeline's own region labels (n=4,774): 82.2% raw / 87.8% among
confidently classified, per-region recall 85.6–90.6%.

**US is under-represented by 10.4pp** — 24.3% of corpus, 13.8% shipped, over-represented in
only 6% of runs. Survived dedup changes, classifier retuning, title+summary coverage, all
six months, and a domestic-vs-entangled split. This is **by design**: `select.md:31` filters
"celebrity, sports, lifestyle, US domestic". Now measured rather than assumed.

**Iran/Gulf crowds out the top of the digest** — **+19.0pp at `must_know`** excluding
`scmp_*`, spread across many feeds (max 15.6% from any one, so not a single-feed artifact),
and positive in **every** month including February, before the escalation
(+8.5, +13.5, +10.1, +8.2, +14.8, +20.6). The squeeze lands on **Latin America −5.5**,
**South Asia −4.7**, **Japan/Korea −3.0**, and gets *stronger* with SCMP excluded. This is
the one genuine editorial finding.

**Ukraine shows no bias** — −0.2 to +1.5pp, CI spans zero on every cut. It held through all
three passes. Worth knowing: Ukraine prominence is the corpus, not the pipeline.

**Retracted: the China deficit.** Claimed as "systematically kept off the front page, every
month, getting worse" (−15.2pp). `scmp_china`/`scmp_asia`/`scmp_world` are 9.1% of the
corpus but **56.1% of all China-tagged articles** (each 72–85% China by volume), inflating
the availability denominator for one region only. Excluding them the deficit collapses to
−4.8pp and the month-over-month worsening vanishes — it had been measured against the same
contaminated baseline. Treat the residual as small and unresolved, not as a finding.

**No drift in eight months.** Every regional shift is under 1 sd of run-to-run noise,
all |ρ| ≤ 0.28.

Caveat that applies to all of the above: "available" is 35 already-Western-skewed feeds. This
is selection bias against a biased corpus, not against the world.

## 4. Coherence flag rate — no trend is detectable

Coherence artifacts exist for only **39 runs** (204–244); the graceful-strip path landed at
run 222, so there is no longer history to recover. At ~16 stories/run, **one flagged story =
6.25pp** — the "median 6%, p90 14%" is 1 and 2 stories. λ = 0.95 flags/run, dispersion index
1.72, first-half vs second-half shift **0.81σ**, no correlation with corpus size (r = 0.085).
It is a count problem, not a rate.

One real signal: the Sonnet 5 detection reframe took flags **5.1% → 12.3%** (28/550 → 8/65,
Fisher p = 0.043). n=4 runs is weak, but it is the direction the reframe was built for, and
all 8 flags are specific and checkable on inspection.

Bonus, previously unmeasured: repair's first two production runs (243/244) repaired and
shipped **3 summary failures that would have been full drops, 0 stories lost**. 5
`why_it_matters` failures were blanked as designed (`repair.py:36` — Phase 2 adds
`why_it_matters` repair).

## What is deliberately not here

The two measurement artifacts — scoop ranking (measured feed backfill depth, ρ = +0.746 with
article age at fetch) and the China deficit above — are written up as a reusable lesson in
`docs/lessons/best-practices/a-per-source-metric-measures-feed-configuration-until-proven-otherwise.md`.
That lesson, not this file, is the durable output of those two questions.
