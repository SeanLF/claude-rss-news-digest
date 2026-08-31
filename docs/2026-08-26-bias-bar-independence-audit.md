# The bias bar is an independence claim. A third of the time it overstates one.

*2026-08-26. Offline, 68 archived runs, zero API cost. Code in
`scratch/source-independence/`.*

## What was already known

`docs/2026-07-25-feed-sourcing-findings.md` measured this properly a month ago:
per-source repost rates (`scmp_world` 67%, `al_monitor` 53%, `straits_times` 49%,
`the_hindu` 48%), and the key limit —

> `collapse_reposts` requires a **verbatim identical normalized title** — only 48% of
> near-duplicate cross-source pairs (Jaccard >= 0.6) share an exact key, so roughly
> half of same-day wire duplication passes through. Reposters rewrite headlines.

That doc stops at the pipeline. This one carries it to the reader.

## The claim being made to the reader

`render.py` draws `N sources · 3 left · 2 centre · 3 right` with a bias bar. That is
not a count of URLs — it is a claim that N independent editorial judgments converged.
The credit line already reasons this way: *"The outlet's own name alone would claim
reporting it did not do."* The bar makes the same kind of claim at the story level and
nothing checks it.

## Measurement

Real archived `selections.json` + `article_index.json`, every run that has both. The
repo's **own** definitions throughout — `digest.collapse_reposts` applied first (not a
reimplementation), then the July doc's **own** Jaccard >= 0.6 threshold on the
survivors. No new thresholds were invented.

```
runs with both artifacts: 68  (204..273)

stories with resolvable sources      1070
  outlets before production collapse 8396
  outlets after  production collapse 7768   (628 collapsed, 7.5%)

stories rendering a MULTI-outlet spread                            927
stories where >=2 SURVIVING outlets are near-duplicates            307   (33.1%)
```

**A third of multi-outlet stories display at least one pair of outlets whose headlines
are near-identical after the production collapse has already run.**

Distribution matters more than the headline. The cases where it materially misleads are
the small ones, because there the pair *is* the spread:

```
2 outlets shown: 17 stories      <- the entire bias bar is one duplicated report
3 outlets shown: 21 stories
4 outlets shown: 23 stories
```

Outlets most often in a surviving near-duplicate pair:

```
reuters          263      straits_times   223      scmp_world   77
al_monitor        72      globe_and_mail   68      the_hindu    46
```

`reuters` topping the list is expected and not a fault — it is the wire *origin*, so it
pairs with each of its reposters. `straits_times` at 223 and `scmp_world` at 77 track
the July per-source rates closely, which is a useful consistency check between two
independent measurements a month apart.

## Negative control

A within-story duplicate rate means nothing without the across-story rate: two outlets
covering the same event share vocabulary, so the threshold could be measuring topicality.
Pairing titles from *different* stories in the *same run* — same day, same news cycle:

```
within-story  pairs:  60615   >= 0.6:  2.5%
across-story  pairs:   6188   >= 0.6:  0.0%
separation:  78x
```

The threshold measures duplication, not shared vocabulary. The headline number stands.

## Caveats, stated plainly

- Near-identical headlines are **evidence of** derivation, not proof of it. Two outlets
  can independently write the same words for a wire-shaped event. The July doc's finding
  that ~half of same-day wire duplication survives the collapse is what makes it likely
  a substantial share are real.
- `the_hindu` was parked 2026-08-21 (`active:false`), which removes one of the three AFP
  pipes the July doc identified. Its 46 appearances here are historical; the current-day
  rate will be lower.
- This measures the *displayed* spread, not SELECT's weighting. Nothing was found
  suggesting source count drives tier selection.

## What to do about it

The July doc already names the fix and marks it not taken:

> **Cheap structural win, not yet taken:** `feeds.py` parses with feedparser and discards
> `entry.author`. Persisting that one field yields a free, high-precision wire flag on
> `scmp_world`, both Haaretz feeds, `daily_maverick`, `npr_world` and `rappler` — no
> extra fetch, no model call.

That is still the right first move, and this audit is the argument for its priority: it
is not a tidiness issue, it is a correctness issue in something a reader sees.

The second question is a product one, not an engineering one: **if the count cannot be
made to mean independence, should it be displayed as a spread at all?** Options, in
increasing cost — credit the wire origin in the bar itself (the credit line already has
the data), or show the count without the bias segmentation when a near-duplicate pair
survives, mirroring `_shared_cluster_ids`' precedent of degrading the garnish rather than
the story.
