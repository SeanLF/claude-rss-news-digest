# Per-story single-turn WRITE, measured against the tool loop

*2026-09-03. Phase 2 of `docs/2026-09-03-stage-invocation-rewrite-plan.md`. Harness:
`bin/eval-write-turns --run 285 --reps 5` (23df49c, 44fe45c). Run 285's archived inputs, all 17
branches, both deliveries five times each, arm T (the Read/Write tool loop production runs)
before arm S (one inline turn, no tools, the same prompt with only its I/O section swapped).
Endpoint: the shipped multi-turn COHERENCE over each assembled draft against the run's full
corpus. Model `claude-sonnet-5`, thinking adaptive, both arms.*

## Verdict

**Gate failed on quality. WRITE stays on the tool loop.** The single-turn arm was flagged more
by COHERENCE in four of five reps, and its mean (2.6 flags per digest) sits above the tool
loop's maximum (2). It also writes shorter summaries, bolts on second events more often, and
in one rep echoed `cluster_index` and `article_ids` from the inline `selected.json` into a
story, which the schema rejects.

With Phase 1 (per-story COHERENCE) also failed on precision, **Phase A of the plan is closed**:
under the current prompts, the file-handoff tool loop earns its price for the checker and the
generator both. The cost lever is real (a third of the price, 40% of the wall clock) and buys
worse output at this prompt and thinking budget. Phases B, C and D do not depend on it and
proceed inside the tool loop.

## Numbers

| | arm T, tool loop (n=5) | arm S, single turn (n=5) |
|---|---|---|
| COHERENCE flags per 17 stories | 2, 1, 1, 0, 1 -- **mean 1.0**, range 0-2 | 3, 3, 3, 3, 1 -- **mean 2.6**, range 1-3 |
| reps above the other arm's max | | 4 of 5 |
| stories written | 17/17 every rep | 17/17 every rep |
| L1 failures | `summary_length` x3, `summary_bolt_on` x1 | `summary_bolt_on` x3, `schema_valid` x1 |
| output tokens per digest (thinking included) | 48.7k-59.9k, **mean 53.7k** | 17.1k-22.6k, **mean 20.2k** |
| cache-read tokens per digest | 520k-572k | 202k (every rep; cache warm from the smoke run) |
| cost per digest, API-equivalent | $0.93-1.23, **mean $1.04** (T1 carried the cache write) | $0.32-0.38, **mean $0.35** warm; **$0.96** on the fresh-container smoke rep |
| seconds per digest (4 branches in flight) | 144-180, mean 160 | 55-69, mean 63 |

The honest production comparison is fresh-container to fresh-container: the smoke rep's
$1.69 (T) against $0.96 (S). The warm figures overstate the saving.

## What the flags were

Both arms are flagged for the same class of thing, and two specifics recur in both: the SCO
declaration "at its Bishkek summit" (no cited source names the city) and "three-quarters of
Bihar" where the source says north Bihar. Arm S adds:

- attribution bindings: the "piracy" remark and the vow to challenge the seizure both put on
  "Russia's foreign ministry" when A6 gives two different officials (S3, S4); the family's
  "terrible decision" when it was the son (S3);
- causal bindings no source states: the Kuwait attack "in retaliation for" the wedding strike
  (S2), "in retaliation after the heaviest US strikes in weeks" (S5);
- added specifics: "temporary mass graves" for "temporary graves" (S1), "days before" a
  Washington trip no source dates (S3), "for talks with President Trump" (S4).

These are the fabrication classes the citation self-check in write.md exists to catch, and
arm S catches fewer of them. The arm S digest is produced with 38% of the output tokens of
arm T. Adaptive thinking spends less when there are no tool turns to think between, and a
prompt that ends "reply with the JSON object and nothing else" invites the model to start
writing. That is the most likely mechanism, and it is testable: an arm S with a matched
thinking budget is the one follow-up this measurement leaves open. It was not run today.

## Two smaller findings

- **Echoed keys.** In S3 one story carried `cluster_index` and `article_ids` copied from the
  inline `selected.json`. The tool loop never does this across five reps. Any inline delivery
  needs Python to strip unknown keys before validation, and the fact that the model merges
  input records into output records at all says something about giving it the input as a
  document rather than as files.
