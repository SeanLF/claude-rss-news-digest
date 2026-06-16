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

The WRITE subagent emits headlines that reference opaque article IDs only. Python (`merge.py:assemble_selections`) drops coherence-failed entries, validates the assembled payload against `schema.SELECTIONS_SCHEMA` (`additionalProperties: false`), and writes `selections.json`. `resolve_article_ids()` in `digest.py` then maps each `article_id` back to its source name, URL, and bias before rendering.

```json
{
  "must_know": [
    {
      "headline": "Sentence case headline",
      "summary": "2-3 sentence summary",
      "why_it_matters": "1-2 sentence insight",
      "sources": [
        {"article_id": "A1"}
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
  "preheader": "One sentence summarizing the 2-3 biggest stories (max 150 chars)"
}
```

**Notes:**
- `reporting_varies` is optional (only when sources frame story differently)
- `sources` entries carry `article_id` only; Python resolves the name/url/bias
- All three top-level keys required (`must_know`, `should_know`, `preheader`)

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
