---
title: An aggregate rating and a single-rater rating are different scales, so a difference between them is not an error
date: 2026-07-25
category: best-practices
module: sources, circulation
problem_type: best_practice
severity: medium
applies_when:
  - Swapping the authority behind a third-party rating, score, or label
  - A rating vocabulary has no slot for a value the new source emits
  - Writing up a data correction where "we were wrong" is the tempting summary
tags: [ratings, mbfc, ground-news, sources, methodology, framing]
---

`sources.json` carried `bias` and `factuality` per outlet, rendered on `/sources`
and credited to Ground News. Re-reading every outlet from Media Bias/Fact Check
moved 18 of 35 feeds, and the change was written up as "we were overstating 18
of 35, every one in our favour."

That framing was wrong, and the reason matters more than the wording.

## Ground News publishes no ratings of its own

Per their rating-system page: bias is "the average rating of three independent
news monitoring organizations: AllSides, Ad Fontes Media, and Media Bias Fact
Check," and factuality "reflects the average of two trusted rating systems: Ad
Fontes Media and Media Bias Fact Check." Their factuality scale has five rungs
— Very Low, Low, Mixed, High, Very High — and **no "Mostly Factual"**. They
publish **no credibility rating at all**.

So the old vocabulary (`very-high` / `high` / `mixed`) was not missing a value.
It was Ground News's scale, correctly implemented. "Mostly Factual" had no slot
because the scale it came from has no such rung, and `credibility` was absent
because the source had no such field.

That reframes the diff. `bbc_world: high -> mostly-factual` is not a corrected
error: MBFC rates the BBC Mostly Factual, but the Ground News number was an
average including Ad Fontes, which can legitimately land on High. Re-basing onto
a stricter single rater and then reporting the difference as *our* overstatement
credits us with a mistake we did not make, and — worse — implies the new numbers
are corrections when they are re-measurements.

Some changes survive on any scale, and those are the real findings: SCMP
(centre/high vs Left-Center/**Mixed**), Al Jazeera (high vs **Mixed**), WSJ
(centre vs **Right-Center**), Deutsche Welle (centre vs lean-left), Haaretz
(lean-left vs **Left**), Ars Technica (lean-left vs centre). A one-rung shift is
scale noise; a two-rung shift or a flipped lean is a disagreement worth acting on.

## The rule

**Before reporting a data change as a correction, ask whether the two numbers
were ever on the same scale.** If the authority changed, most of the diff is
re-measurement and only the outliers are findings. Report the scale change
first, then the outliers — not one number of "errors" that silently mixes both.

## Why a single rater was still the right call

Not because it is more accurate — an average of two or three raters is
methodologically more robust than any one of them, and MBFC is itself criticised
for opacity.

Because **an average of Ad Fontes and MBFC is not reproducible by a reader**:
checking it needs paid Ad Fontes data. An MBFC page is public and linkable. For
a product whose entire claim is that its labels can be checked, one citable
rater beats an uncheckable average, and the rating should link to the page it
came from. Pick the authority your reader can audit, not the one with the best
methodology, when those conflict.

See also [[a-rename-is-silent-until-every-reference-is-updated]] — the same
change left stale "Ground News" attributions in six places, including a page
footer that contradicted its own masthead.
