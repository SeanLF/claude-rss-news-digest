# arXiv RSS Feed Characterisation

Research date: 2026-04-03. Feeds fetched live from `export.arxiv.org`.

---

## 1. Feed Structure

### Endpoint and redirect

All feeds redirect: `https://arxiv.org/rss/{category}` -> `http://export.arxiv.org/rss/{category}` (HTTP 302). The export subdomain is the canonical RSS host.

Combined feeds work via plus-sign syntax: `http://export.arxiv.org/rss/cs.LG+cs.AI`.

### XML fields per item

Every item has these fields:

| Field | Content |
|---|---|
| `<title>` | Paper title (plain text) |
| `<link>` | `https://arxiv.org/abs/YYMM.NNNNN` |
| `<description>` | Full abstract text (100-600 words) |
| `<guid>` | `oai:arXiv.org:YYMM.NNNNN` format, non-permalink |
| `<category>` | One or more subject categories (e.g. `cs.LG`, `cs.AI`) |
| `<pubDate>` | Weekday publication timestamp |
| `<arxiv:announce_type>` | `new`, `cross`, or `replace` |
| `<dc:creator>` | Comma-separated author list |
| `<dc:rights>` | License URI (CC-BY 4.0, CC BY-NC-SA 4.0, or arXiv nonexclusive-distrib/1.0) |

Optional fields, present when applicable:

| Field | Content |
|---|---|
| `<arxiv:journal_reference>` | Conference/journal citation (e.g. "Proceedings of SIAM SDM 2025") |
| `<arxiv:DOI>` | DOI for published version |
| `<arxiv:comment>` | Author notes (page count, code links, etc.) |

### Abstracts

Yes, full abstracts are in `<description>`. Typical length: 150-400 words. The description is prefixed with the announce type: `"arXiv:2604.00005v1 Announce Type: new. Abstract: ..."`. This prefix would need stripping.

### `arxiv:announce_type` values

- `new` -- submitted for the first time, primary category matches this feed
- `cross` -- cross-listed from another category (same paper, already `new` in another feed)
- `replace` -- updated version of a previously-submitted paper

This is important for dedup strategy (see Section 2).

### Update frequency

Feeds update once daily at midnight US Eastern, weekdays only. No Saturday or Sunday updates. The channel `<pubDate>` reflects the announcement date.

### Link format

`https://arxiv.org/abs/2604.00005` -- year-month prefix (2604 = April 2026), then 5-digit sequence. These are stable, permanent URLs.

---

## 2. Volume Analysis

### Per-category daily item counts (2026-04-03)

| Category | RSS feed items | Actual submissions (arxiv.org/list) | RSS cap hit? |
|---|---|---|---|
| cs.LG (Machine Learning) | 50 | 143 | Yes |
| cs.AI (Artificial Intelligence) | 50 | 188 | Yes |
| cs.CL (Computation and Language) | 50 | 89 | Yes |
| cs.CV (Computer Vision) | 50 | 149 | Yes |
| stat.ML (Statistics - ML) | 46 | 17 | No |

**The RSS feed is hard-capped at 50 items per single-category request.** The cap is not documented precisely, but the arxiv RSS help page mentions a 2000-item limit for combined feeds. In practice, all high-volume categories hit a 50-item ceiling on single-category feeds. This means the RSS feed only exposes roughly 28-56% of daily submissions for the major ML categories.

### Combined feed behaviour

A combined `cs.LG+cs.AI` feed returned 50 items total -- the cap still applies to the combined request. This means combining via the plus-sign syntax does not increase throughput; it just pools items from both categories under a single 50-item cap.

### Cross-listing and dedup implications

A paper with primary category cs.LG may also list cs.AI, cs.CL, and cs.CV as secondary categories. In each category's feed, the paper appears with `<arxiv:announce_type>cross</arxiv:announce_type>`. So:

- The same paper title will appear verbatim in multiple category feeds.
- The existing TF-IDF dedup (threshold 0.35) will catch these: identical titles score 1.0 similarity.
- The `arxiv:announce_type` field also provides a clean programmatic signal: only ingest `new` items (skip `cross` and `replace`) per feed to avoid duplicates entirely before TF-IDF even runs.
- The `arxiv:announce_type=replace` items are updated versions -- these would match their original title at 1.0 and be filtered by TF-IDF if the original ran in the last 7 days.

### Volume vs current pipeline

Current pipeline: ~500 articles/day across 35 news sources (~14 articles/source/day on average).

