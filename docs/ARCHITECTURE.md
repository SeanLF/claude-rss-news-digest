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

Claude outputs this via MCP tool; Python renders it to HTML. Schema enforced with `additionalProperties: false` so Claude retries on validation errors.

```json
{
  "must_know": [
    {
      "headline": "Sentence case headline",
      "summary": "2-3 sentence summary",
      "why_it_matters": "1-2 sentence insight",
      "sources": [
        {"name": "Source Name", "url": "https://...", "bias": "center"}
      ],
      "reporting_varies": [
        {"source": "Source", "angle": "Their take", "bias": "center-left"}
      ]
    }
  ],
  "should_know": [
    {
      "headline": "...",
      "summary": "...",
      "why_it_matters": "...",
      "sources": [...]
    }
  ],
  "signals": {
    "americas": [{"headline": "One-liner", "source": {...}}],
    "europe": [],
    "asia_pacific": [],
    "middle_east_africa": [],
    "tech": []
  },
  "regional_summary": {
    "americas": "Narrative with [inline](url) markdown links",
    "europe": "...",
    "asia_pacific": "...",
    "middle_east_africa": "...",
    "tech": "..."
  }
}
```

**Notes:**
- `reporting_varies` is optional (only when sources frame story differently)
- `bias` enum: left, center-left, center, center-right, right
- Empty `signals` regions are skipped in rendering
- All four top-level keys required

## Claude Authentication

Two options:

1. **OAuth token** (recommended) — uses Pro subscription quota, valid 1 year
   ```bash
   claude setup-token
   # Add to .env: CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-...
   ```

2. **API key** — pay-per-use via Anthropic API
   ```bash
   # Add to .env: ANTHROPIC_API_KEY=sk-ant-...
   ```

Both are passed via environment variable — no credential files needed.

## Deduplication

TF-IDF pre-filter runs before Claude sees articles:
- Compares new articles against 7-day history in SQLite
- Threshold: 0.35 similarity (configurable via `DEDUP_SIMILARITY_THRESHOLD`)
- Filters ~20-25% of articles
- Catches word-overlap duplicates; semantic duplicates may slip through

The prompt also instructs Claude to deduplicate, as a second pass for semantic similarity.
