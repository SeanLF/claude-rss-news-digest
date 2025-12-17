# Daily News Digest

Generate a personalized news digest.

## Input Data

@/app/data/input.json

The input contains:
- `previous_headlines`: Headlines from last 7 days (DO NOT repeat these)
- `sources`: Source metadata (name, bias, perspective)
- `articles`: Articles by source_id, each with {title, url, published, summary}

## Processing Rules

### Deduplication (CRITICAL)
1. DO NOT repeat stories from `previous_headlines`
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

Write to `/app/data/output/digest-TIMESTAMP.txt` (use `date -u '+%Y-%m-%d-%H%MZ'`):

```
NEWS DIGEST
===========
[Day], [Month] [Day], [Year]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
MUST KNOW
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

► HEADLINE [TODAY/BREAKING/UPDATE]
  Summary paragraph...
  WHY IT MATTERS: ...
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

Write headlines to `/app/data/shown_headlines.json`:

```json
[
  {"headline": "Headline text", "tier": "must_know"},
  {"headline": "Another headline", "tier": "should_know"},
  {"headline": "Signal text", "tier": "quick_signal"}
]
```
