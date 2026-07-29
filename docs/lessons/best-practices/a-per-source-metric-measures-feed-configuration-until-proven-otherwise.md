---
title: A per-source or per-region metric measures feed configuration until proven otherwise
date: 2026-07-25
category: best-practices
module: sources, stats, analytics
problem_type: best_practice
severity: high
applies_when:
  - Proposing any metric aggregated per source, per region, or per bias bucket
  - A distribution over sources looks like an editorial finding
  - Comparing what shipped against what was "available"
tags: [metrics, confounding, denominator, feeds, analytics, blindspot, false-finding]
---

Three separate metrics were built in one day. Each looked like a finding about editorial
judgment. **All three were measuring how the RSS feeds happen to be configured.**

**1. Blindspot** — "which stories does one side of the spectrum not cover?" Fired 1.4x/run
and looked shippable. Real data fired *less often than randomly shuffled bias labels*
(breadth-matched ratio 1.02: zero information). Cause: four of the five lean-right feeds are
foreign business desks — Clarín, Globe and Mail, Nikkei, Straits Times — so "right-leaning"
is a proxy for "does not file World Cup match reports." Six of its top ten flags were
football.

**2. Scoop ranking** — "which source reports first?" Correlated **rho = +0.746 with article
age at fetch**. It was ranking feeds by how deep a backfill they serve, not by speed. A feed
returning 300 items of history always contains the earliest timestamp for a story.

**3. Regional under-coverage** — "is China systematically kept off the front page?" The three
SCMP feeds are **9.1% of the corpus but 56.1% of all China-tagged articles** (each runs
72-85% China by volume). They inflate the availability *denominator* for one region only.
Excluding them, a −15.2pp deficit collapses to −4.8pp and the month-over-month worsening
vanishes entirely — it had been measured against the same contaminated baseline.

## The rule

**Before believing a per-source, per-region or per-bucket metric, control for feed
composition.** Concretely, at least one of:

- **Recompute excluding the dominant contributor** to whichever bucket carries the finding.
  If the effect largely disappears, the metric was measuring that feed's configuration.
- **Correlate the metric against a plumbing variable** — entries served per fetch, article
  age at fetch, keep ratio. A strong correlation means the metric is downstream of feed
  shape rather than editorial choice.
- **Shuffle the labels** and compare the fire rate. If real data does not clearly beat the
  shuffled null, there is no signal. Prefer a *breadth-matched* shuffle (permute only among
  sources of similar coverage breadth), because raw shuffling flatters a metric that is
  really tracking how many stories a source touches.

## Why the denominator is where it goes wrong

Every one of these compares *shipped* against *available*, and the numerator gets the
scrutiny while the denominator is assumed neutral. It never is: availability is entirely
determined by which feeds were configured, what each one caps at, and how far back it
serves. A source that returns 300 items per fetch contributes 300 units of "the world had
this available" against another's 10.

This also explains why the surviving findings survived: the **US under-representation**
(−10.4pp) held across dedup changes, classifier retuning, six months and a
domestic-vs-entangled split, and the **Iran/Gulf crowding effect** (+19.0pp at must_know)
was spread across many feeds with a 15.6% maximum from any one, and was positive in every
month including before the escalation. Both are robust to exactly the control that killed
the other three.

## Corollary for the catalog audit

`stories cited per run` is a **usage** metric. Pruning the catalog on it narrows coverage
toward whatever the curation stages already favour, which is circular. A source with zero
citations may be a genuine editorial gap rather than dead weight. Treat it as a prompt to
look, never as a rule to act on.

See also [[an-aggregate-rating-and-a-single-rater-rating-are-different-scales]] — same
family: a number that looks like a measurement of the world and is really a measurement of
the instrument.
