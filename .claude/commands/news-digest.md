# Daily News Digest

Generate a personalized news digest from CSV input files.

## Input Files

Read all CSV files from `data/claude_input/`:
- `headlines.csv` - Previous headlines (DO NOT repeat these)
- `sources.csv` - Source metadata (id, name, bias, perspective)
- `articles_*.csv` - Articles split across files (source_id, title, url, published, summary)

## Processing Rules

### Deduplication (CRITICAL)
1. DO NOT repeat stories from headlines.csv
2. Only re-include if MAJOR new development - mark with [UPDATE]
3. Same story from different sources = same story

### Content
- DO NOT fabricate beyond article summary
- Use hedging ("reportedly", "according to") when uncertain

### User Interests
- HIGH: geopolitics, tech/AI, privacy/surveillance
- MEDIUM: economic policy, France/Canada specific
- FILTER: celebrity, sports, lifestyle, US domestic (unless international)

### Tiers
- Must Know (2-4): Stories you'd be embarrassed not to know
- Should Know (3-6): Important but not urgent
- Quick Signals (5-10): One-liners worth tracking
- Target: 15-25 stories total

## Output

Write HTML to `data/output/digest-TIMESTAMP.html` (use `date -u '+%Y-%m-%d-%H%MZ'`):

```html
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    body { font-family: Georgia, serif; max-width: 700px; margin: 0 auto; padding: 20px; line-height: 1.6; }
    h1 { border-bottom: 2px solid #333; padding-bottom: 10px; }
    h2 { color: #444; margin-top: 30px; border-bottom: 1px solid #ccc; }
    .story { margin-bottom: 25px; }
    .headline { font-weight: bold; font-size: 1.1em; }
    .summary { margin: 8px 0; }
    .why { color: #555; font-style: italic; }
    .sources { font-size: 0.9em; color: #666; }
    .sources a { color: #0066cc; }
    .signal { margin: 8px 0; }
    .signal a { color: #0066cc; }
    .footer { margin-top: 40px; padding-top: 20px; border-top: 1px solid #ccc; font-size: 0.85em; color: #777; }
  </style>
</head>
<body>
  <h1>News Digest</h1>
  <p>[Day], [Month] [Day], [Year]</p>

  <h2>Must Know</h2>
  <div class="story">
    <div class="headline">► HEADLINE [TODAY/BREAKING/UPDATE]</div>
    <div class="summary">Summary paragraph...</div>
    <div class="why">WHY IT MATTERS: ...</div>
    <div class="sources">Sources: <a href="url">Source Name</a> (bias)</div>
  </div>

  <h2>Should Know</h2>
  <div class="story">
    <div class="headline">► HEADLINE</div>
    <div class="summary">Summary...</div>
    <div class="why">WHY IT MATTERS: ...</div>
    <div class="sources">Sources: <a href="url">Source Name</a> (bias)</div>
  </div>

  <h2>Quick Signals</h2>
  <div class="signal">• Signal headline — <a href="url">Source</a></div>

  <div class="footer">Generated: YYYY-MM-DD HH:MM UTC | 28 sources</div>
</body>
</html>
```

Write headlines to `data/shown_headlines.json`:

```json
[
  {"headline": "Headline text", "tier": "must_know"},
  {"headline": "Another headline", "tier": "should_know"},
  {"headline": "Signal text", "tier": "quick_signal"}
]
```
