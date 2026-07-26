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
the prior one did not" — is semantic, and lexical similarity is a poor proxy for it
(50% recall here). Any checker built for this should be validated against the 8
labelled headlines above before it ships.

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
