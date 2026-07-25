# Handover — sources, wire provenance, and a lot of corrected assumptions

**2026-07-25.** Resume in a fresh session. Forward queue lives in `.claude/tasks/todo.md`
under *Queued 2026-07-25*; this file is the narrative and the state.

## State in one paragraph

`main` is **16 commits ahead of origin, unpushed and undeployed**. Everything is CI-green
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
  `docs/solutions/best-practices/an-aggregate-rating-and-a-single-rater-rating-are-different-scales.md`
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
— see `docs/solutions/best-practices/verify-the-validation-run-contains-the-code-under-test.md`.

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
