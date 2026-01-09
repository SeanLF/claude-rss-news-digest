# News Digest - Pass 2: Write HTML

Format the curated selections into HTML digest and tracking JSON.

## Input

Read `data/claude_input/selections.json` which contains:
- `must_know`: Array of stories with headline, summary, why_it_matters, sources
- `should_know`: Array of stories with headline, summary, why_it_matters, sources
- `quick_signals`: Array of stories with headline and source
- `below_fold`: Object with regional clusters (americas, europe, asia_pacific, middle_east_africa, tech)
- `regional_summary`: Object with 3+ sentence summaries per region
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
  <title>News Digest – {{DATE}}</title>
  <style>:root{--bg:#fafafa;--text:#1a1a1a;--text-secondary:#555;--text-muted:#777;--border:#ddd;--accent:#c45a3b;--accent-muted:#d4897a;--link:#1a5f7a;--tag-bg:#c45a3b;--tag-text:#fff;--notice-bg:#f5f0eb}@media(prefers-color-scheme:dark){:root{--bg:#141414;--text:#e8e8e8;--text-secondary:#b0b0b0;--text-muted:#888;--border:#2a2a2a;--accent:#e07a5f;--accent-muted:#c45a3b;--link:#7cc5e3;--tag-bg:#e07a5f;--tag-text:#141414;--notice-bg:#1e1a17}}body{font-family:Georgia,"Times New Roman",serif;max-width:600px;margin:0 auto;padding:24px 16px;line-height:1.75;color:var(--text);background:var(--bg);font-size:18px}@media(min-width:768px){body{max-width:820px;font-size:20px;padding:40px 32px}}header{border-left:4px solid var(--accent);padding-left:16px;margin-bottom:32px}header time{font-size:1.4em;font-weight:700;letter-spacing:-0.5px;display:block}header .stats{color:var(--text-muted);font-size:0.85em;margin-top:4px}.ai-notice{background:var(--notice-bg);padding:12px 16px;margin-bottom:24px;font-size:0.82em;line-height:1.5;border-radius:4px;color:var(--text-secondary)}.ai-notice strong{color:var(--text)}.summary{background:var(--border);padding:16px 20px;margin-bottom:32px;font-size:0.92em;line-height:1.6}.summary strong{color:var(--accent)}section{margin-bottom:36px}section>h2{color:var(--accent);font-size:0.75em;font-weight:600;text-transform:uppercase;letter-spacing:2px;margin:0 0 20px 0;padding-bottom:8px;border-bottom:1px solid var(--border)}article{margin-bottom:28px}article h3{margin:0 0 8px 0;font-size:1.1em;font-weight:600;line-height:1.4}article p{margin:8px 0;font-size:0.95em}article .why{color:var(--text-secondary);border-left:2px solid var(--accent-muted);padding-left:12px;margin:12px 0}article .sources{font-size:0.8em;color:var(--text-muted);margin-top:10px}article .sources a{color:var(--link);text-decoration:none}.signals{font-size:0.9em}.signal{margin:12px 0;padding-left:16px;position:relative;line-height:1.5;color:var(--text-secondary)}.signal::before{content:"•";position:absolute;left:0;color:var(--accent)}.signal a{color:var(--link);text-decoration:none}.cluster{margin-bottom:24px}.cluster h3{font-size:0.95em;font-weight:600;margin:0 0 12px 0;color:var(--text)}footer{margin-top:48px;padding-top:16px;border-top:1px solid var(--border);font-size:0.75em;color:var(--text-muted)}footer a{color:var(--link);text-decoration:none}</style>
</head>
<body>
  <header>
    <time>{{TIMESTAMP}}</time>
    <div class="stats">[articles_reviewed] articles from [sources_used] sources → [stories_selected] stories</div>
  </header>

  <div class="ai-notice">
    <strong>About this digest:</strong> Curated and written by Claude (Opus 4.5), an AI assistant. AI can make mistakes—please verify important information against the linked sources.
  </div>

  <div class="summary">
    <p><strong>Americas:</strong> [regional_summary.americas]</p>
    <p><strong>Europe:</strong> [regional_summary.europe]</p>
    <p><strong>Asia-Pacific:</strong> [regional_summary.asia_pacific]</p>
    <p><strong>Middle East & Africa:</strong> [regional_summary.middle_east_africa]</p>
    <p><strong>Tech:</strong> [regional_summary.tech]</p>
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
    <p>Don't want these emails? <a href="mailto:contact@seanfloyd.dev?subject=Unsubscribe%20from%20News%20Digest&body=Hi%2C%0A%0APlease%20remove%20me%20from%20the%20News%20Digest%20mailing%20list.%0A%0AThanks!">Let me know</a> and I'll remove you.</p>
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

1. **Use the EXACT CSS provided** - do not modify colors, fonts, or layout
2. **Use the EXACT HTML structure** - sections must be in order: header, summary, Must Know, Should Know, Quick Signals, Below the Fold, footer
3. **Keep `{{DATE}}` and `{{TIMESTAMP}}` placeholders exactly as shown** - they will be replaced after file is written
4. **Include ALL stories** from selections.json - do not skip any
5. **Headlines must match exactly** between HTML and shown_headlines.json
6. **Stats in header** must use values from selections.json stats object
7. **Cluster emojis** must be: 🌎 Americas, 🌍 Europe, 🌏 Asia-Pacific, 🌍 Middle East & Africa, 🤖 Tech
8. **JSON format for shown_headlines.json** must be an array of objects with headline, tier, and optional cluster keys
