# News Digest - Pass 2: Write HTML

Format the curated selections into HTML digest and tracking JSON.

## Input

Read `data/claude_input/selections.json` which contains:
- `must_know`: Array of stories with headline, summary, why_it_matters, sources
- `should_know`: Array of stories with headline, summary, why_it_matters, sources
- `quick_signals`: Array of stories with headline and source
- `below_fold`: Object with regional clusters (americas, europe, asia_pacific, middle_east_africa, tech)
- `regional_summary`: Object with narrative summaries per region (contains markdown links to convert)
- `stats`: Object with articles_reviewed, sources_used, stories_selected

## Output 1: HTML Digest

Write to `data/output/digest-TIMESTAMP.html` (use `date -u '+%Y-%m-%d-%H%MZ'` for TIMESTAMP).

**You MUST use this EXACT template structure and CSS. Do not modify the styling.**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{DIGEST_NAME}} – {{DATE}}</title>
  <style>{{STYLES}}</style>
</head>
<body>
  <header>
    <time>{{TIMESTAMP}}</time>
  </header>

  <div class="ai-notice">
    <strong>About this digest:</strong> Curated and written by Claude (Opus 4.5), an AI assistant. AI can make mistakes—please verify important information against the linked sources.
  </div>

  <div class="summary">
    <p><span class="region">🌍 Europe:</span> [regional_summary.europe]</p>
    <p><span class="region">🌎 Americas:</span> [regional_summary.americas]</p>
    <p><span class="region">🌏 Asia-Pacific:</span> [regional_summary.asia_pacific]</p>
    <p><span class="region">🌍 Middle East & Africa:</span> [regional_summary.middle_east_africa]</p>
    <p><span class="region">🤖 Tech:</span> [regional_summary.tech]</p>
  </div>

  <section>
    <h2>Must Know</h2>
    <!-- For each item in must_know array: -->
    <article>
      <h3>[headline]</h3>
      <p>[summary]</p>
      <p class="why"><strong>Why it matters:</strong> [why_it_matters]</p>
      <p class="sources"><a href="[url]">[name]</a> ([bias]) · <a href="[url2]">[name2]</a> ([bias2])</p>
    </article>
  </section>

  <section>
    <h2>Should Know</h2>
    <!-- For each item in should_know array: -->
    <article>
      <h3>[headline]</h3>
      <p>[summary]</p>
      <p class="why"><strong>Why it matters:</strong> [why_it_matters]</p>
      <p class="sources"><a href="[url]">[name]</a> ([bias])</p>
    </article>
  </section>

  <section>
    <h2>Quick Signals</h2>
    <div class="signals">
      <!-- For each item in quick_signals array: -->
      <p class="signal">[headline] — <a href="[source.url]">[source.name]</a></p>
    </div>
  </section>

  <section>
    <h2>Below the Fold</h2>
    <div class="cluster">
      <h3>🌎 Americas</h3>
      <!-- For each item in below_fold.americas: -->
      <p class="signal">[headline] — <a href="[source.url]">[source.name]</a></p>
    </div>
    <div class="cluster">
      <h3>🌍 Europe</h3>
      <!-- For each item in below_fold.europe: -->
      <p class="signal">[headline] — <a href="[source.url]">[source.name]</a></p>
    </div>
    <div class="cluster">
      <h3>🌏 Asia-Pacific</h3>
      <!-- For each item in below_fold.asia_pacific: -->
      <p class="signal">[headline] — <a href="[source.url]">[source.name]</a></p>
    </div>
    <div class="cluster">
      <h3>🌍 Middle East & Africa</h3>
      <!-- For each item in below_fold.middle_east_africa: -->
      <p class="signal">[headline] — <a href="[source.url]">[source.name]</a></p>
    </div>
    <div class="cluster">
      <h3>🤖 Tech</h3>
      <!-- For each item in below_fold.tech: -->
      <p class="signal">[headline] — <a href="[source.url]">[source.name]</a></p>
    </div>
  </section>

  <footer>
    <p>Don't want these emails? <a href="mailto:contact@seanfloyd.dev?subject=Unsubscribe%20from%20News%20Digest&body=Hi%2C%0A%0APlease%20remove%20me%20from%20the%20News%20Digest%20mailing%20list.%0A%0AThanks!">Let me know</a> and I'll remove you from the mailing list.</p>
  </footer>
</body>
</html>
```

## Output 2: Headlines Tracking JSON

Write to `data/shown_headlines.json`:

```json
[
  {"headline": "Exact headline from must_know", "tier": "must_know"},
  {"headline": "Exact headline from should_know", "tier": "should_know"},
  {"headline": "Exact headline from quick_signals", "tier": "quick_signal"},
  {"headline": "Exact headline from below_fold.americas", "tier": "below_fold", "cluster": "americas"},
  {"headline": "Exact headline from below_fold.europe", "tier": "below_fold", "cluster": "europe"},
  {"headline": "Exact headline from below_fold.asia_pacific", "tier": "below_fold", "cluster": "asia_pacific"},
  {"headline": "Exact headline from below_fold.middle_east_africa", "tier": "below_fold", "cluster": "middle_east_africa"},
  {"headline": "Exact headline from below_fold.tech", "tier": "below_fold", "cluster": "tech"}
]
```

## Critical Requirements

1. **Use the EXACT HTML structure** - sections must be in order: header, summary, Must Know, Should Know, Quick Signals, Below the Fold, footer
2. **Keep placeholders exactly as shown** (`{{DIGEST_NAME}}`, `{{DATE}}`, `{{TIMESTAMP}}`, `{{STYLES}}`) - replaced after file is written
3. **Include ALL stories** from selections.json - do not skip any
4. **Headlines must match exactly** between HTML and shown_headlines.json
5. **Cluster emojis** must be: 🌎 Americas, 🌍 Europe, 🌏 Asia-Pacific, 🌍 Middle East & Africa, 🤖 Tech
6. **JSON format for shown_headlines.json** must be an array of objects with headline, tier, and optional cluster keys
7. **Convert markdown links to HTML** in regional_summary: `[text](url)` becomes `<a href="url">text</a>`
