---
title: When the defect IS a rewording, lexical similarity is anti-correlated with it and cannot be the detector
date: 2026-07-26
category: best-practices
module: write, dedup, threads, eval
problem_type: best_practice
severity: high
applies_when:
  - Detecting duplicate, restated or "no new information" model output
  - Choosing a similarity threshold for a quality gate
  - Sizing how often a defect occurs using a similarity-based census
  - About to tune a threshold instead of questioning the metric
tags: [evaluation, similarity, tfidf, threshold, detection, null-delta, labelled-set]
---

## The lesson

If the failure you are detecting is *"said the same thing in different words"*, then
the model's paraphrasing is simultaneously the defect and the thing that defeats a
lexical detector. Similarity and severity move in **opposite** directions: the more
fluently the model reheats the sentence, the lower its score and the less likely it
is to be caught. No threshold fixes this, because the ordering itself is wrong.

Do not tune the threshold. Change the instrument.

## The measurement

The digest re-ships ongoing stories under headlines that restate the previous one.
The obvious gate: flag a continuation headline scoring >=0.75 TF-IDF cosine against
the prior headline and regenerate it.

All 206 consecutive continuation pairs (runs 205-245) were labelled **blind** --
read and labelled before any score was computed, so the labels could not be anchored
to the metric under test. Base rate: 10 restatements in 203 pairs (4.9%).

| threshold | caught | missed | false+ | recall | precision |
|---|---|---|---|---|---|
| 0.50 | 3 | 7 | 6 | 30% | 33% |
| **0.75** | **1** | **9** | 1 | **10%** | 50% |
| 0.85 | 1 | 9 | 1 | 10% | 50% |

No operating point is usable. The worst offenders score *lowest*:

```
0.919  "named Labour leader, set to become PM Monday" -> "named Labour leader and will become PM Monday"
0.376  "escapes test environment and hacks X"         -> "escaped sandbox and autonomously hacked X servers"
0.208  "sends coast guard east of Taiwan"             -> "rotates coastguard force into waters east of Taiwan"
```

Only the near-verbatim case is detectable. Every genuinely reworded one -- the ones a
reader actually experiences as "I read this yesterday" -- sits in the noise with
hundreds of legitimate updates.

## The second-order damage

The same metric had been used to *size* the problem: "8 occurrences since April at
>=0.75". That census was run with an instrument now measured at ~10% recall, so it
undercounts by roughly an order of magnitude. **A detector's recall bounds every
prevalence estimate made with it.** A known-floor caveat is not enough; quantify the
floor before planning against the number.

## Guidance

- Ask whether the defect and the metric are correlated *at all* before choosing a
  threshold. Write down the failure in words, then ask what it does to the score. If
  it lowers it, stop.
- Build the blind-labelled set first. It costs no model calls, it exposes the base
  rate, and it converts "pick a threshold" into a measurable question. Label before
  scoring, or the set inherits the bias of the metric it is meant to test.
- Split trigger from test. Here the *trigger* needed no similarity at all -- the
  thread linker already records deterministically which stories are continuations.
  Only the *test* ("does this state a new development?") is hard, and it is semantic.
- A cheap semantic judge validated against a labelled set is affordable where
  threshold-tuning against a noisy score is not.

## Generalisation

Any detector whose signal is *destroyed by the thing it detects*: lexical dedup
against a paraphrasing model, hash matching against a re-encoder, regex secret
scanning against an obfuscator. The question is not "what threshold" but "does the
failure mode move my metric in the direction I assumed".

## Related

- [[measure-a-prompt-change-against-a-control-run]] -- the same endpoint, and why its
  noise floor makes prompt-level A/B unaffordable here.
- [[a-tuned-composite-score-with-no-ground-truth-is-taste]] -- the neighbouring trap:
  a number that was never validated against labels at all.
- [[a-repair-erases-the-evidence-its-detector-is-validated-against]] -- validating a
  detector on data that no longer contains the failure.
- Fixture: `newsroom/tests/fixtures/null_delta_pairs.json`.
  Full record: `docs/2026-07-26-write-delta-poc-findings.md`.
