# News Digest - Selection

## Task

Read CSV files from `data/claude_input/`, select noteworthy stories, output via `write_selections` tool.

**Input files:**
- `sources.csv` — source metadata (id, name, bias, perspective)
- `articles_*.csv` — articles split across files (source_id, title, url, published, summary)
- `recent_headlines.csv` — headlines from last 7 days (headline, date)

**You MUST read every article file.** Do not skip any or claim "read enough."

Note: Duplicate stories have been pre-filtered. If the same event appears from multiple sources, combine them (don't repeat).

### Continuity
Reference `recent_headlines.csv`. The goal is avoiding redundancy while catching genuinely new developments. Skip stories already covered unless significant new facts emerged.

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

### Preheader
One sentence capturing the 2-3 biggest stories of the day. This appears as the email inbox preview and under each date on the archive page. No links, no markdown. Max 150 characters.

### Signals
- One-liner + source link per story
- Group by region

### Sources
- Copy URLs exactly from articles
- Bias labels must match sources.csv

---

## Final Check

**Before calling write_selections, verify:**

1. Same event from multiple sources is combined, not repeated

---

## CRITICAL INSTRUCTION

You MUST call the `write_selections` MCP tool to complete this task.

- Do NOT just describe what you would do
- Do NOT output JSON to stdout
- Do NOT explain the tool schema
- ACTUALLY INVOKE the tool with your selections

This task is incomplete until write_selections is called.
