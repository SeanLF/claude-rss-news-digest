# Daily News Digest

Generate a personalized news digest from CSV input files.

## Input Files

**CRITICAL: You MUST read EVERY file listed below. Do NOT skip any article files. Read ALL of them before generating the digest.**

Read all CSV files from `data/claude_input/`:
- `previously_shown.csv` - Headlines shown in last 7 days (DO NOT repeat these)
- `sources.csv` - Source metadata (id, name, bias, perspective)
- `articles_*.csv` - Articles split across files (source_id, title, url, published, summary)

**Reading articles_1.csv through articles_N.csv is MANDATORY. Never claim you have "read enough" or skip files. Read every single one.**

## Processing Rules

### Deduplication (CRITICAL)
1. DO NOT repeat stories from previously_shown.csv
2. Only re-include if MAJOR new development - mark with [UPDATE]
3. Same story from different sources = same story

### User Interests
- HIGH: geopolitics, tech/AI, privacy/surveillance
- MEDIUM: economic policy, France/Canada specific
- FILTER: celebrity, sports, lifestyle, US domestic (unless international)
- FILTER: trivial controversies, culture war bickering, political theatre with no substance

### Tiers
- Must Know (3+): Stories you'd be embarrassed not to know
- Should Know (5+): Important but not urgent
- Quick Signals (10+): One-liners worth tracking
- Below the Fold: Regional/tech roundup of remaining stories, clustered by geography and topic, ordered by impact within each cluster. Include stories not covered above.
- Be comprehensive, not conservative

## Writing Style

Write like The Economist meets AP wire: clear, authoritative, zero fluff.

### Principles
- **Brevity**: Short sentences. Short words. Cut every unnecessary word.
- **Inverted pyramid**: Lead with the most important fact. Details follow.
- **Precision**: Specific over vague. "12 killed" not "many casualties."
- **Hedging**: Use "reportedly", "according to" when not verified firsthand.

### Avoid
- Journalese clichés: "sparked concerns", "sent shockwaves", "slammed", "blasted"
- Sensational adjectives: "explosive", "shocking", "massive", "unprecedented"
- Passive hedging: "It is believed that" → "Officials say"
- Editorializing: Report facts, let reader judge significance
- Unexplained acronyms: Expand on first use or avoid entirely (FDI → foreign direct investment)

### Headlines
- Sentence case, not title case
- Active voice: "Russia claims village" not "Village claimed by Russia"
- Include key actor and action
- Tag: [UPDATE] only for developments on previously covered stories (check previously_shown.csv)

### "Why It Matters"
- Must add genuine insight beyond the headline
- Connect to broader trends, explain stakes, note what to watch
- One sentence, maximum two. No filler.

### Summaries
- 2-3 sentences maximum
- First sentence: the news (who did what)
- Second sentence: essential context
- DO NOT fabricate or infer beyond what's in the article summary
- Be factual: report what happened, don't speculate on motives or implications not stated

## Output

