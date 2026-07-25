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

## How much of the catalog is just wire copy

Measured by re-parsing all 38 feeds (2,744 items) and fetching **936 full articles**.
Summary-only detection is near-useless for this: `feeds.py:97` truncates RSS blurbs to
500 chars and the wire credit lives at the *end* of the body, so a regex over summaries
scored Straits Times 2% and The Hindu 0% against true values of 49% and 48%.

Detectors unioned: RSS `<author>`/`<dc:creator>` equal to the agency; HTML meta
provenance (The Hindu ships `<meta property="article:author" content=" AFP">`, Al
Jazeera ships `<meta name="source" content="The Associated Press">`); body repost
markers (`WASHINGTON, July 24 (Reuters) -`, a bare tail sigil, `Fuente: EFE`); and bare
wire datelines.

Three traps that inflate a naive count, all excluded: RSS `<credit>` is a **photo**
credit (counting it gave WSJ 63% and NYT 48% — artifacts); **citing** a wire
("two diplomatic sources told Reuters") is not reposting it; and `(AFP)` in The Diplomat
means Armed Forces of the Philippines.

| Source | `perspective` | n | repost % | dominant wires |
|---|---|---:|---:|---|
| scmp_world | asian | 48 | **67%** | AFP 13, AP 7, Reuters 7, dpa 3 |
| al_monitor | middle_east | 17 | **53%** | Reuters 9 (verbatim datelines) |
| straits_times | singaporean | 49 | **49%** | AFP 11, + 13 stripped datelines |
| the_hindu | indian | 54 | **48%** | AFP 14, Reuters 8, PTI 4 |
| clarin_mundo | argentine | 10 | 40% | EFE 3, AFP 1 (wide CI, small n) |
| haaretz_world | israeli | 35 | 31% | Reuters 5, AP 4 |
| france24 | french_international | 24 | **21%** | AFP 5 — and labels every one |
| globe_and_mail / al_jazeera / daily_maverick / cbc_news | — | 20-54 | 15-17% | Reuters, AP |
| scmp_asia, scmp_china, the_diplomat, the_guardian, bbc_world, le_monde, deutsche_welle, economist_* | — | 12-54 | **0%** | — |

No data (0% would be unknown, not clean): `nyt_world`, `wsj_world`, `washington_post`,
`nikkei_asia`, `economist_middle_east_africa` — fulltext blocked by paywall/bot-wall.

### What it changes

- **France 24 stays.** At 21% and self-labelled ("FRANCE 24 with AFP and Reuters") it is
  not the AFP duplication risk. The three pipes already carrying the same AFP feed are
  **scmp_world, the_hindu and straits_times**.
- **`scmp_world` is the clean cut.** 67% wire while its siblings `scmp_asia` and
  `scmp_china` are both **0%** — the redundancy is one feed, not the outlet.
- **`perspective` labels overstate originality.** `singaporean` (49% wire), `indian`
  (48%), `asian` (67%) describe about half of what those feeds actually emit.
- **`perspective` does not collapse anything.** `prepare.py:156` only derives
  `wire = perspective == "wire_service"`, used by `digest.py::_source_priority` to pick a
  canonical link. Every other value is display-only. And `collapse_reposts` requires a
  **verbatim identical normalized title** — only 48% of near-duplicate cross-source pairs
  (Jaccard >= 0.6) share an exact key, so roughly half of same-day wire duplication passes
  through. Reposters rewrite headlines.
- **Cheap structural win, not yet taken:** `feeds.py` parses with feedparser and discards
  `entry.author`. Persisting that one field yields a free, high-precision wire flag on
  scmp_world, both Haaretz feeds, daily_maverick, npr_world and rappler — no extra fetch,
  no model call.
