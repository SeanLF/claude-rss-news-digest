# News Digest - Pass 1: Selection

## BLOCKLIST - Read First

`previously_shown.csv` contains headlines from the last 7 days. **These are banned.**

- If a story matches the blocklist (exact OR semantic match), **skip it entirely**
- Do NOT include blocked stories with notes like "Previously shown"
- Do NOT include blocked stories as signals
- **Only exception**: Major new development → prefix headline with [UPDATE]

Semantic matches = same underlying event:
- "Train crash kills 21" ≈ "Train crash kills 40" (same incident)
- "PM calls election" = "PM calls snap election, betting on popularity"
- Same story from different sources = same story (combine sources, don't repeat)

---

## Task

Read CSV files from `data/claude_input/`, select noteworthy stories, output via `write_selections` tool.

**Input files:**
- `previously_shown.csv` — blocklist (date, headline)
- `sources.csv` — source metadata (id, name, bias, perspective)
- `articles_*.csv` — articles split across files (source_id, title, url, published, summary)

**You MUST read every article file.** Do not skip any or claim "read enough."

---

## Selection Criteria

### Interests
| Priority | Topics |
|----------|--------|
| HIGH | geopolitics, tech/AI, privacy/surveillance |
| MEDIUM | economic policy, France/Canada specific |
| FILTER | celebrity, sports, lifestyle, US domestic* |

*US domestic exception: include only if it directly affects other countries' policies, economies, or citizens. "Markets watching" or "world reacts" is NOT sufficient.

### Tiers

**must_know** (3+ stories)
Stories you'd be embarrassed not to know. Major geopolitical shifts, significant deaths, major policy changes.

**should_know** (5+ stories)
Important but not urgent. Developing situations, notable policy moves, significant tech announcements.

**signals** (grouped by region)
One-liners worth tracking. Everything noteworthy that didn't make the tiers above.
Regions: americas, europe, asia_pacific, middle_east_africa, tech

**Be comprehensive.** Include more rather than fewer.

---

## Writing Style

The Economist meets AP wire: clear, authoritative, zero fluff.

**Do:**
- Short sentences, short words
- Lead with most important fact
- Be specific: "12 killed" not "many casualties"
- Hedge unverified claims: "reportedly", "according to"

**Don't:**
- Journalese: "sparked concerns", "sent shockwaves", "slammed"
- Sensationalism: "explosive", "shocking", "unprecedented"
- Editorializing: report facts, let reader judge
- Unexplained acronyms

**Headlines:** Sentence case. Active voice. Key actor + action.

**Summaries (must_know + should_know only):**
- 2-3 sentences max
- First = the news (who did what). Second = context.
- Don't fabricate beyond what's in the article summary

**Why it matters (must_know + should_know only):**
- One sentence of genuine insight
- Connect to broader stakes, not just restate the headline

**Reporting varies (must_know only, optional):**
- Only when sources genuinely frame the story differently
- 2-3 perspectives max, focus on framing differences
- Skip if all sources report it the same way

---

## Output Format

Use `write_selections` tool. Schema defines structure.

### Regional Summaries
Narrative paragraphs with inline markdown links. Synthesize must_know and should_know stories.

```
"americas": "Nicaragua [released prisoners](https://...) under US pressure. Canada's [Freeland resigned](https://...) from Parliament."
```

- Link the action, not generic words
- Weave into narrative, don't list
- 3-5 sentences per region
- Only reference must_know and should_know stories

### Signals
- One-liner + source link per story
- Do NOT duplicate stories from regional_summary
- Group by region

### Sources
- Copy URLs exactly from articles
- Bias labels must match sources.csv

---

## Final Check

**Before calling write_selections, verify:**

1. Every headline checked against `previously_shown.csv`
2. No blocklist matches in must_know, should_know, OR signals
3. No story appears in both regional_summary and signals
