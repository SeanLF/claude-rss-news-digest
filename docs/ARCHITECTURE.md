# Architecture

Technical decisions and learnings.

## Pipeline

```
Fetch RSS → TF-IDF dedup → Claude selects → Python renders → Email via Resend
```

### Why Two Phases?

Originally Claude did both selection and HTML generation. Pass 2 (HTML) took ~6 minutes median and was purely mechanical templating — no editorial judgment. Moving to Python reduced this to <100ms.

**Timing data (n=13 runs, before Python render):**

| Phase | Q1 | Median | Q3 |
|-------|-----|--------|-----|
| Pass 1 (Selection) | 5.9m | 6.7m | 7.0m |
| Pass 2 (HTML Write) | 5.9m | 6.2m | 6.6m |

Eliminating Pass 2 saved ~50% of runtime.

## selections.json Schema

Claude outputs this; Python renders it to HTML.

```json
{
  "must_know": [
    {
      "headline": "string",
      "summary": "string",
      "why_it_matters": "string",
      "reporting_varies": [
        {"source": "string", "angle": "string", "bias": "string"}
      ],
      "sources": [
        {"name": "string", "url": "string", "bias": "string"}
      ]
    }
  ],
  "should_know": [],
  "quick_signals": [
    {
      "headline": "string",
      "source": {"name": "string", "url": "string", "bias": "string"}
    }
  ],
  "below_fold": {
    "americas": [{"headline": "string", "source": {...}}],
    "europe": [],
    "asia_pacific": [],
    "middle_east_africa": [],
    "tech": []
  },
  "regional_summary": {
    "americas": "string with [markdown](url) links",
    "europe": "...",
    "asia_pacific": "...",
    "middle_east_africa": "...",
    "tech": "..."
  },
  "stats": {
    "articles_reviewed": 847,
    "sources_used": 34,
    "stories_selected": 58
  }
}
```

**Notes:**
- `reporting_varies` is optional (only on `must_know`)
- `should_know` has same structure as `must_know` but no `reporting_varies`
- Empty `below_fold` clusters are skipped in rendering

## Claude Authentication

**Recommended:** Use OAuth token for both local and production.

```bash
# Generate a 1-year token
claude setup-token

# Add to .env
CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-...
```

The token is passed via environment variable — no volume mounts or credential files needed.

## Deduplication

TF-IDF pre-filter runs before Claude sees articles:
- Compares new articles against 7-day history in SQLite
- Threshold: 0.35 similarity (configurable via `DEDUP_SIMILARITY_THRESHOLD`)
- Filters ~20-25% of articles
- Catches word-overlap duplicates; semantic duplicates may slip through

The prompt also instructs Claude to deduplicate, as a second pass for semantic similarity.