If we subscribe to cs.LG + cs.AI + cs.CL + cs.CV + stat.ML as separate sources, we get at most 5 x 50 = 250 items, but with heavy cross-listing dedup this drops substantially. A realistic estimate of unique `new` papers across these five categories: 200-300/day (cross-listing is pervasive; many cs.CL papers are also cs.AI, etc.).

This volume is manageable -- it is roughly half the current pipeline's article input. The academic vertical would be a separate pipeline run, not mixed with news.

**Key constraint: the 50-item RSS cap means a popular category on a busy day delivers only a third of actual submissions.** For a curated digest this is not necessarily a problem -- 50 items is plenty to select from -- but it means the digest cannot claim to cover all new papers. The arXiv API (via `arxiv` Python library) would give complete access if full coverage matters.

---

## 3. Papers With Code and Hugging Face Feeds

### Papers With Code

`paperswithcode.com` has been acquired by Hugging Face. The domain now redirects all URLs to `huggingface.co/papers/trending`. **No dedicated RSS feed exists.**

### Hugging Face Daily Papers

- `https://huggingface.co/papers.rss` -- 404
- `https://huggingface.co/papers/rss.xml` -- 401 (authentication required)
- `https://huggingface.co/papers` -- No RSS link element in the page head. No machine-readable feed discoverable.

The HF Daily Papers page (`huggingface.co/papers`) is community-curated: researchers submit papers, the community upvotes, and a daily list emerges. On 2026-04-03 there were approximately 35 papers listed. This is a much smaller, higher-signal set than raw arXiv.

**No RSS feed is available for HF Daily Papers.** Consuming it would require either screen-scraping the HTML (fragile) or using the HF API if one exists. This is a gap worth investigating separately -- the curation signal is valuable (papers with high upvotes + GitHub repos = real practitioner interest).

### Summary

Neither Papers With Code nor Hugging Face Daily Papers offer a standard RSS feed today. arXiv's own RSS is the only machine-readable option for this vertical.

---

## 4. Pipeline Compatibility Assessment

### What works out of the box

- **Feed fetching (`feeds.py`)**: `feedparser` handles the arXiv RSS format correctly. The `<description>` field maps to `entry.summary`. The `<dc:creator>` field maps to `entry.author` or `entry.authors`. No changes needed.
- **URL format**: `https://arxiv.org/abs/YYMM.NNNNN` links are clean, stable, and safe. The `is_safe_url()` check will pass.
- **TF-IDF dedup**: arXiv titles are long and technical, which actually helps TF-IDF -- they have high lexical specificity. Identical cross-listed titles will hit 1.0 similarity and be filtered. Near-duplicate papers (same topic, different groups) may also match, though whether that is desirable for an academic digest is a content question.
- **Thread pool fetch**: No issue with 5 sources at `max_workers=10`.

### Issues requiring changes

**1. Summary truncation (blocking for abstract quality)**

The pipeline truncates in two places:
- `feeds.py` line 117: `[:500]` characters
- `prepare.py` line 96: `[:MAX_SUMMARY_LENGTH]` = 200 characters

arXiv abstracts are 150-400 words, typically 800-2000 characters. At 200 characters, Claude sees less than the first sentence of the abstract. For news articles this is fine (summary is often a lede). For academic papers, the abstract IS the editorial unit -- cutting it destroys the signal the CLUSTER and SELECT agents need.

`MAX_SUMMARY_LENGTH` would need to increase to at least 1000-1500 characters for academic papers. This is a config change, not a code change, but it increases token consumption.

**2. `bias` and `factuality` fields (semantic mismatch)**

