# News Digest

A transparent, self-hostable AI news desk you run yourself. Every morning it reads 37 feeds across five continents, clusters the day's stories, decides what matters, writes a bias-labelled briefing, fact-checks its own work, and emails it -- no human edits any issue. Clone it and run your own for a few dollars a day, or read the [live instance](https://news-digest.seanfloyd.dev).

Every choice it makes is inspectable: real [subscriber and cost numbers](https://news-digest.seanfloyd.dev/stats), every source [labelled by political bias and factuality](https://news-digest.seanfloyd.dev/sources), and the code that does it all right here.

## What makes it different

- **Fully autonomous.** No human writes, edits, or approves any issue. The pipeline fetches, curates, writes, and fact-checks itself, then sends.
- **Claude never sees a URL.** The pipeline assigns opaque article IDs (`A1`, `A2`, ...) and the model curates and writes referencing only those IDs; the pipeline resolves them back to sources afterward. Curation can't be swayed by domain, and a malicious feed can't inject a link into the output.
- **Specialized stages, deterministically orchestrated.** `CLUSTER -> RECAP -> SELECT -> WRITE -> COHERENCE -> REPAIR`, each a Claude Agent SDK call with its own prompt and schema, sequenced by a [Temporal](https://temporal.io) workflow: a crashed worker resumes where it stopped, and nothing runs twice.
- **It fact-checks itself, then repairs itself.** A `COHERENCE` pass re-reads every headline and summary against its source articles. Anything that fails is first regenerated from its own cited sources, changing as little as possible, and re-checked; only what still fails is dropped, before send rather than after.
- **Cheap clustering by design.** Grouping is a deterministic extract-then-join (entities + event + time per article, then a join), not a holistic LLM pass over everything. Lower cost, less drift.
- **Evolving story threads.** Ongoing stories are tracked across days, so a returning reader sees what changed rather than a fresh fragment.
- **Radical transparency.** A public [stats page](https://news-digest.seanfloyd.dev/stats) shows real subscriber numbers, source balance across the political spectrum, and the AI cost per issue. Every source is [labelled by bias and factuality](https://news-digest.seanfloyd.dev/sources), and the code is right here.

## What an issue looks like

[![The masthead of a recent issue of Sean's Daily Digest, with its AI-written disclaimer and the lead must-know story](docs/assets/issue-screenshot.png)](https://news-digest.seanfloyd.dev/today)

Each story carries a headline, a summary, a why-it-matters note, how the reporting varies across outlets, and the political balance of its sources. [See today's issue](https://news-digest.seanfloyd.dev/today).

## Architecture

- **The worker** (`digest/`, TypeScript on Temporal): `fetch -> prepare -> recap -> cluster -> select -> full text -> write (one call per story) -> preheader -> coherence -> repair -> assemble -> threads -> render -> pre-send checks -> broadcast`. Sonnet 5 writes and fact-checks; Sonnet 4.6 extracts for clustering, selects, repairs and evolves threads; Haiku writes the recaps, the inbox preview line and the thread links. A run that fails a pre-send check holds for 15 minutes so the operator can approve or reject it.
- **The Python worker** (`digest/python/`): full-text extraction with trafilatura, the one step that stays in Python.
- **The site** (`digest/src/site`, TypeScript): the online archive, per-issue pages (also as Markdown and JSON for agents), the sources and stats pages, story threads, and subscriptions.
- **Postgres** holds the product database and Temporal's own. One small box runs all of it.

The decisions behind this are in [the decision record](docs/2026-09-24-web-tier-and-ops-decisions.md); running it is in [the runbook](docs/2026-09-23-temporal-cutover-runbook.md).

## Quick Start

### Prerequisites

- Docker (the Makefile assumes [OrbStack](https://orbstack.dev) for its local domains; plain ports work too)
- A Claude subscription (Max or Pro), for the model calls
- A [Resend](https://resend.com) account, for production only (free tier: unlimited broadcasts to 1,000 contacts)

### 1. Clone and configure

```bash
git clone https://github.com/SeanLF/claude-rss-news-digest.git
cd claude-rss-news-digest
cp .env.example .env
```

The dev stack needs one value in `.env`: `CLAUDE_CODE_OAUTH_TOKEN`, from `claude setup-token`. Everything
else it sets itself, including a fake Resend.

### 2. Run the dev stack

```bash
make dev-up         # Temporal and the workers, the site, a Postgres, and resend-fake
make digest-start   # today's run; watch it at http://127.0.0.1:8233
make dev-urls       # where the site, the caught mail and the Temporal UI answer
```

Mail in the dev stack goes to `resend-fake` and nowhere else, whatever `.env` says. `make help` lists the rest; [docs/operations.md](docs/operations.md) explains them.

### 3. Schedule

A Temporal schedule starts the run each day: `make digest-schedule` creates it on the dev stack. Production's fires at 12:25 Europe/Paris.

## Sources

37 feeds from 30 outlets across five continents, spanning the political spectrum. Bias, factual reporting and credibility are taken from [Media Bias/Fact Check](https://mediabiasfactcheck.com), read per outlet so each rating traces to one published assessment; 26 of 37 feeds rate High or Very High for factual reporting, 10 Mostly Factual or Mixed, and one (Hacker News) is unrated. Every source is shown with its bias and factuality on the live [sources page](https://news-digest.seanfloyd.dev/sources). See [`digest/catalogue/sources.json`](digest/catalogue/sources.json) for the full list.

## Cost

Roughly a few dollars a day in API-equivalent cost (Sonnet for the reasoning stages, Haiku for recap). The live [stats page](https://news-digest.seanfloyd.dev/stats) shows the current per-issue number.

## Troubleshooting

| Problem | Fix |
|---------|-----|
| A run fails or stalls | Open it in the Temporal UI (`make dev-urls`); in production, `bin/ops journal` and the runbook |
| No mail in dev | It is in resend-fake (`make dev-urls`); nothing in the dev stack reaches Resend |
| The worker will not start | Check `CLAUDE_CODE_OAUTH_TOKEN` in `.env` and the worker's first log line (`docker compose logs digest-worker`) |

## Status

Production has run this since 2026-09-25 (`deploy/2026-09-25-035737Z`); before that, a Python pipeline and a Rust web server did, and both are deleted (their last commit is `ae5f03d`). What was verified before the cut-over, and how, is in the [runbook](docs/2026-09-23-temporal-cutover-runbook.md) and the `docs/proposed/` evidence it links.

## More

- **Operations and the dev stack** -- [docs/operations.md](docs/operations.md)
- **Architecture and dev context** -- [CLAUDE.md](CLAUDE.md)

## License

[PolyForm Noncommercial License 1.0.0](LICENSE) -- free to use, modify, and share
for any noncommercial purpose (personal, research, education, nonprofits). Commercial
use, including by for-profit organizations, is not permitted. This is a
source-available licence, not an OSI open-source one.