Write HTML to `data/output/digest-TIMESTAMP.html` (use `date -u '+%Y-%m-%d-%H%MZ'`):

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>News Digest – [Mon] [DD], [YYYY]</title>
  <style>:root{--bg:#fafafa;--text:#1a1a1a;--text-secondary:#555;--text-muted:#777;--border:#ddd;--accent:#c45a3b;--accent-muted:#d4897a;--link:#1a5f7a;--tag-bg:#c45a3b;--tag-text:#fff}@media(prefers-color-scheme:dark){:root{--bg:#141414;--text:#e8e8e8;--text-secondary:#b0b0b0;--text-muted:#888;--border:#2a2a2a;--accent:#e07a5f;--accent-muted:#c45a3b;--link:#7cc5e3;--tag-bg:#e07a5f;--tag-text:#141414}}body{font-family:Georgia,"Times New Roman",serif;max-width:600px;margin:0 auto;padding:24px 16px;line-height:1.75;color:var(--text);background:var(--bg);font-size:18px}@media(min-width:768px){body{max-width:820px;font-size:20px;padding:40px 32px}}header{border-left:4px solid var(--accent);padding-left:16px;margin-bottom:32px}header time{font-size:1.4em;font-weight:700;letter-spacing:-0.5px;display:block}header .stats{color:var(--text-muted);font-size:0.85em;margin-top:4px}.summary{background:var(--border);padding:16px 20px;margin-bottom:32px;font-size:0.92em;line-height:1.6}.summary strong{color:var(--accent)}section{margin-bottom:36px}section>h2{color:var(--accent);font-size:0.75em;font-weight:600;text-transform:uppercase;letter-spacing:2px;margin:0 0 20px 0;padding-bottom:8px;border-bottom:1px solid var(--border)}article{margin-bottom:28px}article h3{margin:0 0 8px 0;font-size:1.1em;font-weight:600;line-height:1.4}article p{margin:8px 0;font-size:0.95em}article .why{color:var(--text-secondary);border-left:2px solid var(--accent-muted);padding-left:12px;margin:12px 0}article .sources{font-size:0.8em;color:var(--text-muted);margin-top:10px}article .sources a{color:var(--link);text-decoration:none}.signals{font-size:0.9em}.signal{margin:12px 0;padding-left:16px;position:relative;line-height:1.5;color:var(--text-secondary)}.signal::before{content:"•";position:absolute;left:0;color:var(--accent)}.signal a{color:var(--link);text-decoration:none}.cluster{margin-bottom:24px}.cluster h3{font-size:0.95em;font-weight:600;margin:0 0 12px 0;color:var(--text)}footer{margin-top:48px;padding-top:16px;border-top:1px solid var(--border);font-size:0.75em;color:var(--text-muted)}</style>
</head>
<body>
  <header>
    <time datetime="YYYY-MM-DDTHH:MMZ">[Weekday], [Month] [DD], [YYYY] · HH:MM UTC</time>
    <div class="stats">[N] articles from [N] sources → [N] stories</div>
  </header>

  <div class="summary">
    <p><strong>Americas:</strong> [3+ sentences on US, Canada, Latin America news]</p>
    <p><strong>Europe:</strong> [3+ sentences on EU, UK, European news]</p>
    <p><strong>Asia-Pacific:</strong> [3+ sentences on China, Japan, Southeast Asia, Australia news]</p>
    <p><strong>Middle East & Africa:</strong> [3+ sentences on regional news]</p>
    <p><strong>Tech:</strong> [3+ sentences on technology, AI, privacy news]</p>
  </div>

  <section>
    <h2>Must Know</h2>
    <article>
      <h3>Headline in sentence case [UPDATE]</h3>
      <p>Summary: who did what, essential context.</p>
      <p class="why"><strong>Why it matters:</strong> Genuine insight connecting to broader trends.</p>
      <p class="sources"><a href="url">Source Name</a> (bias)</p>
    </article>
  </section>

  <section>
    <h2>Should Know</h2>
    <article>
      <h3>Headline in sentence case</h3>
      <p>Summary paragraph.</p>
      <p class="why"><strong>Why it matters:</strong> Analysis.</p>
      <p class="sources"><a href="url">Source Name</a> (bias)</p>
    </article>
  </section>

  <section>
    <h2>Quick Signals</h2>
    <div class="signals">
      <p class="signal">Brief headline with key fact — <a href="url">Source</a></p>
    </div>
  </section>

  <section>
    <h2>Below the Fold</h2>
    <div class="cluster">
      <h3>🌎 Americas</h3>
      <p class="signal">Story not covered above, ordered by impact — <a href="url">Source</a></p>
    </div>
    <div class="cluster">
      <h3>🌍 Europe</h3>
      <p class="signal">Story — <a href="url">Source</a></p>
    </div>
    <div class="cluster">
      <h3>🌏 Asia-Pacific</h3>
      <p class="signal">Story — <a href="url">Source</a></p>
    </div>
    <div class="cluster">
      <h3>🌍 Middle East & Africa</h3>
      <p class="signal">Story — <a href="url">Source</a></p>
    </div>
    <div class="cluster">
      <h3>🤖 Tech</h3>
      <p class="signal">Story — <a href="url">Source</a></p>
    </div>
  </section>

  <footer>Generated YYYY-MM-DD HH:MM UTC</footer>
</body>
</html>
```

Write headlines to `data/shown_headlines.json`:

```json
[
  {"headline": "Headline text", "tier": "must_know"},
  {"headline": "Another headline", "tier": "should_know"},
  {"headline": "Signal text", "tier": "quick_signal"},
  {"headline": "Below fold story", "tier": "below_fold", "cluster": "americas"}
]
```