`sources.json` schema requires `bias` (political) and `factuality` fields. These are nonsensical for arXiv -- all preprints are self-published by researchers, political bias is meaningless, and factuality is undefined for preprints (they're not peer-reviewed). The schema validation in `feeds.py::load_sources()` will reject any source entry missing these fields.

This confirms the need for a `vertical.json` abstraction (separate source definitions per vertical). For an academic vertical, appropriate metadata would be: `open_access: true`, `venue: "arXiv preprint"`, `peer_reviewed: false`, `institution` (optional, per-paper not per-source).

**3. Announce type filtering (quality improvement, not strictly required)**

Ingesting all 50 items per category feed will include `cross` and `replace` items. Including `cross` items without filtering means the same paper can appear multiple times across feeds. Including `replace` items means papers from the past 7 days reappear as "new" (they would be filtered by TF-IDF if the original title ran, but consume dedup budget). Best practice: filter to `arxiv:announce_type = new` only. This requires a small change in `feeds.py` to expose the `announce_type` field, or a post-fetch filter in the academic vertical's pipeline.

**4. The subagent pipeline works conceptually, with prompt adaptation**

CLUSTER, SELECT, WRITE agents receive article title + summary. For academic papers:
- CLUSTER: groups by research area/topic -- appropriate
- SELECT: picks the most significant papers -- appropriate; needs different selection criteria than news (impact, novelty, applicability vs. timeliness)
- WRITE: currently writes news-style headlines and "why it matters" summaries -- the "why it matters for practitioners" framing actually translates well to research digests
- COHERENCE: verifies headlines vs. source -- same logic applies

The SELECT and WRITE prompts would need domain-specific tuning (no "breaking news" framing, emphasis on methodological novelty and practical implications), but the architecture is sound.

**5. Dedup window and `shown_narratives` table**

The 7-day dedup window makes sense for news (avoid covering the same story twice in a week). For academic papers it needs thought: a landmark paper from 3 days ago might warrant mention again if a follow-up appears, while incremental variations of the same approach should be suppressed. The current TF-IDF approach will handle exact duplicates; editorial suppression of "n-th paper on X" requires a different strategy (possibly a higher threshold or a separate recency weighting).

**6. `original_title` dedup register (no changes needed)**

The `shown_narratives.original_title` column stores RSS titles for dedup. arXiv titles are verbose and unique enough that TF-IDF at threshold 0.35 will work correctly -- possibly better than with news articles which often share generic phrasing ("Biden says", "Markets fall").

---

## 5. Recommended Feed Set for an ML Papers Vertical

### Recommended categories

| Priority | Category | Rationale |
|---|---|---|
| Core | cs.LG | Machine learning -- the broadest ML category; highest volume |
| Core | cs.CL | NLP/LLMs -- highest practitioner relevance right now |
| Core | cs.CV | Computer vision -- large, active, many deployed systems |
| Secondary | cs.AI | AI methods; heavily cross-listed with cs.LG, so dedup will trim aggressively |
| Optional | stat.ML | Statistical ML; lower volume (17/day), more theoretical; distinct audience |

### Cross-listing reality check

cs.AI and cs.LG have massive overlap: many papers in cs.AI are also in cs.LG, and the combined feed (cs.LG+cs.AI) still returns only 50 items. Treating them as separate sources with dedup is the right approach: subscribe to both, let TF-IDF collapse the duplicates, and allow the editorial agent to see unique papers from each.

### Filtering recommendation

Add `announce_type` to the fetched article schema and filter to `new` only in the academic vertical. This eliminates cross-list duplicates programmatically before TF-IDF, reducing dedup noise. `replace` items (updated versions) could optionally be shown if the update is substantial -- but detecting that requires reading the changelog, which arXiv does not include in the RSS.

### Volume after filtering

Estimated unique `new` papers/day across cs.LG + cs.CL + cs.CV + cs.AI (with dedup): 150-250. With SELECT agent choosing ~10-15 for the digest, the selection ratio is roughly 1-in-15 to 1-in-25 -- comparable to the current news pipeline's selectivity. stat.ML adds ~15 more with minimal duplication.

### What to skip

- `cs.NE` (Neural and Evolutionary Computing) -- substantial overlap with cs.LG
- `cs.RO` (Robotics) -- adjacent but distinct audience; consider a separate vertical
- `eess.SP`, `eess.IV` -- signal/image processing; cross-lists heavily with cs.CV but more engineering-focused
- Raw arXiv API -- higher complexity, not necessary given the editorial selection ratio

### Source schema for academic vertical

```json
{
  "id": "arxiv_cs_lg",
  "name": "arXiv cs.LG",
  "url": "http://export.arxiv.org/rss/cs.LG",
  "open_access": true,
  "peer_reviewed": false,
  "venue": "arXiv preprint",
  "domain": "machine_learning"
}
```

The `bias`, `factuality`, and `perspective` fields from the news schema have no equivalent here. This confirms that a `vertical.json` abstraction is the right architectural move before adding academic sources to any shared pipeline.

---

## Rate Limiting and robots.txt

- `robots.txt` specifies `Crawl-delay: 15` as the default for all bots.
- No explicit Allow/Disallow rules for RSS feed paths -- the general crawl delay applies.
- arXiv's RSS help page defers to their API Terms of Use for formal policy.
- Fetching 5 feeds once daily at pipeline run time is well within any reasonable limit.
- The existing `fetch_source()` retry logic (with exponential backoff) handles transient failures gracefully.

For heavy usage (e.g., polling multiple times per day or fetching many categories), the arXiv API (`api.arxiv.org/query`) is the appropriate interface and has its own documented rate limits (1 request/3 seconds, but returns up to 2000 results per query with pagination).
