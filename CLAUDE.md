# Claude Instructions

## Project

Automated news digest: RSS feeds → Claude curation → HTML email.

Single file architecture: everything is in `run.py` (~400 lines).

## File Layout

```
run.py sections:
├── Configuration    # Paths, RSS sources list
├── Utilities        # Logging, internet check
├── Database         # Schema, init, last run time
├── RSS Fetching     # Parallel feed fetching
├── Claude Input     # Prepare CSVs for slash command
├── Digest Generation # Claude invocation via /news-digest
├── Email            # Resend API (HTML)
└── Main Pipeline    # Orchestration
```

Runtime data in `data/`: digest.db, fetched/, output/, claude_input/, digest.log

## Running the Digest

### Locally (Interactive)

```bash
# 1. Ensure Claude Code CLI is installed and authenticated
claude --version

# 2. Install dependencies
pip install feedparser resend

# 3. Copy the slash command to your local Claude config
cp .claude/commands/news-digest.md ~/.claude/commands/

# 4. Set environment variables (or copy .env.example to .env)
export RESEND_API_KEY=re_xxxxx      # Get from https://resend.com/api-keys
export RESEND_FROM=onboarding@resend.dev  # Or your verified domain
export DIGEST_EMAIL=you@example.com  # Comma-separated for multiple

# 5. Run the pipeline
python run.py

# Or with options:
python run.py --dry-run    # No email, no DB record
python run.py --no-email   # Record to DB, skip email
python run.py --no-record  # Send email, skip DB record
```

### Via Docker (Automated)

```bash
./run-digest.sh
```

## How run.py Works

1. **Fetches RSS** - Pulls all feeds in parallel with retry (3 attempts, exponential backoff), filters by last run time
2. **Prepares CSV files** - Splits articles into ~10k token chunks for Claude to read
3. **Invokes Claude** - Runs `/news-digest` slash command
4. **Sends email** - Delivers HTML digest via Resend API
5. **Records history** - Saves shown headlines to SQLite for deduplication

## The /news-digest Slash Command

Located at `.claude/commands/news-digest.md` (copied to Docker container at build time).

When invoked, Claude must:
1. Read ALL CSV files from `data/claude_input/` (previously_shown.csv, sources.csv, articles_*.csv)
2. Deduplicate against previously_shown.csv
3. Generate HTML digest with tiers: Must Know, Should Know, Quick Signals, Below the Fold
4. Write to `data/output/digest-TIMESTAMP.html`
5. Write shown headlines to `data/shown_headlines.json`

**CRITICAL**: Claude must read EVERY article file. Never skip files.

## Content Curation Rules

- Never fabricate details beyond RSS title/summary
- Never repeat stories from last 7 days unless major development (mark [UPDATE])
- Filter out: celebrity, sports, lifestyle, US domestic policy (unless international), trivial controversies
- Prioritize: geopolitics, tech/AI, privacy, France/Canada news
- Expand acronyms on first use (FDI → foreign direct investment)
- Be factual, don't speculate

## Database

SQLite at `data/digest.db`. Tables:
- `digest_runs` - metadata per run (run_at, articles_fetched, etc.)
- `shown_narratives` - headlines shown (for 7-day deduplication)

## Output Format

HTML email with:
- **Regional summary** at top (Americas, Europe, Asia-Pacific, ME&Africa, Tech)
- **Must Know** (3-4 stories): Major headlines with "Why it matters"
- **Should Know** (5-8 stories): Important but not urgent
- **Quick Signals** (10-15): One-liners
- **Below the Fold**: Regional clusters with emoji headers

## Don't

- Don't skip article files
- Don't skip deduplication
- Don't hardcode paths or emails
- Don't fabricate details not in the RSS summary
