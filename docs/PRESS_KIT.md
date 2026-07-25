# Press Kit

Talking points and demo notes for writing about, or posting about, news-digest.
Every claim here is checkable against the repo or the live instance. If you
cannot verify it, do not repeat it.

## One-line pitch

An autonomous AI news desk that fact-checks its own output, repairs what fails,
and publishes the error rate.

## Longer description

news-digest reads 35 RSS feeds across five continents each morning, clusters the
day's stories, decides what matters, writes a bias-labelled briefing, verifies
every claim against its source articles, repairs the ones that fail, and emails
the result. No human writes, edits, or approves any issue. It runs on a single
2-vCPU server for a few dollars a day, and every decision it makes is
inspectable on the web.

## Key URLs

| Link | What it is |
|---|---|
| [news-digest.seanfloyd.dev](https://news-digest.seanfloyd.dev) | Live instance, today's issue |
| [/issues](https://news-digest.seanfloyd.dev/issues) | Full archive |
| [/stats](https://news-digest.seanfloyd.dev/stats) | Real subscriber and cost numbers, published |
| [/sources](https://news-digest.seanfloyd.dev/sources) | Every feed, labelled by political bias and factuality |
| [GitHub](https://github.com/SeanLF/claude-rss-news-digest) | Source (PolyForm Noncommercial 1.0.0) |

## What to show

**1. The self-correction record.** The differentiator. `COHERENCE` re-reads
every headline and summary against its cited sources; failures are regenerated
from those same sources with minimal edits and re-checked; whatever still fails
is dropped before send. Every attempt lands in `repair_log.jsonl`.

*Show:* a `repair_log.jsonl` entry -- what was flagged, what changed, which
sources justified it, whether the re-check passed.

**2. Claude never sees a URL.** Python assigns opaque article IDs (`A1`, `A2`,
...). The model curates and writes referencing only those IDs; Python resolves
them back to sources afterward. Curation cannot be swayed by domain reputation,
and a malicious feed cannot inject a link into the output.

*Show:* `article_index.json` next to `draft_selections.json` -- the model's
output contains IDs, nothing else.

**3. Five subagents, deterministically orchestrated.** `CLUSTER -> RECAP ->
SELECT -> WRITE -> COHERENCE`, plus a repair phase. Each is a file-based Claude
Agent SDK subagent reading and writing JSON, run in fixed order by Python, so
the parent context stays small and the run is reproducible.

*Show:* `orchestrate.py`, and the intermediate files in `data/claude_input/`.

**4. Published cost and subscriber numbers.** Not a marketing page -- the actual
figures from the database.

*Show:* `/stats`.

**5. Bias and factuality labelling.** Every source carries a Media Bias/Fact
Check bias rating and factual-reporting grade, read per outlet so each one
traces to a published assessment, shown on the source page and beside each
story. The catalog is not uniformly high-rated and the page does not pretend
otherwise: 6 outlets are Mostly Factual and 5 are Mixed.

*Show:* `/sources`, spectrum bar and badges.

**6. It runs on a CX23.** 2 vCPU, 4 GB RAM, roughly $2.50-3.00/day in
API-equivalent model spend. The digest is computed once and broadcast, so the
marginal cost of an additional subscriber is one email send.

## The number, and how to state it honestly

Across 17 archived runs (runs 204-221, 285 headlines checked), `COHERENCE`
flagged **10 headlines, 3.5%**. Median run: zero. 65% of runs had no flags at
all. Worst run: 5 of 17.

Three caveats that must travel with the number:

1. **It is a detection rate, not an error rate.** The true rate is at least
   3.5%. The detector was reframed in July 2026 precisely because the old one
   was undercounting.
2. **The reframed detector is more sensitive**, so the published rate should
   *rise*. That is the measurement improving, not the pipeline degrading.
3. **The sample is 17 runs from one archive window.** Not a lifetime figure.

State it as "here is what we measure and how we measure it," never as a quality
claim.

## Framing notes

**Lead with transparency, not autonomy.** "Fully autonomous" invites the
reader to hunt for the mistake. "It shows its work, including its errors" is
the actually unusual thing and is the claim the repo can back.

**It is source-available, not open source.** PolyForm Noncommercial 1.0.0.
Personal, research, and non-commercial use is permitted; commercial use is not.
Do not call it open source -- it is not an OSI licence, and saying so invites a
correction that costs more credibility than the word gains.

**Do not oversell the fact-checking.** COHERENCE verifies that claims are
supported by the cited source articles. It does not verify that the source
articles are themselves true. That distinction is the honest boundary.

## Not claims we make

- Not a prediction or risk-scoring product. No composite indices, no instability
  scores. See [`solutions/best-practices/a-tuned-composite-score-with-no-ground-truth-is-taste.md`](solutions/best-practices/a-tuned-composite-score-with-no-ground-truth-is-taste.md).
- Not a real-time dashboard. One issue per day, deliberately.
- No claim about readership scale.