- **Bolt-ons.** `summary_bolt_on` fired in 3 of 5 arm S reps against 1 of 5 arm T. Same
  cluster, same instruction; the inline delivery reaches for "Separately" more. Weak evidence
  (n=5) for the plan's Phase C claim that cohesion has to be fixed before WRITE sees the
  cluster, and against the idea that the "one story per cluster" rule moves this number.

## What this settles

- The tool loop is not vestigial for generation either. The 2026-08-31 lesson said the
  handoff earns its price for a checker; it earns it for the writer too, at this prompt.
- The plumbing cost stays. The next cost lever is not delivery; it is context size per call
  (fewer, shorter inputs per branch) and the thinking budget, both of which the tool loop can
  carry.
- One measurement per stage, as the lesson said. Two are now done; neither passed.

## Raw output

```
WRITE turns eval  run=285  branches=17 (dropped 0)  model=claude-sonnet-5  thinking=adaptive  reps=5  arms=TS
[T1] wrote 17/17 in 153s  tokens in=104 cw=143910 cr=519703 out=54533  cost=$1.23
[T1] coherence flags 2/17 ['summary', 'summary']  L1 failures ['summary_length']
[T2] wrote 17/17 in 152s  tokens in=102 cw=76354 cr=564748 out=51158  cost=$0.93
[T2] coherence flags 1/17 ['why_it_matters']  L1 failures ['summary_length']
[T3] wrote 17/17 in 144s  tokens in=102 cw=92630 cr=545339 out=48658  cost=$0.97
[T3] coherence flags 1/17 ['summary']  L1 failures ['summary_bolt_on', 'summary_length']
[T4] wrote 17/17 in 169s  tokens in=104 cw=87877 cr=572040 out=54240  cost=$1.01
[T4] coherence flags 0/17 []  L1 failures []
[T5] wrote 17/17 in 180s  tokens in=102 cw=90845 cr=559133 out=59854  cost=$1.07
[T5] coherence flags 1/17 ['summary']  L1 failures []
[S1] wrote 17/17 in 55s  tokens in=34 cw=0 cr=201594 out=17091  cost=$0.32
[S1] coherence flags 3/17 ['summary', 'summary', 'summary']  L1 failures ['summary_bolt_on']
[S2] wrote 17/17 in 61s  tokens in=34 cw=0 cr=201594 out=19990  cost=$0.35
[S2] coherence flags 3/17 ['summary', 'summary', 'why_it_matters']  L1 failures ['summary_bolt_on']
[S3] wrote 17/17 in 69s  tokens in=34 cw=0 cr=201594 out=22570  cost=$0.38
[S3] coherence flags 3/17 ['summary', 'summary', 'why_it_matters']  L1 failures ['schema_valid']
[S4] wrote 17/17 in 69s  tokens in=34 cw=0 cr=201594 out=20938  cost=$0.36
[S4] coherence flags 3/17 ['summary', 'summary', 'summary']  L1 failures []
[S5] wrote 17/17 in 63s  tokens in=34 cw=0 cr=201594 out=20368  cost=$0.36
[S5] coherence flags 1/17 ['summary']  L1 failures ['summary_bolt_on']
=== NOISE FLOOR (read this before the comparison) ===
arm T:
  coherence flags: min 0  mean 1.00  max 2  (n=5)
  L1 failures: min 0  mean 0.80  max 2  (n=5)
  cache_read tokens: min 519703  mean 552192.60  max 572040  (n=5)
  output tokens: min 48658  mean 53688.60  max 59854  (n=5)
  cost (API-equiv $): min 0.93  mean 1.04  max 1.23  (n=5)
  seconds: min 144  mean 159.60  max 180  (n=5)
arm S:
  coherence flags: min 1  mean 2.60  max 3  (n=5)
  L1 failures: min 0  mean 0.80  max 1  (n=5)
  cache_read tokens: min 201594  mean 201594.00  max 201594  (n=5)
  output tokens: min 17091  mean 20191.40  max 22570  (n=5)
  cost (API-equiv $): min 0.32  mean 0.35  max 0.38  (n=5)
  seconds: min 55  mean 63.40  max 69  (n=5)
S mean flags 2.60 vs T range 0-2: ABOVE T's maximum
```

Smoke rep (fresh container, `--reps 1`, same day): T $1.69, 1 flag; S $0.96, 4 flags.
