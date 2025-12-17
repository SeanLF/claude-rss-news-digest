# Daily News Digest (Plain Text)

Generate a personalized news digest from 28 balanced sources. Output plain text for email.

## Step 1: Fetch & Context

```bash
cd /workspace && python3 fetch_feeds.py
```

Get previously shown narratives (CRITICAL for deduplication):
```bash
sqlite3 /workspace/digest.db "SELECT date(shown_at) as date, tier, headline FROM shown_narratives WHERE shown_at > datetime('now', '-7 days') ORDER BY shown_at DESC;"
```

## Step 2: Process & Generate

### Reading Sources
1. Read `fetched/_metadata.json` for source info
2. Read ALL JSON files from `fetched/`

**Article JSON structure**: `{title, url, published, summary}`
**Source metadata**: `_metadata.json` has `sources[source_id] = {name, bias, perspective}`

### Content Rules
- DO NOT fabricate details beyond what's in the summary
- Use hedging language when uncertain ("reportedly", "according to")

### Deduplication (CRITICAL)
1. DO NOT repeat stories from the SQLite query above
2. Only re-include if there's a MAJOR new development - mark with [UPDATE]
3. Same story from different sources = same story

### User Interests
- HIGH: geopolitics, international relations, technology/AI, privacy/surveillance
- MEDIUM: economic policy, systems/complexity, France/Canada specific
- FILTER OUT: celebrity, sports, lifestyle, US domestic policy unless international implications

### Processing
1. Deduplicate (same URL or >85% similar titles)
2. Filter noise
3. Cluster into narratives
4. Tier: Must Know (2-4), Should Know (3-6), Quick Signals (5-10)
5. Target 15-25 stories

## Step 3: Generate Plain Text

Get UTC timestamp:
```bash
date -u '+%Y-%m-%d-%H%MZ'
```

Write to `/workspace/output/digest-YYYY-MM-DD-HHMMZ.txt` using this format:

```
NEWS DIGEST
===========
[Day], [Month] [Day], [Year]
~10 min read

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
MUST KNOW
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

► HEADLINE [TODAY/BREAKING/UPDATE]
  Summary paragraph...

  WHY IT MATTERS: Significance and second-order effects...

  HOW IT AFFECTS YOU: Personal implications if applicable...

  REPORTING VARIES:
  • WSJ: Frames as...
  • Guardian: Emphasizes...

  Sources: Source Name (bias) <url>

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SHOULD KNOW
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

► HEADLINE
  Summary...

  WHY IT MATTERS: ...

  Sources: ...

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
QUICK SIGNALS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

• Signal headline — Source <url>
• Signal headline — Source <url>

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Generated: YYYY-MM-DD HH:MM UTC | 28 sources
```

## Step 4: Record

```bash
sqlite3 /workspace/digest.db "INSERT INTO digest_runs (timezone, articles_fetched, narratives_presented) VALUES ('UTC', [N], [M]);"
```

Record shown narratives:
```bash
sqlite3 /workspace/digest.db "INSERT INTO shown_narratives (headline, tier, shown_at) VALUES
('[Headline 1]', 'must_know', datetime('now')),
...;"
```

## Source Reference

| Source | Bias | Perspective |
|--------|------|-------------|
| Globe and Mail, CBC | center | Canadian |
| Al Jazeera | center | Middle East |
| Reuters, AP, AFP | center | Wire |
| SCMP (3 feeds) | center | Hong Kong/Asian |
| Le Monde | center | French |
| Der Spiegel | center-left | German |
| FT, Economist, WSJ | center-right | Finance/UK/US |
| Guardian, NYT, WaPo | center-left | UK/US |
| HN, Ars, Verge, RoW | center | Tech |
| ProPublica | center-left | Investigative |
| The Intercept | left | Investigative |
| Nikkei Asia | center-right | Japanese |
| The Hindu | center | Indian |
| Straits Times | center | Singaporean |
| Daily Maverick | center-left | South African |
| Rappler | center | Filipino |
