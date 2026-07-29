# Handover — sources, wire provenance, and a lot of corrected assumptions

**2026-07-25.** Resume in a fresh session. Forward queue lives in `.claude/tasks/todo.md`
under *Queued 2026-07-25*; this file is the narrative and the state.

## State in one paragraph

`main` is **unpushed and undeployed** (15 commits ahead of origin at time of writing; `git log origin/main..main` is the authority). Everything is CI-green
(Python + Rust). A deploy-readiness review returned **GO-WITH-CAVEATS** and its blocking
findings are fixed. The next production run is the real test. Nothing has been applied to
the production database.

## What changed

**Incident fixes**
- **Thread linker** returned correct matches as JSON *strings* and a strict `isinstance(int)`
  discarded all 16, so run 244 shipped with zero thread continuity. Coerced digit strings,
  made the prompt's example type-consistent (`null`, not `"NEW"`), and made total/partial
  link loss loud. Verified against the live model on run 244's real input.
- **Alerting had been dead for months.** Terraform wrote `DIGEST_ALERT_EMAIL`; the code reads
  `HEALTH_ALERT_EMAIL`. Nothing read the former. Fixed in the infra repo (`e38e9b9`, on
  `main` there — *unpushed but deployable*, terraform reads the local checkout and
  `null_resource.news_digest_env` is in `bin/deploy`'s `-target` list). App side now logs the
  dropped payload at ERROR and checks the recipient at startup, non-fatally.
- **`the_hindu` has 403'd since 2026-07-07** — Cloudflare managed challenge on the Hetzner
  ASN. 403 from prod with any UA including none, even `/robots.txt`; 200 from a residential
  IP with the same bare UA. Their robots.txt permits `/feeder/`, so it is bot-scoring
  collateral, not policy. Unresolved; the fix is a human email asking for an allowlist.

**Feed and catalog**
- `der_spiegel` had returned **zero usable articles for 15 consecutive runs** — not broken,
  its English section publishes ~1 piece per 5 days. Switched to the German `/ausland` desk.
- `reuters` and `nikkei_asia` are Google News `site:` searches with no time bound, so they
  wasted 33% and 93% of their slots on stale items (nikkei's spanned 357 days). Added
  `when:1d`.
- Added **UPI** (the only Reuters/AP-class wire with a working direct feed), **France 24**
  (AFP proxy — AFP has no public feed), **Clarín Mundo** (first South American source, new
  `S. America` region). 31 outlets / 38 feeds.
- **Every rating re-read from MBFC**; 18 of 35 moved. Reuters is now the only `very-high`.
  Attribution switched from Ground News to MBFC everywhere. Note the framing correction in
  `docs/lessons/best-practices/an-aggregate-rating-and-a-single-rater-rating-are-different-scales.md`
  — much of that diff is a scale re-base, not error.

**Wire provenance**
- `feeds.py` keeps `entry.author`; `wire_agency()` exact-matches 23 agencies (never
  substring); `wire_from_dateline()` is start-anchored. `prepare.py` writes `wire_agency`
  into `article_index`; `render.py` shows "AFP · via SCMP" with leaning `wire`.
- **Verified live at 189/1268 articles (14.9%)** on a real run.
- Keying `collapse_reposts` on the agency was tried and **reverted** — it over-collapsed four
  distinct Reuters articles into one and stopped a verbatim repost from folding. Labelling
  survives; merging does not.

## How it was validated

Three local pipeline runs. **The first two were void and it took an outside prompt to notice**
— see `docs/lessons/best-practices/verify-the-validation-run-contains-the-code-under-test.md`.

1. **Void** — `docker compose run` does not rebuild, so it ran a 5-day-old image with the old
   `sources.json` (which is `COPY`d into the image, not mounted).
2. **Partly useful** — correct image, but `data/digest.db` was 22 days stale so the 7-day
   dedup blocklist was empty and dedup was inert. Corpus inflated to 1,268 articles against
   production's 366-604, which produced two SDK idle timeouts. **Those timeouts were an
   artifact; do not treat them as a finding.** It did establish three real things: every
   shipped headline was **English** despite der_spiegel in 8 stories, france24 in 6 and
   clarin_mundo in 3; `wire_agency` populates end-to-end; der_spiegel went from 0 to
   appearing in 8 of ~16 shipped stories.
3. **Valid** — verified image, prod DB cloned and integrity-checked (page_count × page_size ==
   filesize, `integrity_check ok`, 854 rows in the dedup window). **Completed exit 0.**

   | | |
   |---|---|
   | Corpus | 231 kept of 2,717 fetched, 37/38 sources (france24 failed *locally only*, see below) |
   | Window | since prod's 10:25 run, so ~10h not 24h — counts are not directly comparable to prod |
   | Cluster | 215 articles → 91 clusters, $0.30 |
   | Select | 112s, $0.29 — **no idle timeout**, confirming (2)'s timeouts were corpus-size artifacts |
   | Write | 335s, $0.63 |
   | Coherence | 220s, **$1.64 — the most expensive stage**, matching the `/open` finding |
   | Shipped | 3 must_know + 12 should_know |

   What it proves:
   - **Zero non-English headlines** across all 15 stories, with der_spiegel in 3 and
     clarin_mundo in 3. The unconstrained-language risk does not materialise.
   - **`wire_agency` populates at 41/215 (19.1%)** — reuters 30, upi 8, AFP/dpa/PA Media 1 each.
   - New sources contribute: der_spiegel 3 stories, clarin_mundo 3, upi 1.
   - **The alerting fix fired, live, on real history:**
     `ALERTING MISCONFIGURED (HEALTH_ALERT_EMAIL unset): source-health alert DROPPED … persistently
     failing: the_hindu (10x)` — the exact information that was invisible for 18 days. Note "10x"
     against a true 18 days: `get_consecutive_failures` caps at 10.
   - COHERENCE stripped two `why_it_matters` for claims absent from cited sources (a June that
     "already overwhelmed emergency services"; "already absorbing US tariff pressures"). Working.

   Unexplained and minor: `data/digest.log` and `.log.1` both reset to 11K mid-run with only
   migration lines, while the run's own stdout stayed intact. Tests do patch `DATA_DIR`, so it
   is not obvious test bleed. The stdout capture is the authoritative record; on-disk logs were
   not trustworthy for this run.

**Local runs still diverge from production in two ways that cannot be fixed locally:**
`the_hindu` fetches fine from a residential IP but is blocked from the production ASN, and
**France 24 geo-routes** — `/en/rss` returns 200 with 24 items from prod, but 301s to an HTML
index that 403s from a Canadian IP. France 24 failed in run 3 for that reason alone; it is
healthy from prod. That URL is not a canonical feed endpoint and is worth hardening.

## Open decisions (yours)

- **Deploy.** Not raised again here; the tree is ready and the timing is yours.
- **Run the thread repair.** `ThreadStore.merge_thread` is built and tested but **never
  called from the pipeline** — it exists for a one-off repair of the 5 duplicate threads run
  244 created. Dry-run script proven against a copy of the real tables; it restores day counts
  4/6/10/26/5, including a 25-day arc carrying 106 open questions. Needs the deployed image.
- **`the_hindu`**: email for an allowlist, or replace. LiveMint is the only rated alternative
  measured (`mixed` factuality); Hindustan Times is disqualified (MBFC Low Credibility).
- **Two prompt/editorial calls**: nothing instructs the models to write in English (observed
  to be fine, but unconstrained), and the `/stats` bias bar still counts a wire repost under
  the republisher's lean while the table beside it says `wire`.

## Watch on the first production run after deploy

1. Did it send. Recipient count in the log.
2. `[der_spiegel]` and `[clarin_mundo]` kept/fetched counts, and whether a German or Spanish
   story produced an English headline.
3. Absence of the `HEALTH_ALERT_EMAIL` startup ERROR — if it appears, terraform did not apply
   and alerting is still blind.
4. Source-table rows reading "X · via Y" that credit an outlet's own reporting to a wire.
5. From ~day 3: dedup drops of non-English titles at 0.80-0.90 (`prepare.py` logs the matched
   headline and similarity). That is the multilingual stopword risk surfacing.

## The most valuable thing found, and it is not in the diff

`dedup_log` has **24,218 rows** recording every cross-day drop with its similarity score, and
`fetched_articles` has **97,762 articles across 147 runs** with title/url/published/summary.
The eval corpus that was reconstructed by hand from live feeds all afternoon already existed
in the database.

Reading the borderline band (0.80-0.90) shows the digest **silently drops the resolution of a
story because it covered the anticipation**: "Jimmy Lai *to be sentenced* Monday" ran, so
"Jimmy Lai *sentenced to 20 years*" was dropped; Portugal's runoff ran, so the final result
was dropped. Templated serial headlines also collide with themselves ("Here's the latest.",
"Here's What Happened … on Monday/Thursday"). Roughly 3-4 of 14 sampled drops. Only 770 of the
24,218 sit at today's 0.80 threshold — the rest are historical at 0.35, which is where the
recorded "65% false-positive" figure came from, so that number is stale in your favour.

**This outranks the wire-provenance work.** It is a live product bug, it has ground truth
already recorded, and losing the end of a story is exactly what a reader notices.

## Calibration note for whoever picks this up

Eleven assertions in this session were corrected by measurement, review, or Sean's questions —
including a feature that was a complete no-op through 841 green tests, a PoC whose "failure"
was a broken ground truth, and two validation runs that validated nothing. The queue in
`todo.md` deliberately carries evidence per item rather than intentions. **Anything not on
that list should start with a measurement, not a patch.**

Research briefs worth not repeating: prior art in
`docs/2026-07-25-feed-sourcing-findings.md` (METER, Rodier & Carter, NEWS-COPY — unigram and
hapax containment beat higher-order n-grams on short news text; off-the-shelf embeddings lose
to 3-gram Jaccard; skip LSH at this scale). Blindspot is **closed NO-GO** with 39 runs of
evidence — do not re-litigate without changing the source catalog.

## Analytics backtest — measured over 194 runs

Prompted by the observation that the day's failures were all *already recorded* and simply
never queried. Prototypes in `scratchpad/poc-analytics/`.

**The reframe that matters: five of the seven known failures are CHRONIC, not degradations.**
They were present from the first run that had the feed, so change detection cannot catch them
by construction. Per-source yield is stationary across five 40-run blocks (`der_spiegel`
0/0/0/0/0%, `nikkei_asia` 8/9/9/9/11%); degenerate summaries are 100% of `reuters` and
`nikkei_asia` output in every run; unresolved redirect links appear in **211 of 220 issues**
back to 2025-12-08. The "per-source baseline health check" proposed earlier in this session
would therefore have caught almost none of them. It needs to be two instruments: a quiet
daily alert set (change detection) and a standing catalog audit (absolute invariants).

**Four checks survived backtesting**, precision in brackets: source-down at 3 consecutive
failures, 0.005/run [1/1]; empty feed = HTTP 200 with zero entries, 0.021/run [4/4]; short
freshness window <18h, 0.015/run [2/2]; unsafe dedup drop, 0.46/run [11/11]. The first
**already exists** at `db.py:363` and would have caught `the_hindu` on **day 3, not day 18** —
detection was never the problem, the alert email was dead. Add a monthly liveness heartbeat:
an alert path with no proof of life is not a control.

**Checks killed, with evidence — do not re-propose without new data.** Yield-collapse vs a
trailing median fired 0.45/run and *every* fire was a Sunday or Monday (the 10:25 UTC Sunday
run covers Saturday). Feed-stopped-updating survived weekday-matching down to 9 fires, and
**every actionable one was a public holiday** — Easter, SA Freedom Day, Memorial Day,
Juneteenth, July 4. Technically correct, operationally worthless. Unresolved-link share is
chronic and fires on 90% of issues.

**Two findings nobody went looking for:**

- **Google News link resolution has never worked in production** — not degraded, never worked.
  0% resolution on 38 of 39 runs. Across issues 204-244, `reuters.com` appears in shipped HTML
  **exactly once** while `news.google.com` appears 8-31 times per issue. The 429 observed on
  2026-07-25 is the steady state, not an incident.
- **Five feeds cover only a fraction of each 24h window** (measured as the age of the oldest
  *kept* article): `rappler` 4.3h, `financial_times` 6.4h, **`al_jazeera` 8.7h — the
  4th-most-cited source**, `le_monde` 14.6h. Each keeps ~100% of what it serves, so the entry
  cap is binding. The mirror image of the stale-slots problem.

**Email size is NOT measurable from the database.** `digests.html` is the *web* copy —
`prepare_for_web()` strips email-only nodes and MSO conditionals before storing, so stored
sizes are 42-74KB against a real email of ~120KB. Querying the DB would tell you the opposite
of the truth. Assert `len(email_html) < 102400` before `send_broadcast` and persist
`digests.email_bytes`.

**Worth instrumenting, since the data cannot substitute:** `source_health.newest_item_at` /
`oldest_item_at` / `entries_returned` (makes feed truncation first-class rather than
inferred), an age histogram of *discarded* articles (`fetched_articles` stores only kept
rows), and per-run gnews counters plus a canary asserting zero `news.google.com` links in the
shipped digest — without which the link problem stays invisible even after a fix.

**One caution on the audit:** `stories cited/run` is a *usage* metric. Pruning the catalog on
it will quietly narrow coverage toward whatever the curation stages already favour;
`rest_of_world` at zero citations may be a genuine editorial gap rather than dead weight.
Treat it as a prompt to look, not a rule to act on.
