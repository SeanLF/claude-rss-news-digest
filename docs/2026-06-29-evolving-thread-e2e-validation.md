# Evolving story-thread — live e2e validation + go/no-go (2026-06-29)

The validation the prior sessions never did: a **full live pipeline run pulling real RSS**, with
threads on, linking today's stories against a backfilled multi-day history. Prior trials all used
archived run-215 data. This is the evidence for the enable decision.

## What was run

1. **Rebuilt** the `digest-newsroom` image so migrate/backfill/e2e exercise committed production
   code (the prior image predated the thread modules — no scratch `src` mounts needed now).
2. **Applied** the three thread migrations to the local clone (`data/digest.db`, runs 1-215).
3. **Backfilled** A+B over archived runs 204-215 with current production code → real
   faithful-by-construction deltas as history.
4. **Live e2e**: `THREADS_ENABLED=true THREAD_LATEBIND=true … run.py --no-email --force` (recording
   ON so threads persist). Fetch → cluster → recap → select → write → coherence → threads → render.

## Results

### Backfill (history)
- **132 threads, 25 multi-day, 62 active** at run 215. Matches the validated replay shape
  (heatwave, Starmer, Lebanon, Hormuz, Venezuela, Iran strikes all collapse into coherent
  multi-day threads).
- Heatwave thread evolves day-by-day, every fact cites that day's article IDs: heat dome → 40
  drowned → Britain's record 36.1 C → Switzerland/Germany all-time records → Germany 41.3 C →
  records across Germany/Denmark/Czech. **Grounding spot-check passed**: run-212 fact "Britain's
  hottest June day, 36.1 C [A291]" — A291 is Straits Times "Britain swelters in hottest June day
  on record". Faithful, not just plausible.

### Live run (run 216, today's real articles)
- 448 articles fetched from **35/35 sources**; 290 sent to curation after dedup; 17 stories
  selected (5 must_know, 12 should_know).
- **Threads: 17 stories → 8 continued, 9 new; 7 installments synthesized; 0 audit fail-open.**
- **Linker precision 8/8** — every continuation is a correct, near-verbatim label match, zero
  over-merges:
  US-Iran/Hormuz, Venezuela quakes, Europe heatwave, Ukraine refinery strikes, Israel-Lebanon
  ceasefire, South Africa protests, Burnham/UK-next-PM, Japan-SK jets.
- Deltas read as genuine "what's new today", all attributed (e.g. Russia-Ukraine day 6: "Putin
  publicly acknowledged for the first time that Ukrainian drone strikes … have caused fuel
  shortages"; Hormuz day 4: "US and Iran agree to halt attacks … meet in Doha June 30, according
  to a U.S. official").
- **Render verified visually** (headless-Chrome screenshot): "ONGOING · DAY N" terracotta badge
  beside the headline, delta replaces the summary, new stories carry no badge. Production-quality.

### Cost (measured, not estimated)
Run 216 `run_usage` rows, with `THREAD_LATEBIND=true`:

| | $ |
|---|---|
| curation (cluster/recap/select/write/coherence) | 1.6923 |
| thread_synthesis (7 rows) | 0.1904 |
| thread_audit (7 rows) | 0.0627 |
| **thread total** | **0.2531** |
| grand total run 216 | 1.9454 |

Thread overhead = **~$0.25/run (~15% over curation, ~8-10% over a typical $2.5-3/day)** with
late-binding on. The Haiku linker (~$0.004) isn't separately attributed.

**This is ~2x the handoff's $0.13/run estimate.** Two reasons: (a) the estimate omitted the
`thread_audit` cost entirely (~$0.06/run), and (b) `THREAD_LATEBIND` widens the synthesis input
(+29% coverage → ~30% more synthesis tokens). Even threads-only (late-bind off) lands ~$0.18-0.20
once audit is counted — the $0.13 figure undercounted.

## Go / No-Go

**Recommendation: GO — enable `THREADS_ENABLED`. It is a real, faithful, well-rendered
returning-reader improvement, and all pre-prod gates are green.**

Decision on `THREAD_LATEBIND` is a separate cost/quality knob: it roughly doubles thread cost
(~$0.13 → ~$0.25) for +29% article coverage. Reasonable to ship `THREADS_ENABLED` first
(cheaper, simpler) and add late-binding once threads have proven themselves in prod.

Caveats:
- **n=1 live run.** Backfill replay is n=12 and the linker/synthesis are well-validated offline,
  but only one true live run exists. Watch the first few prod days.
- **Cost is ~2x the documented estimate** (above). Not a blocker — ~10% of daily spend — but the
  $0.13 number in older docs is wrong; use ~$0.25 (late-bind on) / ~$0.20 (off).
- **OAuth token rate-limits under burst.** The first backfill returned 0 continuations across all
  12 runs purely because the digest OAuth token was rate-limited; it recovered and a paced re-run
  (5s/run) worked perfectly. The **prod backfill must be paced**, or it will silently produce an
  all-NEW (history-less) thread table.

## Launch sequence (turn-key — flags are off by default; nothing here flips them until step 3)

The tooling now exists (committed): the thread flags are plumbed through terraform, and
`bin/seed-threads` does the paced prod backfill. Steps:

1. **Deploy** (`bin/deploy`): builds the image *with* the thread code + `seed_threads.py` + the
   linker recall fix, pushes the commits, applies the additive migrations. Safe no-op while the
   flags are off — the digest is byte-identical.
2. **Seed prod history** — on the server, while `THREADS_ENABLED` is still false (the seed runs
   invisibly), paced:
   ```
   docker run --rm --env-file /opt/news-digest/.env -v news-digest-data:/app/data \
     <registry>/digest-newsroom:latest .venv/bin/python src/seed_threads.py --reset
   ```
   `--dry-run` first to confirm the run range; the per-run "N continued" log is the rate-limit
   canary (all-zeros = token exhausted, re-run). Then clone prod down and eyeball a few threads.
3. **Enable**: set `news_digest_threads_enabled = "true"` (and optionally
   `news_digest_thread_latebind = "true"`) in `../seanfloyd.dev/.../terraform.tfvars`, then
   `terraform apply`. The one-shot container reads the regenerated `.env` on the next scheduled
   run — no restart needed.

Terraform plumbing (variables + heredoc + `env_hash` trigger) is staged uncommitted in the
`seanfloyd.dev` infra repo for review. `terraform validate` passes; defaults are `"false"`.

## The linker recall fix (landed `2742dd2`, 2026-06-29)

A re-threading bug surfaced while reviewing this run: storylines whose label drifted got a fresh
thread_id mid-stream (e.g. Lebanon split day-8 + day-3), undercounting continuity. Fixed by
showing the linker each thread's recent label arc; full re-backfill confirms the fragments
collapse to single coherent threads (Lebanon day 10, UK day 8) with zero over-merges. The seed
produced for launch carries this fix. Detail: `docs/2026-06-29-thread-relinking-recall-fix.md`.

## Tooling notes
- The paced backfill is now a committed tool: `bin/seed-threads` → `newsroom/src/seed_threads.py`
  (reads archived runs from `run_artifacts`, `--reset`/`--dry-run`/`--runs-back`/`--from/--to`/
  `--latebind`). It supersedes the gitignored `scratch/cluster-replay/thread_backfill.py`.
- `scratch/cluster-replay/trial_render.py` is stale (references dropped `prev_narrative`/
  `narrative`); not needed — the live e2e renders through production `attach_thread_context`.
