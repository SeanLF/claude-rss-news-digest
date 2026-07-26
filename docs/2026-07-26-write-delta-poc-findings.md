# Does giving WRITE the thread delta fix null-delta continuations?

**2026-07-26. Answer: no measurable effect, and the premise was wrong. The
"move thread linking before WRITE" refactor was dropped on this evidence.**

Harness: `bin/eval-write-arms` (was `scratch/delta-poc/probe.py`). Replays a past
run's archived artifacts through the real WRITE stage, two arms, N reps.

## The question

Run 237 re-shipped Burnham under a headline that restated run 236's:

> run 236: Andy Burnham named Labour leader, set to become UK prime minister on Monday
> run 237: Andy Burnham named Labour leader and will become UK prime minister on Monday

`8beeefe` transported the *prior headline* to WRITE and measured null. The queued
refactor proposed transporting the *delta* instead, which required reordering
`generate_selections()` so thread identity exists before WRITE. Nobody had tested
whether the delta changes anything. That test is cheap; the refactor is not.

## Pre-registration

- **Arm A** = production today: run 237's archived inputs + `recent_digest_headlines.txt`.
- **Arm B** = A + `thread_deltas.txt` (the 5 linker-matched threads' `whats_new`
  facts) + one prompt line pointing WRITE at it.
- **Primary endpoint**: TF-IDF cosine of the Burnham headline vs run 236's. The mean
  over all 16 stories is *not* an endpoint — the intervention cannot touch the
  ~10 non-continuation stories, and averaging over them is what diluted `8beeefe`.
- **4 reps per arm**, because the previous experiment's entire claimed effect was
  smaller than the variance of one configuration.
- **Declared confound, asymmetric**: run 237's `whats_new` was synthesized *after*
  WRITE from its own selections, so the delta is mildly downstream of the headline
  under test. This biases *toward* the treatment — a null is decisive, a positive
  provisional.

## Result

| tag | vs run 236 | led with a new development | headline |
|---|---|---|---|
| A1 | 0.375 | yes | Burnham set to be UK prime minister faces day-one demand over Iran bases |
| A2 | 0.810 | no | Andy Burnham named Labour leader, becomes UK prime minister on Monday |
| A3 | 0.495 | no | Andy Burnham takes office as UK prime minister |
| A4 | 0.810 | no | Andy Burnham named Labour leader, becomes UK prime minister Monday |
| B1 | 0.333 | yes | Burnham confirmed as Labour leader and Britain's next PM; faces immediate test over Iran |
| B2 | 0.558 | yes | Andy Burnham to be sworn in as UK prime minister Monday, faces immediate Iran test |
| B3 | 0.225 | yes | Burnham sworn in Monday facing day-one demand: let US use British bases against Iran |
| B4 | 0.869 | no | Andy Burnham confirmed as Labour leader, set to become Britain's seventh prime minister in a decade |

| arm | mean | sd | range | near-dup ≥0.75 | led with new |
|---|---|---|---|---|---|
| A | 0.623 | 0.192 | 0.375–0.810 | 2/4 | 1/4 |
| B | 0.496 | 0.247 | 0.225–0.869 | 1/4 | 3/4 |

Fisher exact: near-dup p=1.0, led-with-new p≈0.49. **Not significant.** Both
directions favour B, but B4 — the single worst headline in the study — is in the
treatment arm, and it alone flips near-dup from 0/3 to 1/4.

## What survives, independent of the arms

**1. The refactor's premise is false.** Run 237's shipped *summary* read:

> "...will be Britain's seventh prime minister in a decade and **faces immediate
> decisions on Iran — the US has asked to use British military bases for strikes** —
> and on whether to stiffen Britain's position on Israel and Gaza..."

The headline carried none of it. WRITE **had** the fact and put it one sentence
down. This is a prioritisation failure, not a transport failure, so supplying the
same information earlier cannot fix it. A1 corroborates: the control reaches the
Iran angle unaided, just unreliably.

**2. The noise floor exceeds every effect ever claimed here.** Control spans
0.375–0.810 on identical inputs. `8beeefe`'s headline result (0.864 → 0.803, n=1
per arm) sits inside that band and means nothing in either direction.

## The deterministic near-dup check also fails, as specified

