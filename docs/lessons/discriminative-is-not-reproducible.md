# A signal that discriminates better can still be the wrong one to weight

*2026-08-31. I argued from an AUC study that the clustering join weights the wrong field.
A sweep says keep it as it is, for a reason the AUC could not have shown.*

## The argument I made, which looked airtight

`cluster_extractjoin._tag_bag` builds the join's term bag as `entities*3 + keywords + primary_event*2`.
`docs/2026-07-02-dedup-poc-findings.md` — a blind-judge study, n=178, Cohen's kappa 0.87 — measured
each signal's power to predict "same story":

```
primary_event   AUC 0.923
entity-overlap  AUC 0.867
title-TF-IDF    AUC 0.798
```

So the join weights the *weaker* signal 1.5x above the *stronger* one. Three things seemed to
agree: our own AUC numbers, the junk-drawer diagnosis naming `entities*3 > primary_event*2` as the
cause, and an external paper finding named entities hurt clustering while helping outlier
detection. A shipped digest that day cited a NATO story and "China's robot lawn mowers" under a
piece about European desertification — all three sharing only the token "Europe".

**The sweep says keep 3/1/2.** The reweight is real but small (~-2.5pp junk pairs), and it does not
fix the failure that motivated it.

## What the AUC could not see

The AUC measured each field's discriminative power **within one extraction**. It said nothing about
whether the field is *the same field* next time. Two independent extractions of the same run's
664 articles, same model, same prompt:

```
entities        token Jaccard  0.903
primary_event   token Jaccard  0.740
primary_event   strings IDENTICAL:  29.7%
```

**Entities are ~1.2x more reproducible, and the primary_event string is literally the same phrase
less than a third of the time.** A clustering join is not a one-shot classifier: it is a similarity
computed *between* articles whose tags were generated in different batches. A field that
discriminates well but is re-worded 70% of the time supplies less usable agreement between two
articles than a slightly weaker field that comes back stable.

**Discriminative power and reproducibility are different properties. A weighting needs both, and
an AUC only reports the first.**

## The other two traps in the same measurement

**The instrument was biased toward the hypothesis.** Scoring cluster cohesion by title-vs-title
lexical overlap flatters a primary_event-heavy bag, because `primary_event` tokens are themselves
more title-overlapping (0.582) than entity tokens (0.510). Cutting that path drops the measured
effect from -1.6pp to -0.6pp — level with the noise. Always ask whether the metric shares
vocabulary with the thing being varied.

**The effect was smaller than the choice of how to hold granularity constant.** The same reweight
measures -5.5pp dropped in at the production threshold, -3.1pp at matched cluster count, and
-2.5pp at matched pair count. **Spread 3.0pp against an effect of 2.5pp.** The drop-in number is
not a weighting result at all — it is +29 clusters/run of finer clustering wearing a weighting's
name. Two independent experiments the same day converged on this: passenger count is overwhelmingly
a function of **granularity**, not of bag composition.

**And there is an extraction noise floor.** Two extractions of the same run under the same
weighting differ by 0.6pp, and two candidate weightings flipped sign between them. Any effect
reported without that band is unfalsifiable.

## What to do instead

Keep 3/1/2. The join is the wrong lever: every weighting that removes passengers does it by
clustering finer. The cohesion **gate** recommendation from the junk-drawer measurement stands, and
this sweep is evidence *for* it rather than against.

## Cost of the lesson

$3.30 of extraction — and the reusable artifact is the tags, not the answer. They are archived in
`docs/proposed/join-tags/` (`scratch/` is gitignored, and paying twice for the same tokens would be
the real waste). `cluster_tags.json` now archives them per-run, so the next sweep is free.

---

## Coda: the follow-on hypothesis was also wrong, and the reason completes the lesson

Having found that `primary_event` is re-worded 70% of the time, the obvious next move was: stop
comparing it lexically. Embed the phrase, and "US-Iran ceasefire talks" matches "Iran-US truce
negotiations". **Measured, and it is a dead end.**

The literal claim held. MiniLM cosine between the same article's `primary_event` from two
independent extractions is **0.933** (0.905 on the 70% that actually changed wording). The
embedding does see through the re-wording.

It just does not buy anything, and the control that shows it is retrieval rank-1 — can you find an
article's own partner among 664 candidates?

```
pe lexical Jaccard   86.3%
pe TF-IDF (incumbent) 86.9%
pe MiniLM cosine      87.2%   <- +0.3pp
```

Separation *collapses* in the other direction: 146x for Jaccard against 14.5x for the embedding,
because the embedding lifts matched and unmatched pairs alike. Same-domain news phrases sit high
against everything.

**The mechanism, and the part I had wrong.** Among reworded pairs, rare-token overlap is 0.454 and
common-token 0.726 — the model genuinely swaps names, not just connectives. But **71.3% of reworded
pairs still share at least one rare token**, and a TF-IDF cosine is made almost entirely of those.
That is why TF-IDF scores 0.749 on exactly the pairs where flat Jaccard scores 0.654.

So my framing — *"the best signal contributes nothing when re-worded"* — was **false**. The
incumbent already recovers most of it, via IDF weighting rather than via the token identity I was
looking at. The genuine headroom is the ~29% of the 70% with no shared rare token: a minority of a
minority.

Confirmed from the other direction: deleting `primary_event` outright (`3,1,0`) is the **largest
single effect in the whole sweep** — +2.1 to +5.8pp junk, −20 to −24% cohesive pairs. `primary_event`
is not weak, and it is not being wasted.

**The completed lesson.** Reproducibility is a real second axis that AUC does not report — but
before concluding a signal is being thrown away, check what the incumbent already extracts. A
lexical comparison with IDF weighting is not the naive string match it looks like. Three
consecutive hypotheses about this join (reweight it, embed the phrase, and the framing behind both)
were each refuted by measuring the thing rather than reasoning from a property of it.
