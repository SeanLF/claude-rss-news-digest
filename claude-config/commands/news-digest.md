# Daily News Digest

Generate a personalized news digest from CSV input files.

## Input Files

Read all CSV files from `/app/data/claude_input/`:
- `headlines.csv` - Previous headlines (DO NOT repeat these)
- `sources.csv` - Source metadata (id, name, bias, perspective)
- `articles_*.csv` - Articles split across files (source_id, title, url, published, summary)

## Processing Rules

### Deduplication (CRITICAL)
1. DO NOT repeat stories from headlines.csv
2. Only re-include if MAJOR new development - mark with [UPDATE]
3. Same story from different sources = same story

### User Interests
- HIGH: geopolitics, tech/AI, privacy/surveillance
- MEDIUM: economic policy, France/Canada specific
- FILTER: celebrity, sports, lifestyle, US domestic (unless international)

### Tiers
- Must Know (2-4): Stories you'd be embarrassed not to know
- Should Know (3-6): Important but not urgent
- Quick Signals (5-10): One-liners worth tracking
- Target: 15-25 stories total

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

### Headlines
- Sentence case, not title case
- Active voice: "Russia claims village" not "Village claimed by Russia"
- Include key actor and action
- Tags: [TODAY] for breaking, [UPDATE] for developments on prior stories

### "Why It Matters"
- Must add genuine insight beyond the headline
- Connect to broader trends, explain stakes, note what to watch
- One sentence, maximum two. No filler.

### Summaries
- 2-3 sentences maximum
- First sentence: the news (who did what)
- Second sentence: essential context
- DO NOT fabricate beyond article summary

## Output

Write HTML to `/app/data/output/digest-TIMESTAMP.html` (use `date -u '+%Y-%m-%d-%H%MZ'`):

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>News Digest</title>
  <style>
    body { font-family: Georgia, "Times New Roman", serif; max-width: 680px; margin: 0 auto; padding: 24px 16px; line-height: 1.65; color: #1a1a1a; }
    header { border-bottom: 3px solid #1a1a1a; padding-bottom: 12px; margin-bottom: 28px; }
    header h1 { margin: 0; font-size: 1.75em; letter-spacing: -0.5px; }
    header time { color: #666; font-size: 0.95em; }
    section { margin-bottom: 32px; }
    section > h2 { color: #1a1a1a; font-size: 1.1em; text-transform: uppercase; letter-spacing: 1px; margin: 0 0 16px 0; padding-bottom: 8px; border-bottom: 1px solid #ddd; }
    article { margin-bottom: 24px; }
    article h3 { margin: 0 0 6px 0; font-size: 1.05em; font-weight: 600; line-height: 1.35; }
    article p { margin: 6px 0; font-size: 0.95em; }
    article .why { color: #444; }
    article .sources { font-size: 0.85em; color: #666; }
    article .sources a { color: #1a5f7a; }
    .signals { font-size: 0.92em; }
    .signal { margin: 10px 0; line-height: 1.5; }
    .signal a { color: #1a5f7a; }
    footer { margin-top: 40px; padding-top: 16px; border-top: 1px solid #ddd; font-size: 0.8em; color: #888; }
  </style>
</head>
<body>
  <header>
    <h1>News Digest</h1>
    <time datetime="YYYY-MM-DD">Wednesday, December 17, 2025</time>
  </header>

  <section>
    <h2>Must Know</h2>
    <article>
      <h3>Headline in sentence case [TODAY]</h3>
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

  <footer>Generated YYYY-MM-DD HH:MM UTC</footer>
</body>
</html>
```

Write headlines to `/app/data/shown_headlines.json`:

```json
[
  {"headline": "Headline text", "tier": "must_know"},
  {"headline": "Another headline", "tier": "should_know"},
  {"headline": "Signal text", "tier": "quick_signal"}
]
```
