# Daily News Digest

Generate a personalized news digest from balanced sources.

## Step 1: Read Input

1. Read `/app/data/input.json` for previous headlines (deduplication)
2. Read `/app/data/fetched/_metadata.json` for source info
3. Read all JSON files from `/app/data/fetched/` for articles

**input.json structure**: `{previous_headlines: [{headline, tier, date}]}`
**Article structure**: `{title, url, published, summary}`
**Metadata**: `sources[source_id] = {name, bias, perspective}`

## Step 2: Process

### Deduplication (CRITICAL)
1. DO NOT repeat stories from `previous_headlines` in input.json
2. Only re-include if there's a MAJOR new development - mark with [UPDATE]
3. Same story from different sources = same story

### Content Rules
- DO NOT fabricate details beyond what's in the article summary
- Use hedging language when uncertain ("reportedly", "according to")

### User Interests
- HIGH: geopolitics, international relations, technology/AI, privacy/surveillance
- MEDIUM: economic policy, systems/complexity, France/Canada specific
- FILTER OUT: celebrity, sports, lifestyle, US domestic policy unless international implications

### Processing Steps
1. Deduplicate (same URL or >85% similar titles)
2. Filter noise
3. Cluster into narratives
4. Tier: Must Know (2-4), Should Know (3-6), Quick Signals (5-10)
5. Target 15-25 stories total

## Step 3: Output Digest

Get UTC timestamp:
```bash
date -u '+%Y-%m-%d-%H%MZ'
```

Ensure output directory exists:
```bash
mkdir -p /app/data/output
```

Write to `/app/data/output/digest-YYYY-MM-DD-HHMMZ.txt`:

```
NEWS DIGEST
===========
[Day], [Month] [Day], [Year]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
MUST KNOW
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

► HEADLINE [TODAY/BREAKING/UPDATE]
  Summary paragraph...

  WHY IT MATTERS: Significance and second-order effects...

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

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Generated: YYYY-MM-DD HH:MM UTC | 28 sources
```

## Step 4: Output Headlines

Write shown headlines to `/app/data/shown_headlines.json`:

```json
[
  {"headline": "Headline 1", "tier": "must_know"},
  {"headline": "Headline 2", "tier": "should_know"},
  {"headline": "Signal headline", "tier": "quick_signal"}
]
```

Include ALL headlines from the digest.