The obvious follow-up — "flag a continuation headline scoring ≥0.75 against the
prior headline and regenerate via the existing repair ladder" — was tested against
the 8 labelled headlines above:

```
caught 2   missed 2   false alarms 0
recall 50%   precision 100%
```

It misses A3 (0.515, restates without lexical overlap) and B4 (0.661). And the
threshold is not portable: **B4 scores 0.869 against a prior-week IDF corpus and
0.661 against an all-time one** — same pair, same formula, different corpus. A
fixed cut cannot be specified without also fixing the corpus. This matches the
already-known historical case of a true duplicate at 0.486, well below the cut.

**Corrected shape, untested:** the *trigger* needs no similarity at all — the
linker already tells us deterministically which stories are continuations
(`matched_score IS NOT NULL`). The *test* — "does this headline state a development
the prior one did not" — is semantic, and lexical similarity is a poor proxy for it.

## Then measured properly: the threshold approach is dead

The 8 headlines above are too few and were generated by one experiment. So all 206
consecutive continuation pairs in the DB (runs 205–245) were extracted and labelled
**blind — read and labelled before any score was computed**, so the labels could not
be anchored to the metric under test. Result:
`newsroom/tests/fixtures/null_delta_pairs.json`. Base rate **10 null of 203 = 4.9%**.

Sweeping the threshold against those blind labels:

| threshold | flagged | caught | missed | false+ | recall | precision |
|---|---|---|---|---|---|---|
| 0.50 | 9 | 3 | 7 | 6 | 30% | 33% |
| 0.65 | 3 | 1 | 9 | 2 | 10% | 33% |
| **0.75** | 2 | **1** | **9** | 1 | **10%** | 50% |
| 0.85 | 2 | 1 | 9 | 1 | 10% | 50% |

**There is no usable operating point.** At the canonical 0.75 the rule catches one
of ten. The ten null-deltas, by similarity:

```
0.919  Andy Burnham named Labour leader and will become UK prime minister on Monday
0.587  More than 500 Rohingya feared drowned at sea after boats sink
0.512  Explosion at Qatar's Ras Laffan gas facility tests Gulf energy recovery
0.493  Iran begins six-day funeral for Khamenei as mourners chant for revenge
0.488  Zelensky fires Ukraine's defence minister, triggering street protests...
0.412  Andy Burnham takes office as UK's seventh prime minister in a decade
0.376  OpenAI agent escaped sandbox and autonomously hacked Hugging Face servers
0.350  Venezuela earthquake death toll passes 1,400 as government faces relief criticism
0.299  NATO summit ends without rupture, but key disputes papered over
0.208  China rotates coastguard force into waters east of Taiwan
```

**The detector is anti-correlated with the defect.** "sends coast guard east of
Taiwan" → "rotates coastguard force into waters east of Taiwan" scores 0.208; the
synonym substitution that *constitutes* the failure is exactly what destroys lexical
similarity. The better the model reheats a headline, the less detectable it becomes.

Two consequences:

1. **The historical census undercounts by roughly 10x.** The "8 cases at ≥0.75 since
   April" figure was produced by an instrument with ~10% recall. It was already
   flagged as a floor; the floor is far lower than the number. At a 4.9% base rate
   this is closer to one null-delta every other run than a rarity.
2. **Any checker must be semantic and validated against the fixture.** 203 labelled
   pairs make an LLM-judge eval well-posed and cheap — which is the affordable path
   the similarity work never was.

Caveat on the fixture: one rater, one pass. It is a working set, not gold. Two
numeric regressions found while labelling are worth a look independently — a
Venezuela toll going "reaches 1,430" → "passes 1,400", and a Khamenei funeral going
"seven-day" → "six-day".

## Cost signal

Resolving a 50%→25% near-dup difference at conventional power needs ~58 reps per
arm, ~$80, for one story on one run, generalising to neither. **Prompt-level A/B on
this endpoint is not affordable.** Do not run further arms; prefer a decision rule
with a citable trigger.

## Process note

An interim read was reported at 7 of 8 reps as a real effect ("0/3 near-dups, 3/3
led with new"). The 8th rep flipped it and produced the worst headline in the
study. Peak confidence immediately before being wrong — wait for the pre-registered
n. See [[measure-a-prompt-change-against-a-control-run]].
