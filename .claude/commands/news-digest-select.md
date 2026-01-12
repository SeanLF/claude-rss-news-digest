# News Digest - Pass 1: Selection

Select and curate stories from CSV input files. Output structured JSON for formatting.

## Input Files

**CRITICAL: You MUST read EVERY file listed below. Do NOT skip any article files.**

Read all CSV files from `data/claude_input/`:
- `previously_shown.csv` - Headlines shown in last 7 days (DO NOT repeat these)
- `sources.csv` - Source metadata (id, name, bias, perspective)
- `articles_*.csv` - Articles split across files (source_id, title, url, published, summary)

**Reading articles_1.csv through articles_N.csv is MANDATORY. Never claim you have "read enough" or skip files. Read every single one before making selections.**

## Processing Rules

### Deduplication (CRITICAL)
1. DO NOT select stories from previously_shown.csv
2. Only re-include if MAJOR new development - mark with [UPDATE] in headline
3. Same story from different sources = same story (combine sources)

### User Interests
- HIGH: geopolitics, tech/AI, privacy/surveillance
- MEDIUM: economic policy, France/Canada specific
- FILTER: celebrity, sports, lifestyle, US domestic (unless international impact)
- FILTER: trivial controversies, culture war bickering, political theatre with no substance

### Tier Definitions
- **must_know** (3+ stories): Stories you'd be embarrassed not to know. Major geopolitical shifts, significant deaths, major policy changes.
- **should_know** (5+ stories): Important but not urgent. Developing situations, notable policy moves, significant tech announcements.
- **quick_signals** (10+ stories): One-liners worth tracking. Brief but noteworthy.
- **below_fold**: Regional/tech roundup of ADDITIONAL worthy stories not already placed in must_know, should_know, or quick_signals. These stories must NOT be mentioned in regional_summary.

**Be comprehensive, not conservative.** Include more stories rather than fewer.

## Writing Style

Write like The Economist meets AP wire: clear, authoritative, zero fluff.

### Principles
- **Brevity**: Short sentences. Short words. Cut every unnecessary word.
- **Inverted pyramid**: Lead with the most important fact.
- **Precision**: Specific over vague. "12 killed" not "many casualties."
- **Hedging**: Use "reportedly", "according to" when not verified firsthand.

### Avoid
- Journalese clichés: "sparked concerns", "sent shockwaves", "slammed", "blasted"
- Sensational adjectives: "explosive", "shocking", "massive", "unprecedented"
- Editorializing: Report facts, let reader judge significance
- Unexplained acronyms: Expand on first use

### Headlines
- Sentence case, not title case
- Active voice: "Russia claims village" not "Village claimed by Russia"
- Include key actor and action
- Add [UPDATE] only for developments on previously covered stories

### Summaries (for must_know and should_know)
- 2-3 sentences maximum
- First sentence: the news (who did what)
- Second sentence: essential context
- DO NOT fabricate beyond what's in the article summary

### "Why It Matters" (for must_know and should_know)
- Must add genuine insight beyond the headline
- Connect to broader trends, explain stakes
- One sentence, maximum two. No filler.

## Output

Write JSON to `data/claude_input/selections.json`:

```json
{
  "must_know": [
    {
      "headline": "Headline in sentence case [UPDATE if applicable]",
      "summary": "2-3 sentence summary of the news and context.",
      "why_it_matters": "1-2 sentence insight on broader significance.",
      "sources": [
        {"name": "Source Name", "url": "https://...", "bias": "center-right"}
      ]
    }
  ],
  "should_know": [
    {
      "headline": "Headline in sentence case",
      "summary": "2-3 sentence summary.",
      "why_it_matters": "1-2 sentence insight.",
      "sources": [
        {"name": "Source Name", "url": "https://...", "bias": "center"}
      ]
    }
  ],
  "quick_signals": [
    {
      "headline": "Brief headline with key fact",
      "source": {"name": "Source Name", "url": "https://...", "bias": "center-left"}
    }
  ],
  "below_fold": {
    "americas": [
      {
        "headline": "Story headline",
        "source": {"name": "Source Name", "url": "https://...", "bias": "center"}
      }
    ],
    "europe": [],
    "asia_pacific": [],
    "middle_east_africa": [],
    "tech": []
  },
  "regional_summary": {
    "americas": "3+ sentences summarizing Americas news from must_know, should_know, and quick_signals ONLY.",
    "europe": "3+ sentences on European news from above-the-fold tiers ONLY.",
    "asia_pacific": "3+ sentences on Asia-Pacific news from above-the-fold tiers ONLY.",
    "middle_east_africa": "3+ sentences on Middle East and Africa news from above-the-fold tiers ONLY.",
    "tech": "3+ sentences on tech news from above-the-fold tiers ONLY. Do NOT mention below_fold stories."
  },
  "stats": {
    "articles_reviewed": 912,
    "sources_used": 26,
    "stories_selected": 45
  }
}
```

### Output Requirements
- All arrays must contain the minimum number of items (must_know: 3+, should_know: 5+, quick_signals: 10+)
- Each below_fold cluster should have 3+ stories if available
- Regional summaries must ONLY cover must_know, should_know, and quick_signals stories (NOT below_fold)
- below_fold stories must NOT overlap with stories mentioned in regional_summary - they are ADDITIONAL stories
- Stats must accurately reflect the input (count articles from all CSV files)
- URLs must be copied exactly from the source articles
- Bias labels must match sources.csv
