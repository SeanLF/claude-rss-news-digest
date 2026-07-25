# Feed sourcing: what actually reaches the pipeline

**2026-07-25.** Measured, not inferred. Prompted by The Hindu's feed dying and a
question about whether to route blocked publishers through Google News.

## Three feeds were wasting most of their capacity

| Feed | Before | After | Change |
|---|---|---|---|
| `der_spiegel` | 20 entries, **0 fresh in 24h** (newest 390h old) | 20 entries, **20 fresh** | switched to the German world desk |
| `reuters` | 100 entries, **67 fresh** | 100 entries, **100 fresh** | added `when:1d` |
| `nikkei_asia` | 100 entries, **7 fresh** (span to 8561h — 357 days) | 27 entries, **27 fresh** | added `when:1d` |

### der_spiegel was never broken, just English

`spiegel.de/international/index.rss` is a curated English section publishing
roughly **one article every five days** — its 20 entries spanned ~3 months. The
pipeline filters on `published > last_run`, so `articles_kept = 0` was the filter
working correctly, every day, for weeks.

`spiegel.de/ausland/index.rss` is the same masthead's **German** foreign desk:
20 entries, all 20 published inside 24h, 203-char summaries. `le_monde` is
already French, so a non-English feed is established precedent — the curation
models read it fine; only WRITE's output language matters.

**This kills the "alert when a source keeps 0 articles for N runs" idea.** It
would have fired on der_spiegel daily for a source that was behaving correctly.
Any such check has to know each source's expected cadence.

### Google News `site:` searches need a time bound

Without one, Google returns ~100 results ranked by relevance over an unbounded
window, and the pipeline then discards everything older than the last run. Nikkei
was spending 93 of its 100 slots on articles up to a year old. `when:1d` spends
every slot on today.

## Google News: no API, and the links are opaque

There is no official Google News API (the original was deprecated in 2011).
`news.google.com/rss/search?q=` is real and stable-ish but undocumented and
unsupported. Since ~2024 its links are opaque `CBM...` ids, not publisher URLs.

`gnews.py` decodes them via Google's internal `batchexecute` RPC — undocumented,
needs a browser User-Agent, and rate-limits. Prod on 2026-07-25:
`gnews: rate-limited (429), stopping link resolution for this run`.

When resolution fails, readers get the raw Google link. Measured across four
published issues: **19-22% of external links** were `news.google.com` redirects,
and `curl -L` does not reach the publisher — the final URL is still Google (the
redirect is JS-driven). That is one in five links landing on an interstitial,
against a stated promise that every story links out to its source.

The cache in `gnews.py` is per-run and in-memory, so a resolved id is thrown away
between runs even though the mapping is permanent.

## How worldmonitor does it (for comparison)

[koala73/worldmonitor](https://github.com/koala73/worldmonitor), same problem
domain, ~10x the feed count:

- **296 of 563 feed entries are Google News `site:` searches** — over half, vs 2
  of 35 here. They lean on it as the default acquisition path, and they do use
  `when:1d` / `when:2d` / `when:3d` throughout.
- **No URL resolution at all.** No `batchexecute`, no decoding, nothing. They
  ship the opaque Google links to readers. Our (rate-limited) decoder is ahead of
  theirs.
- **No VPN and no commercial proxy** — no Bright Data, ScraperAPI, ZenRows,
  Oxylabs. The one "residential proxy" reference in the repo is about YouTube
  stream detection, not RSS.
- Their "proxy" is **their own two-hop egress**: a Vercel serverless function
  (`api/rss-proxy.js`) fetching direct with a spoofed Chrome 120 UA, falling back
  to a **Railway relay** on failure — which sends an honest
  `WorldMonitor-RSS-Proxy/1.0` UA. Google News gets a longer timeout (20s vs 12s)
  because it is slow.

The Railway-relay fallback is effectively "retry from a different IP." It would
work against The Hindu's block. It is also, in effect, routing around an access
decision, which is why it is documented here rather than adopted.

## The Hindu: not a Google News candidate

Cloudflare managed challenge on the Hetzner ASN — 403 from prod with any UA
including none, 200 from a residential IP with the same bare UA, and even
`/robots.txt` 403s. Their robots.txt does **not** disallow `/feeder/`, so the
block is bot-scoring collateral, not stated policy.

Routing it through Google News would add a third source to the dependency that is
already producing a fifth of our dead-end links, to escape a block on a feed the
publisher's own robots.txt permits. The cheaper and more honest move is to ask
The Hindu to allowlist us. Their feed is worth the email: 60 entries, 30 fresh in
24h, 167-char summaries — comfortably the best Indian option measured
(LiveMint 35/35 but India-domestic-heavy; Indian Express 200 entries with **no
summaries**; scroll.in and thewire.in return 0 entries; ThePrint 403s).

## Honest-UA experiment: rejected

A/B'd `NewsDigestBot/1.0 (+url)` against all 35 feeds from prod. The Hindu stays
403 (the block is ASN-level, not UA-level) and **haaretz_middle_east +
haaretz_world flip 200 -> 403**. A global honest UA costs two working feeds and
gains nothing. It is only worth revisiting as a per-source override alongside an
actual allowlist request.
