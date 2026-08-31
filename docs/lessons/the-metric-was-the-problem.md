# When three findings in a row dissolve under hand-checking, the metric is the finding

*2026-08-31. A day spent improving cluster quality, ending with: the problem is 3.6x smaller
than measured, and the best change is the one already shipped.*

## What happened, in order

A validated lexical relevance metric — negative control separating **19.4x**, hand-checked on a
real case — was used to measure "junk citations": articles cited under a story they are not about.
It produced three findings, and hand-adjudication destroyed all three.

**1. The gate's reader-facing win was 82% artifact.** An English-token metric scored an
English-only encoder. Both fail on the same inputs, so the metric endorsed 88 removals a second MODEL rater judged wrong. Non-English citations were cut at **41.0% vs 5.6%** for English, and **82% of those
cuts were the same event in another language**. Corrected: 41.2%→37.3% became 41.2%→40.5%.

**2. A better rule that survived held-out validation was still wrong.** A floor rising with cluster
size beat the incumbent on lexical labels *and* transferred to held-out runs — the usual proof.
Hand labels on its extra cuts: **23% precision, against 58% for cuts both rules make** (p=0.002).
It destroyed **3.4 relevant citations per junk one**. The metric had priced those same cuts at 74%
— a **51-point error**, and the entire "improvement".

**3. Most of the problem never existed.** 120 blind adjudications across two disjoint samples:

```
                 lexical metric      hand
baseline junk    1057  (35.0%)    297  (9.8%)
after the gate         (32.7%)         (6.7%)
```

**72% of the "junk problem" was the metric.** The residual is 0.55 junk citations per story, 8.7
per run in a list of 137.

## The lesson

**A validated metric is validated for a population and a comparison, not forever.** This one earned
its negative control on production clusters, then was used to grade a method whose failure mode it
shares. Nothing about the original validation was wrong; it simply did not cover the case it was
later asked to decide.

Three checks, cheap, that would have caught each:

- **Does the metric fail where the method fails?** English-token overlap grading an English-only
  encoder is a shared blind spot. Ask what inputs the metric is bad at, then check whether the
  method is bad at the same ones. Here the tell was available: the metric flagged **93–98%** of
  non-English citations as junk versus 36.7% of English ones. That ratio was visible before any
  conclusion was drawn.
- **Hand-check the DELTA, not the corpus.** All three errors lived in the cuts a change adds — a
  small set, cheap to read. Adjudicating 35 added cuts was enough to kill finding 2 outright.
- **Held-out validation does not fix a biased label.** Finding 2 transferred cleanly to unseen runs
  because the *bias* transferred too. Generalisation tests protect against overfitting, not against
  a systematically wrong target.

## The other half: the baseline was wrong too

Two derived numbers were wrong in ways that survived because nobody recomputed them:

- **"41.2% → 36.6%"** paired an *uncorrected* baseline with a *hand-corrected* treatment. Like for
  like it is 38.6% → 36.6%. Correcting one side of a comparison and not the other manufactures most
  of an effect.
- **"30% of junk is unreachable below `min_size`"** conflated *clusters the gate declines to split*
  with *clusters too small to consider*. Junk below size 4 is **3.7%**, not 30% — so the parameter
  everyone reached for first was a dead lever, and the reasoning built on it was void.

When a number is quoted onward, recompute it once at the destination. Both of these were repeated
into decisions before anyone re-derived them.

## What was actually true

- **Ship the multilingual encoder** — cut precision 52% → 70%, and it stops stripping the Spanish,
  German and French corroboration the bias bar and the source-diversity goal both depend on.
- **Change nothing else.** Average linkage, floor 0.35, `min_size` 4 dominated every alternative at
  every cost budget once labels were honest.
- **Do not add an LLM cohesion gate.** Not on cost ($0.03–0.05 against $2.50–3.00) — on prize.
  8.7 junk citations per run, on a population the deterministic gate has already skimmed, where it
  would have to beat 58% precision on exactly the cases the embedding got confidently wrong. And
  validating it needs the same hand adjudication, per change.

The best available change was the one already running. That is a result, and it took the day to
earn it.

---

## Correction, same day: the raters were models, not humans

Everything this lesson calls "hand adjudication" was produced by **Claude agents applying a rubric
another Claude agent wrote** — rater A `claude-opus-5`, rater B `claude-fable-5`. The kappa of 0.809
measures two instances of one model family agreeing, not inter-human reliability. Sean caught it;
the verdict files' own `_note` fields say "Hand adjudication by the analyst", which is how the error
propagated.

So this lesson is **stronger, not weaker**, and its own headline needs re-reading: the check that
caught three bad findings was itself an ungrounded instrument. What actually survived is the part
that needed no verdicts at all — the deterministic counts (non-English cut rate 41.0% -> 3.3%) and
the **failing negative control** on the temporal kernel, where a near-identity setting still
"found" a -1.04pp effect. A negative control that fires needs no ground truth to be believed.

The per-stratum kappa also matters and was averaged away: **0.848 / 0.715 / 0.944**. The headline
0.809 is buoyed by the smallest stratum, while the largest (n=80) — the one the "72% phantom" claim
rests on — is only **0.715**, which is moderate, not substantial.

Two further traps found while fixing this:

- **Do not label only where the raters disagreed.** That set is the highest-information sample
  about the *rubric*, but it is a biased sample of the corpus — and because both raters are Claude,
  their **agreement is not evidence of correctness**. The 146 agreements are exactly where
  correlated same-family error hides, and they are entirely unaudited.
- **The rubric is the load-bearing artifact, and no human ever ratified it.** "The raters say
  editorial pieces are relevant" reduces to *opus-5 and fable-5, applying a rule opus-5 wrote,
  agreed with that rule*. Ratifying the rubric is ~15 minutes of human time and is worth more than
  100 labels taken under a rubric nobody endorsed.
