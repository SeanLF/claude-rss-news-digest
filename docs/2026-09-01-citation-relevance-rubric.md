# Citation relevance rubric — DECIDED, reversible

*2026-09-01. My call, not Sean's. Flip any line and the downstream numbers change; that is the
point of writing it down rather than leaving it implicit in a model's judgement.*

## The question this settles

The junk-citation programme stalled on one definitional split: **is an editorial, explainer, or
market-reaction piece about the story's own event a citation you want?** Two model raters said
yes; Haiku, Sonnet and the embedding gate all said no. No amount of model quality resolves a
definition, so every precision/recall figure downstream of it was uninterpretable.

## The rule

> **Genre is not the test. Event identity is.**
>
> A piece **about the story's own event** is a valid citation whatever its genre — news report,
> editorial, explainer, analysis, market reaction, live blog.
>
> A piece about a **different event** is not a citation, whatever its genre — including a
> straight news report from a high-credibility outlet.

## Why, from what citations actually do

`digest.resolve_article_ids` turns each cited `article_id` into `{name, url, bias}`. Those feed
two reader-facing things and nothing else:

1. **The verify link.** The masthead says *"verify anything important against the linked
   sources."* A reader clicks through to check a claim.
2. **The bias bar.** Political leaning per source, the digest's stated differentiator.

Both are served by an editorial on the same event. The bias bar exists precisely to show that
outlets of different leanings covered *this event* — an op-ed from a differently-leaning outlet is
the clearest possible instance of that, not noise. And a reader verifying a claim is served by any
piece covering the event, because they can see what it says.

Neither is served by a hard news report on a *different* event. That is the failure the reader
actually experiences: a source list that includes something unrelated.

**The models were not miscalibrated detectors of an agreed target.** They were applying a
different rule — genre as a proxy for relevance. The raters were tracking what the product does.
That is why Haiku's hand-audited reasons read as accurate ("editorial commentary", "background
explainer", "stock reaction") while its verdicts were still wrong for this product: it was
answering a different question correctly.

## The ten shapes, adjudicated

| # | shape | verdict |
|---|---|---|
| 1 | Op-ed arguing about the same event | **keep** |
| 2 | Explainer: "what the ruling means", same event | **keep** |
| 3 | Market reaction: "stocks fell after the announcement" | **keep** |
| 4 | Live blog covering the event | **keep** |
| 5 | Analysis piece, same event, different outlet leaning | **keep** — this is the bias bar working |
| 6 | Same actors, **different event** (a separate strike, an earlier ruling) | **cut** |
| 7 | Background piece on the topic with no reference to this event | **cut** |
| 8 | Round-up mentioning this event among five others | **keep** — it covers the event |
| 9 | Same-language wire repost of a cited piece | **cut** — dedup's job, not relevance |
| 10 | Foreign-language report on the same event | **keep** — [[project_source_diversity]] |

Rows 6 and 7 are the only genuine junk classes. Row 9 is a different mechanism.

## What this changes

- **The residual junk rate is smaller than any model-based estimate**, because rows 1-5 and 8, 10
  were being counted as junk by every model filter. That is consistent with the hand-adjudicated
  ~9.8% baseline being far below the metric's 35.0%.
- **The multilingual encoder ships on row 10**, which this rubric makes a first-class requirement
  rather than a nice-to-have: cutting non-English coverage of the same event is now a defect by
  definition, not a trade-off.
- **Every model-based precision/recall figure in the junk line should be re-scored against this
  rule** before it is quoted again. Not re-measured — re-scored; the verdicts exist.

## The risk I am not resolving here, flagged rather than buried

Citations are also the evidence base COHERENCE verifies against. Admitting editorials widens the
surface from which WRITE may draw a factual specific, and a fabricated statistic *inside an op-ed*
would pass COHERENCE because it is genuinely present in a cited source.

That is a real second-order risk and it is **not** the citation rubric's job. It belongs to WRITE
(prefer reporting for specifics) and to COHERENCE (treat an opinion piece as weaker support for a
number than a news report). Both are unenforceable as prose and neither is measured. Recording it
as an open question rather than smuggling a genre rule back in through the side door.

## Reversing this

Flip the rule to "genre matters" and rows 1-5 become cuts, the residual junk rate roughly triples,
and the multilingual encoder's justification weakens to a cost argument. That is a coherent
alternative product — a citation list of pure reporting — it is simply not the one the bias bar
describes. One line here is the whole switch.
