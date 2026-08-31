# Health check, dep bump, and what the clustering/RAG literature says we already know

*2026-08-30. Analytics over runs 251–280 on a fresh prod clone (through today's run 280).
Every number here is from `bin/analytics` or a direct query against that clone.*

## 1. The pipeline is healthier than it has been all month

**The 2026-08-24 prediction came true.** The persistent TODO said to watch run 274 for
`FEED_ERRORS` leaving `run-reliability` "for the first time in 45+ runs". It did, at exactly
run 274, and has stayed gone for seven consecutive runs:

```
runs 251-273   23 runs   FEED_ERRORS on 22 of them   avg 25.9 min   avg $5.68
runs 274-280    7 runs   FEED_ERRORS on  0 of them   avg 19.4 min   avg $5.04
```

Parking The Hindu removed a 3-retry WAF stall from every run. Wall clock fell 25%. All 30 runs
in the window completed, shipped 13–19 stories, and reached all 11 recipients. Zero failures.

## 2. Two memory entries are provably stale — corrected here

**Cost.** The recorded figure is "$2.5–3.0/day, CLUSTER ~38% (~$1.1/run)". Actual, runs 251–280:

| stage | share | $/run |
|---|---|---|
| **coherence** | **32.6%** (+4.5pp) | 1.80 |
| write | 17.8% (−2.7) | 0.98 |
| cluster | 16.0% (−3.0) | 0.88 |
| select | 12.2% (−5.1) | 0.68 |
| repair_recheck | 8.4% (+5.1) | 0.93 |
| repair | 3.9% (+2.2) | 0.43 |

Run cost is **~$5.5/run, roughly 2x the recorded figure**, and **COHERENCE, not CLUSTER, now owns
the bill**. The cost-reduction work in memory is aimed at the wrong stage. Note also that
`repair_recheck` costs **2.2x `repair` itself** — the verification half is the expensive half.

**Cross-day dedup.** Memory carries "BIG OPEN: cross-day dedup TF-IDF @0.35 has 65% false-positive
rate — awaiting Sean's steer." It is not open. The 0.35 filter last fired at **run 220**; run 221
switched to 0.80, and `config.py:58-64` documents the reasoning and cites
`docs/2026-07-02-dedup-poc-findings.md`. It has been settled for 60 runs.

```
threshold  rows    first_run  last_run
0.35       24214   55         220
0.80         504   221        280
```

## 3. The one thing getting worse: coherence failures the repair path does not cover

Memory's Phase-2 finding was "repair covers only ~23% of coherence failures — 10 `why_it_matters`
strips vs 3 repairs — and it's the SAME failure mode." **The gap is widening, not holding.**

Measured over 60 runs (221–280), not two windows. The rise is monotonic across three windows and
run 280 is separately a genuine outlier:

```
runs 221-250 (Jul)         n=29   mean  8.3%   sd  7.5
runs 251-273 (Aug 1-23)    n=23   mean 13.8%   sd  8.3
runs 274-280 (Aug 24-30)   n= 7   mean 21.7%   sd 12.4
run  280                          43.8%  = 3.72 sd above the 221-279 mean (11.5%)
```

`run 280` is the highest in 60 runs. Runs at or above 25% are 243, 261, 262, 263, 271, 276, 279,
280 — **three of the last five runs**, the tightest cluster in the series. Caveat: n=7 with
sd=12.4 is a weakly-estimated window mean; the monotonic three-window rise and the recent
clustering are the load-bearing evidence, not the 21.7% itself.

**The obvious mechanism is REFUTED.** "Thin news day → less grounding → more coherence failure"
does not hold: across 58 runs, `pearson(articles_kept, coherence_fail%) = 0.157` — weak, and the
*wrong sign*. Splitting at the median goes the same way (10.4% below vs 13.9% above). Run 280 had
the **lowest** kept pool in the window (489) and the **highest** fail rate. Article volume is not
the driver.

### CORRECTION: the baseline shift is the checker working as designed

An earlier draft of this doc said the COHERENCE model change was "long before the rise, so not the
cause". **That was wrong, and it inverts the conclusion.** The level shift is exactly there:

```
runs 221-240   mean  6.5%
runs 241-280   mean 14.7%     <- 2.3x, and the best changepoint by mean-difference sits at 243
```

Run 241 is commit `5856f35`, *"adversarial reframe + Sonnet 5 to catch hallucinations prod
missed"* — landed because **run 245 shipped 6 verified hallucinations the old confirmatory prompt
caught 0 of 6 times.** The model change and the adversarial reframe are in one commit and cannot
be separated from this data.

So most of the "rise" is **the checker doing the job it was deliberately rebuilt to do**. A higher
fail rate is the intended effect of that change, not a regression. What it exposes is a *writer*
defect that was previously shipping undetected.

### What run 280 actually was

Not more of the same failure. **A different failure mode.** Six of the seven were
`why_it_matters`-only, and every verdict is **absence**, not contradiction:

> *"no cited source mentions the Strait of Hormuz or Iranian oil market conditions — this causal
> link is fabricated from outside context."*

Regex-testing each flagged claim against all 483 articles in the run: **4 of 6 are absent from the
entire pool**, not just the cited sources. That is prior-knowledge fabrication, and it is a real
WRITE defect — it reaches for geopolitical connective tissue from priors when a story is
strategic/diplomatic rather than a discrete event. Healthy runs (274/278/279) fail differently:
contradictions of a cited source ("source says four journalists, story says five").

**The reader-facing consequence, verified in the shipped `selections.json`:**

```
run 280   6 of 16 shipped stories have an EMPTY why_it_matters   <- 38% of the digest
run 279   0 of 15        run 278   0 of 17        run 277   2 of 13
```

`_REPAIRABLE_FIELDS = frozenset({"headline", "summary"})` (`merge.py:28`) excludes
`why_it_matters` by design — documented as "not repaired in the MVP". So **6 of 7 failures were
structurally repair-ineligible** and were silently blanked. Nothing was dropped, so run health,
`run-reliability` and the story count all read clean while 38% of the digest shipped with its
"why this matters" section missing.

**Cause of the spike on top of the baseline: NOT DETERMINED.** The strongest surviving association
is checker scrutiny — `pearson(coherence output_tokens per story, fail%) = 0.662` (n=29, p<0.001),
with the two healthiest runs (274, 278) having the lowest output/story and 280 the highest. Four
negative controls passed (report bytes r=−0.03; PASS-reason length r=−0.06; excluding 280 entirely
still 0.601). But direction is unidentified — both numbers come from the same call — and run 256
is a counterexample (higher output/story, 6.2% fail). **Ruled out with controls:** fulltext
coverage per run (r=0.065, wrong sign; the *lowest*-coverage run is one of the two healthiest),
WRITE prompt change (35 runs earlier), WRITE context growth (280 had the smallest), WRITE output
style (causal density r=0.14; the highest-density run had 0% fail), model drift.

One finding worth its own ticket regardless: **stories with no fulltext fail at 30.0% vs 14.2%**
(n=469) — but run 280 had only one such story, so it does not explain 280.

Run 280 shipped all 16 stories with `failed_but_shipped = 7` against `repair_calls = 2`. Five of
those seven shipped by **stripping `why_it_matters`**, not by repairing. Nothing is lost loudly;
the story ships with a field blanked. This is the single strongest argument in the data for
doing Phase 2, and it is now the most expensive stage in the pipeline too.

*Caveat, from the query's own header:* `failed_but_shipped` conflates repair with field-blanking,
and the persisted `coherence_report.json` on a repair-on run may already be a post-repair
re-check. The trend is real; the exact split needs a trace read, not this table.

## 4. Five feeds are 56% of all fetch work for 0.5% of the kept pool

```
economist_* (5 feeds)   1,500 fetched/run   3.1 kept/run
ALL feeds               2,695 fetched/run   639  kept/run
```

Each Economist regional feed serves a 300-item deep archive; the 28-hour recency window discards
essentially all of it. **This is the funnel query's "99%+ attrition" — it is not editorial
selectivity, it is five archive feeds.**

**Do NOT delete them — an earlier draft of this doc said to, and that was wrong.** The Economist
is a weekly, so a per-run average hides its shape. By day of week (runs 221–280):

```
        kept/run   citations   must_know
Fri       11.11        10          5      <- 20x volume spike (Thu-evening edition)
Thu        2.75         8          4
Tue        3.13         7          6
Sat        0.56         2          2
```

The Friday spike is real (**20x** the quietest day) but it **does not drive the value**: Friday
converts 100 kept articles into 10 citations (10%), where Tuesday converts 25 into 7 (28%). The
~34 citations over 60 runs arrive *evenly all week*, at a 59% must_know rate against a **54.5%
baseline across all sources** — i.e. ordinary value, genuinely earned, roughly 0.57 stories/run.

**So the fix is fetch-side, not source-side.** `articles_fetched` is **exactly 300 on every feed
on all 59 runs** — a fixed archive page, re-fetched and re-parsed daily for content that has not
changed. Cap items per source, or use conditional GET / ETag. That removes ~56% of fetch work
while dropping **nothing** we currently ship. Deleting the feeds would cost ~3.5% of shipped
stories for no reason.

Two feeds are worse than wasteful — they are inert:

- `haaretz_world`: 35 fetched/run, 23 articles in the pool over 30 runs, **0 citations, lift 0.00**
- `scmp_asia`: 490 pool articles, 11 citations across only 5 distinct stories, lift 0.11

## 5. The thread question backlog is compounding

```
open      1798 questions (76.4%)   74.1% sit on DORMANT threads   mean age 33d, oldest 62d
resolved   554 questions (23.6%)
raised in the last 7 days: 192      resolved in the last 7 days: 17
```

The digest raises questions ~11x faster than it closes them, and **1,333 open questions are on
threads that have gone dormant** — abandoned in all but name. Separately, 81.8% of threads are
never continued (603 of 737), and 39 of those singletons are still marked *active*, so decay is
not reaching them.

---

# Review of the three pending docs

All three are good work: each states its method, each carries its caveats, and two open with a
negative control before claiming anything. Notes below are challenges, not corrections.

## `2026-08-26-temporal-vs-restate-poc.md` — verdict correct, and the strongest argument is understated

The verdict ("neither is justified by the daily pipeline alone") is right and well-evidenced —
both ports built, fault-injected identically, same output. Two pushes:

1. **The memory ceiling is a harder no than the doc makes it.** The doc files Restate's ~4.75 GiB
   of default pools as a tunable. Production is a CX23 with **4 GB total**, already running the
   newsroom container and circulation. That is not a tuning exercise, it is the binding
   constraint, and it belongs in the verdict rather than in the specifics list.

2. **The shared footgun is the finding, and it argues against adoption more than the doc says.**
   Both runtimes retried a *validation verdict* forever. That means today's `retry.py` encodes a
   distinction — validation failure is terminal, transient failure is not — that **neither runtime
   knows**. Adopting either does not delete those 226 lines; it re-encodes the same knowledge in
   a less obvious place. The doc's line-count table reads as a saving that would not be realised.

## `2026-08-26-thread-as-entity-poc.md` — best of the three; one claim is too cheap

Part A as a negative control before anything else is exactly right, and Part D (label stable
0/14 days, content hash stable 0/14 days) is the finding that settles the design: **identity and
continuity are different jobs and must not share a key.** I verified the premise still holds in
today's code — `digest.py:350` is still `by_story = {a["story"]: a ...}`, looked up at line 372
by `cluster_id`, with `_shared_cluster_ids` as the guard.

**But "the cheapest correct fix is to carry the `thread_id` through to render, and delete the
by-label lookup entirely" understates the work.** Selections do not carry a thread id; they carry
`cluster_id`, which `merge._attach_cluster_id` assigns by a coarse "first source that maps to any
cluster" heuristic, and which is itself the non-unique modal label the doc just proved unfit.
Carrying the linker's answer through therefore requires a genuinely unique per-run cluster id
threaded CLUSTER → merge → selections → linker → render. That is a modest refactor, not a
deletion — and it is precisely Part D's "identity: content-derived works" recommendation, so the
doc already contains the answer; it just prices it as free.

Still the most shippable item across all three docs. It needs no new runtime.

## `2026-08-26-bias-bar-independence-audit.md` — finding holds, and it is a differentiator

The 78x within/across-story separation is a proper negative control and the 33.1% headline
survives it. Two additions from this session:

1. **A second, independent stored query carries the same caveat about the same missing field.**
   `story-sourcing-depth` says: *"Distinct source_id is not distinct REPORTING — several feeds can
   be reprinting the same wire copy, so this over-counts independence, and the `perspective` field
   in sources.json is not in the database to correct for it."* That query also shows **17.6% of
   `should_know` stories rest on a single outlet**. Two queries, written separately, both blocked
   on persisting wire provenance. That strengthens the July "persist `entry.author`" recommendation
   from cheap-win to the thing unblocking two measurements at once.

2. **Ground News makes the identical unqualified claim.** Their bias bar is *"calculated based on
   the number of bias-rated sources reporting on the story"* — a raw outlet count, with **no
   published wire-deduplication step at all**, across 50,000+ sources. Their rating-system page
   documents outlet-level bias sourcing (AllSides / Ad Fontes / MBFC) and a Blindspot formula, and
   says nothing about syndication.

   So this is not a defect unique to us; it is the category convention, and nobody in the category
   discloses it. That lowers the urgency of "we are broken" and raises the value of the doc's
   second, product question — crediting the wire origin in the bar would be a real differentiator,
   and it lands squarely on the builder-recognition goal.

---

# Research: clustering and RAG for same-day news

## The one genuinely relevant paper, and what it does and does not transfer

**Škvorc, Ivačič, Hribar & Robnik-Šikonja, "Real-time News Story Identification"
([arXiv:2508.08272](https://arxiv.org/abs/2508.08272), Jul 2025)** — a production media-monitoring
system over 85M articles. Closest published work to this pipeline that exists.

Their **existing production** clustering is: embed → pairwise cosine → threshold → **Louvain
community detection (γ=0.1)** → aggregate. That is an independent confirmation of the shape our
own `docs/2026-06-26-news-clustering-prior-art.md` predicted: cheap per-article representation,
deterministic join, thin refine.

Three results that bear directly on decisions here:

1. **Online clustering is dramatically worse than batch.** DBSTREAM AMI 0.162, TextClust 0.172,
   best online+merging 0.569 — against **0.838 for offline k-means on the same embeddings**.
   *We batch once a day. This is a reason not to chase streaming, and it is the first hard number
   I have seen putting a price on it.*

2. **Named entities help outlier detection but HURT clustering.** Text-only AMI 0.838; text+NEs
   0.791; NEs-only 0.678 — but outlier accuracy goes the other way (0.720 → 0.768). Their
   conclusion is to use entities for outlier detection and cluster **without** them. This is
   independent support for the junk-drawer diagnosis (`entities*3 > primary_event*2` in
   `_tag_bag`) — though note it argues for *reweighting the join*, which our own measurement
   recommended against in favour of a cohesion gate. Two pieces of evidence now point different
   ways; that is worth resolving before either is built.

3. **Summarising articles before embedding makes clustering worse** (0.838 → 0.780).

## The claim I started to make, and the repo's own data that kills it

The paper's embedding table shows a large spread — TF-IDF 0.603 vs BGE-M3 0.838 — and our
recorded PoC result is "MiniLM best 0.497 ARI", with MiniLM near the bottom of their table. The
obvious hypothesis is that we measured the wrong embedder.

**That hypothesis is wrong, and `scratch/cluster-embed/` already refuted it on 2026-06-25:**

| model | best ARI | threshold |
|---|---|---|
| Qwen3-Embedding-0.6B | **0.691** | 0.45 |
| BGE-M3 | 0.683 | 0.45 |
| Qwen3-Embedding-4B | 0.674 | 0.45 |
| mxbai-embed-large-v1 | 0.673 | 0.40 |
| all-MiniLM-L6-v2 | 0.652 | 0.55 |

Five embedders including both of the paper's winners, swept over runs 204–211. The whole spread
is **0.652 → 0.691, ~6% relative** — and a 4B model is *worse* than a 0.6B one.

The reason the paper's spread does not transfer is the gold: theirs is human-labelled, ours is
`clusters.json` from a Sonnet run. Per `feedback_eval_ill_posed_metric`, **Sonnet-vs-Sonnet
self-agreement on this task is ARI 0.60–0.88** — so the entire embedder spread sits *inside the
reference's own noise band* and is not measurable as an improvement at all.

**Conclusion: the embedding model is not the lever here, and no amount of SOTA-chasing on
embedders will show up.** The junk-drawer measurement's own recommendation (a cohesion gate on
the ~17 SELECTED clusters) remains the better-grounded intervention.

## Would RAG help?

**Not as generative RAG, no.** A run's corpus is ~640 kept articles handed to the model as files;
there is no retrieval bottleneck to relieve. What the pipeline actually has is three *retrieval*
problems, and only one has a measured pain:

- **Cross-day dedup** — the classic case for it, and **already retired** (§2). Not a problem.
- **Thread continuity / the linker** — already retrieval, matching on the arc rather than the
  label, and Part D of the thread doc shows why that is the correct design. Working.
- **COHERENCE grounding** — the one live candidate. COHERENCE is now 32.6% of spend, its failure
  rate is climbing, and the residual failures are grounding-starved `why_it_matters` strips (§3).
  Retrieving the specific supporting passage rather than handing whole articles is a plausible
  intervention *for the stage that is actually failing and actually expensive*.

That is the RAG-shaped idea worth a measurement. Frame it before measuring: the claim would be
that grounded retrieval reduces the `why_it_matters` strip rate, and the negative control is that
a broken retriever must show up as *more* strips, not silently fewer.

## Ground News and Digg

**Ground News** — methodology is published only at the outlet level (bias from AllSides /
Ad Fontes / MBFC averaged; a documented Blindspot formula). The story-level bar is *"calculated
based on the number of bias-rated sources reporting on the story"*, with **no syndication
handling disclosed**. They concede "imperfect clustering" as a known limitation. See §"bias bar"
above — this is the useful finding of the whole competitor sweep.

**Digg** — **there is no public technical information on the 2026 relaunch.** Worth stating
plainly, because search results actively mislead here: the detailed "Clustering Ensemble …
multiple learning algorithms that determine if two separate news articles are part of the same
story" description that surfaces is from the **pre-2012 `blog.digg.com`**, whose domain no longer
resolves. It is a historical artifact, not current architecture. Secondary coverage of the
relaunch describes real-time ingestion from X with sentiment analysis and clustering, but that is
journalism about the product, not an engineering disclosure, and I could not corroborate it from
a primary source. **Do not treat any of it as evidence about how new Digg clusters.**

## Sources

- [Real-time News Story Identification (arXiv:2508.08272)](https://arxiv.org/abs/2508.08272)
- [Ground News — Media Bias Bar](https://ground.news/bias-bar)
- [Ground News — Rating System](https://ground.news/rating-system)
- [Ground News — Our Approach to Media Bias](https://ground.news/media-bias)
- [CJR — The business of balance: Ground News](https://www.cjr.org/analysis/the-business-of-balance-ground-news.php)

---

# Dependency bump (done, CI green, uncommitted)

| package | from | to | reaches prod? |
|---|---|---|---|
| `claude-agent-sdk` | 0.2.143 | 0.2.148 | **yes** — `constraints-prod.txt`, tracked |
| `resend` | 2.39.0 | 2.42.0 | no-op — see below |
| `ruff` | 0.16.4 | 0.16.5 | no-op — see below |

**Only the SDK bump is a real change.** `newsroom/uv.lock` is **gitignored** (`.gitignore:28`) and
both Dockerfiles install from `pyproject.toml`, which lists `resend` and `ruff` unpinned — so prod
and CI already float to PyPI-latest for those at build time. Bumping them in the lock moved the
local host venv and nothing else. That is the whole reason `constraints-prod.txt` exists, and it
is the only file in this change that a deploy will observe.

0.2.144–0.2.148 are **CLI-bundle bumps only** (Claude Code 2.1.246 → 2.1.251); no SDK-level
changes, no breaking changes. Verified in the built image: SDK 0.2.148, bundled CLI 2.1.251.

`bin/sdk-canary` re-run live on 0.2.148 per `test_sdk_pin.py`'s rule. Result is **identical to
0.2.143**: thinking-disabled still accepted on next-gen, Haiku 4.5 still accepts `effort` — both
400-rationales remain obsolete, neither workaround regressed. `_WORKAROUNDS_VALIDATED_THROUGH`
bumped to match. `make ci` and `make ci-full` (with `cargo audit`) both pass.

Rust direct deps and the scientific stack (numpy/scipy/scikit-learn/trafilatura/lxml) are all
current — no cp314-wheel re-verification needed.

## Unrelated bug found: `bin/db-clone` destroys the local DB on any failure

```bash
"$SCRIPT_DIR/ssh" "docker run ... cat /data/digest.db" > "$OUTPUT_PATH"
```

The redirect truncates `data/digest.db` **before** `bin/ssh` runs. The `[ ! -s ]` check below it
fires after the data is already gone. This session lost a 156 MB local clone to a stopped
Tailscale daemon — recoverable only because prod is the source of truth.

Fix: write to `"$OUTPUT_PATH.tmp"`, verify size *and* `pragma page_count * page_size == filesize`
(the truncation mode `MEMORY.md` already warns about), then `mv`. Not applied — flagged only.

---

# Phase 2 shipped: why_it_matters is repairable, and blanking is now visible

*Added 2026-08-30 after the run-280 investigation. `make ci` green, uncommitted.*

## The base rate is the real finding, not run 280

Adding the read-out first changed the story. Blanking is not an anomaly — it is **chronic**:

```
runs 219-280   68 blanked / 937 shipped stories = 7.3% of every digest
               38 of 62 runs (61%) shipped at least one blanked story
```

**Roughly one story in fourteen has been shipping with no "why this matters" section, on three
runs out of five, invisibly.** Run 280 (6 of 16, 37.5%) is a spike on a persistent baseline, not a
new failure. Nothing in `run-reliability`, the story count, or `coherence-repair-yield` shows it,
because nothing is *dropped* — the story ships, one field short.

## The change

`_REPAIRABLE_FIELDS` was `{"headline", "summary"}` (`merge.py:28`), so every `why_it_matters`
failure went straight to the blanking path with **no repair attempted**. It is now
`{"headline", "summary", "why_it_matters"}`.

Blanking is unchanged as the **fail-closed fallback** — it still catches anything repair cannot fix
with a clean, guard-passing, re-checked patch. Only the order changed: try, then blank.

Measured on the committed run-245 fixture, repair coverage on a real archived day **doubles,
3 → 6**: the three whole-story drops as before, plus the three stories that previously shipped with
the field silently emptied.

Two visibility additions, because a fix you cannot observe is not finished:

- **Aggregate log line** — one warning per run naming the count and the share of the digest, in the
  idiom the file already uses for `reporting_varies`. It fires only when blanking happens; a line
  that always appears carries no signal, and there is a test for each direction.
- **`analytics/queries/blanked-why-it-matters.sql`** — reads the archived `selections.json`, i.e.
  what the reader actually received, rather than inferring from the coherence report. Logs age out
  within days; this does not.

TDD throughout: the two repair tests were written failing first, and the run-245 fixture test was
rewritten to assert the new policy rather than bent to fit it.

## What this does NOT fix

The run-280 investigation found that **4 of 6 flagged claims were absent from the entire
483-article pool** — prior-knowledge fabrication by WRITE, not a citation slip. Repair regenerates
`why_it_matters` *from the story's own cited sources*, so it will produce a grounded field or fail
the guard. That is the right outcome, but it treats the symptom: WRITE is still reaching for
geopolitical connective tissue it was never given. That is a separate ticket.

---

# Run 280 replay: the incident is REAL, and the alarm metric should be retired

*2026-08-30. N=9 replays of run 280 and N=8 of run 278 through the production harness.
$18.30 measured from the SDK's own `total_cost_usd`. Code in `scratch/coherence-band/`.*

## Negative control passed first

One replay of run 280 produced **5 failures of 16**, with **4 of the 5 the same story, same field,
and substantively the same reason** as the archived report. Not zero, not an error — the instrument
works. Fidelity was measured, not assumed: every artifact extracted character-exact from
`run_artifacts`, bind-mounted at the production path so the agent prompt runs **verbatim with no
redirect**, `md5` of `coherence.md` identical on host and in the image, and invocation arguments
read from `orchestrate` itself rather than reconstructed.

## The bands

| | counts per replay | min–max | mean | rate |
|---|---|---|---|---|
| **run 280** (16 stories) | 2,4,5,5,5,5,5,8,8 | 2–8 | 5.22 | **32.6%** |
| **run 278** (17 stories) | 1,1,1,2,2,3,3,4 | 1–4 | 2.12 | **12.5%** |

Prod's archived values (280 = 7, 278 = 1) both sit *inside* their own replay band. Re-verified
independently on **rates** rather than counts, since the denominators differ: exact permutation
**p = 6/24310 = 0.00025** one-sided. The bands are decisively separated.

## Two conclusions, and they pull in opposite directions

**1. Run 280 is real. Keep it open.** Five stories fail in ≥60% of replays, and the checker names
the *same specific claim every time* — Gulf commerce restrictions **9/9**, Hormuz closure 8/9,
Kim–Russia cooperation 7/9, Berlin's "largest peacetime build-up" 7/9, "approval in the low 30s"
6/9. Eight of sixteen stories never failed once. Run 278 has exactly **one** stable failure (8/8,
the same "five journalists vs four"). This is a WRITE-side grounding failure on 2026-08-30's
content, not a checker mood.

**2. The per-run fail rate should be retired as an alarm.** Checker variance alone spans ±2
stories. Run 278 — a *healthy* run — replays anywhere from **5.9% to 23.5%**, which covers most of
the archived distribution (mean 11.5%, sd 8.8pp). A single run reporting 20% tells you close to
nothing.

**This partly walks back an earlier claim in this doc.** I flagged "three of the last five runs at
≥25%" as the tightest cluster in the series. Given a healthy run can draw 23.5%, that observation
is much weaker than presented — 25% is barely outside one healthy run's observed band, on n=8.
The **monotonic three-window rise** (8.3% → 13.8% → 21.7%) remains the load-bearing evidence, as
that section's own caveat already said; the run-to-run clustering does not.

**Alert on stable failures instead** — majority vote over 2–3 replays — not on a single draw.

## This validates the Phase 2 change

**Four of run 280's five stable failures are `why_it_matters`-only** — precisely the field repair
did not cover until today. Run 280 shipped 16/16 stories with 6 blanked; run 278 shipped 17/17
with 0.

## Open, and honestly unresolved

Run 280's observed sd (1.75) exceeds the independent-Bernoulli prediction (1.15), hinting at a
per-replay "strictness mood" correlated across stories within a call. **n=9 is too small to call**;
resolving it needs ~25 replays. Flagged as INFERRED, not measured.

---

# COHERENCE model sweep: Opus 5 leads, but not yet by enough to switch

*2026-08-30. 46 runs, $46.53 measured. Reused `bin/eval-coherence` unchanged (it already had a
`--model` override); sweep harness in `scratch/coherence-models/`.*

## Deflation control passed in both directions

Haiku 4.5 was worst on recall **and** the only model flagging hand-audited clean fields, with
fabricated justifications — it claimed "no cited source mentions an ICBM test" when the cited
article's *title* is *"Australia's Pacific rivalry with China heats up after Beijing's ICBM test"*.
That reproduces the 2026-07-01 finding exactly, so the instrument is sound.

## The methodological move that matters: a held-out set

Run 245 is the set the adversarial prompt was **designed from**, so its recall is training-set
performance. A second set was built by planting 8 verified fabrications into archived run 278.

**Set 1 — run 245 (training set), 6 positives / 35 clean fields**

| model | n | recall /6 | false pos /35 |
|---|---|---|---|
| haiku-4-5 *(control)* | 6 | 1.67 | **2.50** |
| sonnet-4-6 | 7 | 1.71 | 0 |
| **sonnet-5** | 7 | **3.43** | 0 |
| opus-5 | 7 | 3.14 | 0 |

**Set 2 — held out, 12 known-bad / 19 clean**

| model | n | recall /12 | false pos /19 |
|---|---|---|---|
| haiku-4-5 *(control)* | 4 | 3.5 | 0.8 |
| sonnet-4-6 | 5 | 8.0 | 0 |
| sonnet-5 | 5 | 7.4 | 0 |
| **opus-5** | 5 | **9.4** | 0.2 |

**The ranking inverts between the two sets, and that is the finding.** Sonnet 5 leads on the set
the prompt was tuned against and trails on fresh content. The clearest single piece of evidence:
Sonnet 5 catches run-245's entity-swap (Iran vs Israel) **5/5**, but misses a *fresh* one of the
same class (Nepal-Bhutan contradicting its own summary's Nepal-Tibet) in **4 of 5 runs**. Haiku,
Sonnet 4.6 and Opus all catch it 5/5. That is direct evidence the Set-1 ranking is partly overfit.

## Four real errors production shipped — two verified independently

Auditing Opus's "over-flagging" reversed the first-pass scoring: four flagged fields carry genuine
unsupported specifics that **run 278 shipped to subscribers**. **Opus found all four; Sonnet 5
found one; Sonnet 4.6 found none.**

I verified two of them myself against run 278's complete archived article text (564,880 chars):

```
"Iran-related"       0 word-boundary hits in the entire run-278 corpus
"already disrupting" 0 hits   —  while "could undermine" has 3
```

The second is a hedge hardened into an assertion, which is a subtler class than a fabricated
entity. I also reproduced the substring trap that made the third finding non-obvious: `secret`
matches 19 times with a loose prefix but only **5** with a true word boundary — the rest are
"Secretary".

## Cost: Opus is only ~16% more, and ~29% faster

| model | $/run | cache-read tok/run | sec/run |
|---|---|---|---|
| haiku-4-5 | 0.22 | 0.25M | 241 |
| sonnet-4-6 | 0.81 | 0.58M | 256 |
| sonnet-5 | 1.33 | **3.19M** | 161 |
| opus-5 | 1.55 | 0.67M | **115** |

Opus costs 2.5x per token yet only ~16% more per run, because **Sonnet 5 burns ~5x the cache-read
tokens**. That is independently corroborated by production: COHERENCE on Sonnet 5 reads 1.93M
cached tokens per run (§4). Sonnet 4.6 is not a cheap fallback — it is the slowest of all four and
costs more per token than Sonnet 5.

## Verdict: promising, NOT yet decided

The case rests on one held-out set at **n=5, 9.4±1.3 vs 7.4±1.1, t≈2.6** — marginal. Switching the
model of the stage that exists to catch fabrication is not a change to make on marginal evidence,
and this project has been burned before by a single-run measurement (`feedback_eval_ill_posed_metric`).
**A second independent held-out set is running, scored blind, pooled with the first.**

Two things already settled regardless of the outcome:

- **Commit `5856f35` is partly vindicated and partly not.** Sonnet 5 genuinely beat 4.6 on the
  incident set, but that advantage **does not generalise** to fresh content.
- **Two run-245 errors are missed by every model in all 27 runs** (the scope conflation and the
  quantifier). Those are **prompt-level blind spots a model swap cannot fix** — do not mistake a
  model change for a fix to them.

---

# Prompt audit: the model sweep was confounded, and two blind spots have a mechanism

*2026-08-30. Audit run per the `claude-api` skill's `prompt-audit` method over `.claude/agents/*.md`,
`orchestrate.py`, `cluster_extractjoin.py`, `threads.py`, `thread_synthesis.py`. Report and proposed
diff in `scratch/prompt-audit/`. Findings below are the ones I verified myself.*

## 1. `thinking: disabled` — the premise is void, not merely stale

`orchestrate.py:66` sets `_THINKING = {"type": "disabled"}` for every stage, justified by a
CLUSTER incident (thinking over ~460 articles tripped the 32k output ceiling).

**That stage no longer sends a prompt to a model at all.** `orchestrate.py:747` short-circuits
`label == "cluster"` to `cluster_extractjoin.run_extractjoin_stage` before `parse_agent_spec` is
ever reached — verified in the code. `cluster.md`'s body has not been sent to a model since
2026-07-01. The setting is a no-op on the 4.x stages and behaviour-changing on exactly two:
**COHERENCE (`claude-sonnet-5`) and the repair re-check that reuses its prompt.**

It also contradicts the project's own written policy: `cluster_extractjoin.py:50-58` and
`docs/2026-07-01-sdk-pinning-canary-finding.md:44-47` both say never send `disabled` to a next-gen
model, while production has done exactly that daily for ~93 runs.

## 2. The model sweep was measured with the Opus arm handicapped — VERIFIED

Every record in `scratch/coherence-models/out/*.jsonl` ran `thinking: "disabled"`:

```
claude-haiku-4-5   x10        claude-sonnet-4-6  x12
claude-sonnet-5    x18        claude-opus-5      x20
```

Anthropic's Opus 5 guidance names `thinking: {type: "disabled"}` as a documented failure mode, with
two symptoms: **a tool call written into visible text where it silently never runs**, and
`<thinking>` tag leakage. COHERENCE's entire output is a single `Write` call, so that failure mode
lands precisely on the mechanism being measured. The recommended configuration is thinking on at
lower `effort`.

**So the 9.4±1.3 vs 7.4±1.1 result is not a clean model comparison.** The confirmation run has been
redirected to four arms — opus-5 and sonnet-5 × {disabled, adaptive} — with the disabled arms kept
as controls, and instructions to report any missing `Write` call or leaked thinking tags. If
thinking configuration dominates model choice, **we have been tuning the wrong variable.**

## 3. Two blind spots have a mechanism, and it is one word

The sweep found two run-245 errors missed by every model. The audit found why, and I verified the
rates directly from the per-run records:

```
idx 3  headline  "50% tariffs on MOST Canadian goods"   hard_caught 0/27   hard_missed 27/27
idx 0  summary   "17 killed since its resumption"       hard_caught 0/27   hard_missed 27/27
```

(Both across haiku 6 + sonnet-4-6 7 + sonnet-5 7 + opus-5 7. For contrast, idx 8 — the Iran/Israel
entity swap the prompt was written against — is caught **7/7 by all three non-control models**.)

**Cause: `coherence.md` caps its own coverage.** Line 9 says *"the least-supported claim in each
field"*, line 21 says *"the single least-supported specific"*, and only line 48 says *"every
specific"*. Two instructions to check one thing against one to check everything — and current
models are literal. The decisive evidence is that probe 1 **already contains a near-verbatim
quantifier example** written to catch idx 3, and it is still missed 27/27. Naming the error class
did not work, which also means the deferred "add a quantifier probe" item from `5856f35` would not
have worked either.

The second gap is structural: **no probe covers a specific that is present in the sources but bound
to the wrong scope, time window, or event.** Probe 2 states the right principle ("not merely that X
and Y each appear somewhere") and then scopes it away with a closed list of three relation types;
probe 3 scopes entity-binding to the headline only. Run 245's "17 killed since its resumption" (17
is the cumulative toll) falls exactly in that gap.

## What the audit correctly declined

I had suggested `coherence.md`'s emphasis density might drive the strictness variance. **The audit
declined to promote it**, and was right to: the adversarial register is a *measured win*
(`5856f35`, 0/6 → 3.43/6 recall with zero false drops), my own mood reading is labelled INFERRED at
n=9, and a better-evidenced competitor for the same variance already exists (the r=0.662 narration
correlation). It filed only the bounded part — "uncertainty is a FAIL" appears three times — at low
confidence.

It also killed two of its own candidate findings by measuring: `initialPrompt`/`description` are
live (Claude Code's agent loader reads them), and `select.md`'s tier caps are enforced in practice
(11 prod runs, zero breaches). Group 3 is N/A — the pipeline defines no custom tools.

## Caveat on the deliverable itself

The agent reported "all nine hunks pass `git apply --check`". **That is wrong on both counts**: the
diff carries **27 hunks across 11 files**, and it does **not** apply — `.claude/agents/coherence.md`
is patched twice, each hunk written against the pristine file, so the second collides with the
first. The five `newsroom/src` patches apply cleanly under `--3way`. The analysis is sound and
independently verified; the packaging is not. Apply per-file, sequenced, not as one patch.

---

# DECISION: turn thinking on, do not switch model

*2026-08-30. 94 runs across three held-out sets, $112.02. Reverses the model-swap rationale of the
previous round. SHIPPED to the working tree, `make ci` green, NOT deployed.*

## The result

Under `thinking: disabled`, Opus 5 genuinely beats Sonnet 5. Under `thinking: adaptive`, the gap is
**exactly zero** — and Sonnet 5 gets there for 58% of Opus's cost.

| arm | n | recall | FP/run | $/run |
|---|---|---|---|---|
| opus-5 / **adaptive** | 12 | **0.919 ±0.067** | 0.00 | 1.705 |
| sonnet-5 / **adaptive** | 12 | **0.919 ±0.067** | 0.00 | **0.994** |
| opus-5 / disabled | 17 | 0.871 ±0.135 | 0.06 | 1.446 |
| sonnet-5 / disabled *(production)* | 17 | 0.761 ±0.145 | 0.00 | 1.357 |

| contrast | diff | p |
|---|---|---|
| sonnet-5 adaptive vs disabled | **+0.158** | **0.002** |
| opus disabled vs sonnet-5 disabled | +0.109 | 0.028 |
| **opus adaptive vs sonnet-5 adaptive** | **+0.000** | **1.000** |

**Thinking configuration is the larger effect than model choice** (+0.158 vs +0.109), and it makes
the model swap worth nothing. We had been tuning the wrong variable.

## The mechanism, verified independently from the raw records

```
                        thinking_tokens   cache_read/run   output/run    $/run
sonnet-5 / disabled                   0        3,308,468       16,023    1.349
sonnet-5 / adaptive              26,807          572,587       31,948    0.994
opus-5   / disabled                   0          567,414        9,243    1.459
opus-5   / adaptive              13,099          497,132       18,536    1.705
```

**With nowhere to think, Sonnet 5 substitutes RE-READING for reasoning** — 5.8x the cache reads.
Give it a thinking channel and it reads 5.8x less, costs **26% less**, and catches more. Opus never
had the pathology (567k → 497k), which is why its cost barely moves.

The zero/non-zero `thinking_tokens` column is the proof the manipulation took effect at all; I
verified every number in this table myself from `scratch/coherence-models/out/*.jsonl`.

This also explains §3's `pearson(visible output tokens, fail%) = 0.662`: with thinking off, the
checker's probes ran as visible narration, so "more narration" literally meant "more reasoning
happened". Adaptive moves that reasoning where it belongs.

## The symptom check came back negative — and that is worth stating

Zero incidents across **94 runs** of all four documented disabled-thinking symptoms: no `<thinking>`
leakage, no malformed reports, no run failures, and critically **the "tool call narrated but never
issued" failure occurred 0/94** — every run produced a parsed `coherence_report.json`. So the
handicap was real but it was a *quality* effect, not the silent-tool-call mode. Caveat as stated:
`final_text` was captured for only 32/94 records, so narration is directly checkable on those 32;
the load-bearing symptom is proven on all 94 by report presence.

## What shipped

One line of frontmatter — the per-stage plumbing already existed (`AgentSpec.thinking`,
`spec.thinking or _THINKING`):

```yaml
thinking: adaptive
```

Verified in the rebuilt production image: `thinking: {'type': 'adaptive'}`, the repair re-check
inherits it via `dataclasses.replace`, and the explanatory comments do not leak into the prompt
body. Two TDD tests written failing first. `make ci` green.

**The global `_THINKING = {"type": "disabled"}` in `orchestrate.py` is deliberately left alone** —
it is a no-op on every other stage, and changing it would alter four stages on evidence gathered
for one. Overriding at the one stage that was measured is the smaller, reversible change.

## Honest limits

- Set 3's plants hit the ceiling — every Opus and Sonnet 5 run scored 8/8, so it discriminates only
  via one discovered error. The headroom is in set 2b; both sets agree.
- Recall figures come from the agent's scoring against a frozen key. I verified the cost, cache,
  thinking-token and false-positive columns directly, but not recall itself.
- The harness had a real bug, caught and fixed mid-run: `eval_coherence._norm` kept combining marks,
  so Opus echoing "Mladic" for "Mladić" silently failed to map — it had been quietly penalising
  Opus. NFKD-folding raised its set-2 recall 9.4 → 9.6.
- **The two 0/27 prompt-level blind spots are untouched by any of this.** No model, no thinking
  setting moved them. The deferred quantifier/scope-fidelity probe remains the only lever.

---

# Request config is now recorded (migration `20260830210000`)

*2026-08-30. Shipped to the working tree, `make ci` green, migration applied to the local clone.
NOT deployed.*

The gap that cost the most today: **`run_usage` recorded which model ran but never how it was
configured.** A 60-run model sweep silently ran every arm thinking-disabled, and the confound was
invisible in the pipeline's own telemetry — it took a separate prompt audit to find it. Thinking
then turned out to be a *larger* lever than model choice, so the single variable that mattered
most was the one not recorded.

Two additive columns, `thinking` and `effort`, carrying the **resolved** value actually sent to
the SDK — `spec.thinking or _THINKING` in `orchestrate`, and the module constant in
`thread_synthesis` (which sets its own config locally rather than via `AgentSpec`; recording NULL
there would have reproduced the very blind spot the columns close). A caller that supplies nothing
stores NULL, which reads as "not recorded" — never a fabricated default, because a synthesised
`"disabled"` would be indistinguishable from a measured one.

`_thinking_label` flattens the SDK's `ThinkingConfig` to the one queryable token (`adaptive`,
`disabled`), so a `GROUP BY thinking` reads the way `GROUP BY model` already does. An unparseable
value records NULL rather than its repr, so a typo cannot masquerade as a distinct setting.

Applied to the local clone: all 1,709 historical rows backfill as NULL, as intended. Three TDD
tests, written failing first.

**This is the ~95% of the LLM-tracing question that needs no vendor.** What a hosted backend would
add beyond it is being evaluated separately in
`docs/2026-08-30-llm-tracing-backend-options.md`.

## A repeat gotcha worth naming

Both `.claude/agents/` and `migrations/` are `COPY`'d into the image at build time. A
`docker compose run` against a cached image therefore reports the *old* agent frontmatter and
reports "No pending migrations" for a migration that exists on disk. This produced two false
negatives today. **Rebuild before verifying anything that lives in those directories.**

## CORRECTION: OTLP tracing would NOT have caught today's confound

I claimed a tracing layer "captures request config by default; we'd have caught it on run one".
**That is wrong**, and verified wrong against the primary source
(https://code.claude.com/docs/en/monitoring-usage, checked 2026-08-30):

- **Tracing is off by default** — spans are a beta needing `CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1`
  on top of `CLAUDE_CODE_ENABLE_TELEMETRY=1`. Only metrics and logs are on with the first flag.
- **There is no `thinking` attribute.** The full `claude_code.llm_request` attribute list does not
  contain it. **The single variable that invalidated a 60-run sweep is not emitted at all.**
- `effort` **is** emitted (`low|medium|high|xhigh|max`) — so half the config is available.
- Token counts are **custom names** (`input_tokens`, not `gen_ai.usage.input_tokens`), so no vendor
  maps them to cost automatically.
- **No documented flush-on-exit**, against a 5 s span / 60 s metric interval — a real tail-loss risk
  for a one-shot container.

So the migration is not "95% of what a tracing backend would give us" — for `thinking` it is
**100%, because tracing cannot supply it.** The two are complementary, not substitutes:

| signal | `run_usage` | Claude Code OTLP |
|---|---|---|
| `thinking` | **yes** | **no — not emitted** |
| `effort` | yes | yes |
| tokens, cost, duration | yes | yes (custom names) |
| retry `attempt`, `ttft_ms`, `stop_reason`, `request_id` | no | **yes** |
| `agent_id` / `parent_agent_id` / `query_source` | no | **yes** |

That last row is the real argument for tracing here, and it is not the one I made: per-subagent
parentage is exactly what a COHERENCE fan-out would need to attribute a failure to one of sixteen
concurrent calls. Retry attempts and TTFT are likewise invisible today.

---

# COHERENCE was special: the finding does NOT generalise

*2026-08-30. Per-stage 2x2 sweeps (model x thinking) on SELECT, REPAIR and CLUSTER extraction.
Manipulation verified on every arm via `thinking_tokens`.*

The obvious next move after COHERENCE was "put every stage on Sonnet 5 adaptive". **Measured, and
the answer is no.**

**SELECT: do not change it.** Adaptive costs ~2.2x and runs ~3x slower, and agreement with the
production selection sits *inside the control arm's own self-agreement band* — no measurable gain.
The reason is mechanical and settles the question: **SELECT reads ~90k cached tokens per run, not
COHERENCE's 3.3M.** The re-reading-instead-of-reasoning pathology that made adaptive *cheaper* on
COHERENCE is simply not present, so there is no cost saving to offset the slowdown.

That is the negative control this whole line of work needed. COHERENCE's 5.8x cache-read collapse
was the mechanism, not a general property of thinking, and a stage without that signature has
nothing to gain.

**CLUSTER extraction: the original abort condition looks unreachable.** Adaptive peaked at 6,861
output tokens on a 40-article batch — **21% of the 32k ceiling** that aborted a run in 2026-07 and
justified the global `disabled` in the first place. Batching removed the failure mode; the constant
outlived it twice over.

## A latent production defect, found by the sweep — and my Phase 2 change makes it worse

`repair._index_by_article_ids` does `index[frozenset(ids)] = result`: **last write wins.** Its
docstring reasons carefully about *cross-story* collisions (two stories with empty ids) but never
about *same-story duplicates*. Under adaptive thinking the repairer answers **one object per
field**, so the second silently overwrites the first. Reproduced model-free:

```
results returned by model : 2      ({"summary": ...}, then {"why_it_matters": ...})
entries after indexing    : 1
patched_fields surviving  : {'why_it_matters': 'fixed why'}
summary patch SURVIVED    : False
```

The guard then sees a patch that does not cover the flagged fields and **drops a story that was
fully repaired**. Control arm hits it 1/6; adaptive 6/6.

**I measured the interaction with today's Phase 2 change, and it is real.** Across all 75 archived
coherence reports (114 failed results):

```
multi-field AND repairable under OLD {headline, summary}        6   (5.3%)  in  6 runs
multi-field AND repairable under NEW {+ why_it_matters}         11  (9.6%)  in 10 runs
                                                     exposure   1.8x
```

Adding `why_it_matters` to `_REPAIRABLE_FIELDS` is still right — 68 of 114 failures are
`why_it_matters`-only, which is the entire point — but it **nearly doubles the traffic through a
buggy code path**.

> **These must ship together. The Phase 2 change must not deploy without the
> `_index_by_article_ids` fix.** A merge-per-field index rather than an overwrite is the natural
> shape, pending a check that no downstream guard assumes one object per story.

## And a broken instrument

**16/16 recent production recaps and 15/31 production selections fail their own L1 graders.** A
grader that rejects almost all healthy output is not a quality signal, it is a broken instrument —
and `bin/eval-stages --run N` therefore cannot currently serve as a regression gate. Whether the
graders drifted from the prompts or vice versa is still being characterised. Nothing was changed.

### Correction to the exposure figure above, and the fix's shape

My "1.8x exposure" counted *failures* and compared field sets over a window that already had the
new set applied. Re-measured properly over runs 250-280, counting **repair requests**:

```
field set                        requests   multi-field    rate    runs exposed
OLD {headline, summary}                25            6     24.0%          6/31
NEW {+ why_it_matters}                 76           10     13.2%          9/31
```

**The RATE fell** (24.0% -> 13.2%) because the denominator tripled. But rate is the wrong
denominator for a per-request defect: **absolute multi-field requests rose 6 -> 10 (+67%) and runs
exposed rose 6 -> 9 (+50%).** The conclusion stands — Phase 2 increases exposure and the two must
ship together — but for the absolute count, not the rate. 47 of 76 failures (61.8%) are
`why_it_matters`-only: single-field, unexposed, pure win.

**The fix needed an amendment I did not anticipate.** `_index_by_article_ids` has **two** callers:
field patches (`repair.py:130`) and re-check **verdicts** (`repair.py:181`). Merging is correct for
patches but **wrong for verdicts** — merging a `pass:false` with a `pass:true` could flip a drop
into a keep. The proposed diff adds a separate `_merge_repaired_by_article_ids` for the patch path
and leaves the verdict path last-wins, documented, so it stays fail-closed by design rather than by
luck (0 duplicate verdict groups in 31 archived reports — unexercised, not safe-by-accident).
Merging is fail-closed on conflict: two objects giving different text for the same field guard-fail
rather than arbitrarily picking one. 2 of 5 new cases fail on today's `repair.py`, all 5 pass
patched, and all 137 existing repair/merge tests still pass.
Diff: `scratch/stage-sweep/proposed/repair_index_merge.diff`.

### The two failing graders have OPPOSITE causes

"Broken instrument" fits only one of them:

- **RECAP — the grader is wrong.** 29/31 runs fail the 600-char cap; **0/31 fail the sentence cap**,
  and observed sentence counts are 2-3, exactly what `recap.md` asks for. The output obeys the
  prompt. `RECAP_MAX_CHARS = 600` was never calibrated against real output (real range 563-1126).
- **SELECT — the grader is stale, but it is measuring something real.** 57 of 63 failing selections
  are *fully disjoint* from the cluster they name — `cluster_index` pointing at an unrelated
  cluster, not a few stray ids. The pipeline already knows: `threads.py:202` keys on `article_ids`
  "**NOT** its `cluster_index`", citing run 247 where 7/12 entries indexed a cluster containing none
  of their own articles. So the grader asserts an invariant the pipeline **abandoned on purpose** —
  a true observation about a field nothing depends on any more.

Neither is a quality signal today and both exit non-zero on healthy runs, so `bin/eval-stages`
cannot gate. Nothing changed.

### Both landed — blocker cleared

The `_index_by_article_ids` fix is applied alongside Phase 2, so the coupling above is resolved
rather than merely documented. `_merge_repaired_by_article_ids` merges per-field patches for one
story; conflicting text for the same field guard-fails to the drop path. The **verdict** path keeps
last-wins deliberately — merging `pass:false` with `pass:true` would fail *open*. Five new tests,
two of which were red before the change. `make ci` green. Still uncommitted and undeployed.

---

# The join's own input was being thrown away

*2026-08-30, local work, no model calls. `make ci` green, uncommitted.*

I set out to run the one experiment on this pipeline that costs nothing — `join_tags` is pure
Python, so sweeping the `_tag_bag` weights (`entities * 3 + keywords + primary_event * 2`) and the
threshold against archived runs needs **zero model calls**, and the junk-drawer measurement already
named that weighting as the suspected cause of 23% of shipped clusters bundling unrelated stories.

**It was impossible, and the reason is a missing artifact.** `run_artifacts` keeps `clusters.json`
— the join's *output* — and nothing keeps the extracted tags that are its *input*. `claude_input/`
is `rmtree`'d on the next run, so the tags are gone within a day. I checked every archived artifact
name and every file the interrupted extract sweep left behind: no tags anywhere.

Two costs, and the second is worse than the first:

1. **The cheapest available experiment needs a full re-extraction** — i.e. the entire cost of the
   stage — to run at all.
2. **A bad cluster cannot be attributed.** Given only the output, there is no way to tell whether
   the extraction was wrong or the join was. Every clustering incident in this repo's history was
   diagnosed without that distinction available.

Fixed: `cluster_extractjoin._write_cluster_tags` archives the tags **after** the title-only
fallback fill, so what is stored is exactly what `join_tags` sees, and records
`_TAG_BAG_WEIGHTS` alongside them — a tag set is only replayable if you know the weights it was
joined under, and those are a code constant that moves independently of the data. Added to
`db._TRACE_ARTIFACTS`, because a file the archiver does not pick up is a file that never existed.
Same fail-soft contract as `cluster_health.json`: observability must never cost a digest.

Four tests, two red before the change.

**The weight sweep becomes free from the next run onward.** It cannot be run retroactively — no
archived run has tags — which is precisely the point.

---

# WRITE sweep: thinking wins again, but the model change has a reader-facing cost

*2026-08-30/31. 96 model calls (48 WRITE + 48 COHERENCE gradings), 4 arms x 2 source runs x 6 reps,
$91.72. Source runs 280 (the measured-defect day) and 276. Manipulation verified; `newsroom/src/`
and `.claude/agents/` SHA-256-checked before and after.*

## Within-arm spread first, as the method requires

```
s4.6/disabled (PROD)  mean 0.362  range 0.067-0.563   spread 0.496
s4.6/adaptive         mean 0.162  range 0.067-0.333   spread 0.267
s5/adaptive           mean 0.168  range 0.000-0.267   spread 0.267
s5/disabled           mean 0.248  range 0.067-0.500   spread 0.433
```

**Every between-arm gap is smaller than the widest within-arm spread.** A single-rep comparison on
this endpoint is worthless; the effects below need n=12 and run-blocking to see at all.

## Thinking dominates, again — and the effect is specific

| metric | THINKING (adaptive − disabled) | MODEL (s5 − s4.6) |
|---|---|---|
| COHERENCE fail rate | **−0.140, p=0.0002** | −0.054, p=0.17 |
| `why_it_matters`-only fails | **−1.83, p=0.0002** | −1.00, p=0.063 |
| absence-worded fails | **−1.63, p<0.0001** | −0.46, p=0.25 |
| headline+summary fails | −0.33, p=0.40 | +0.17, p=0.72 |

Two things make this credible rather than a judge artifact. **Specificity:** the effect sits
entirely in `why_it_matters` and the absence/fabrication class — the exact run-280 defect — while
headline+summary is flat (p=0.40); a judge merely being kinder to one arm would move both. And a
**matched-length control:** s4.6/disabled and s4.6/adaptive write near-identical `why_it_matters`
(345.3 vs 344.3 chars) yet fail 4.08 vs 1.42 times per draft, so "shorter text, fewer claims" does
not explain it.

The re-reading signature reproduces (cache_read ÷ output: s5/disabled 74.3 → s5/adaptive 12.0) —
but here thinking costs **more**, not less, because WRITE's context is small enough that the
re-reading it replaces was cheap. The COHERENCE saving was a property of that stage's 3.3M-token
context, not of thinking.

## Two of my own claims, refuted

I told Sean that Sonnet 5 is cheaper than 4.6 and that 4.6 was the slowest model, and used both to
argue a switch was likely free. **Both are false on WRITE.** Sonnet 5 costs **+15%** at matched
thinking ($0.721 vs $0.628) despite lower per-token pricing, because it re-reads twice as much. And
`s4.6/adaptive` is by far the slowest arm — median **1197 s**, minimum 612 s, max 2694 s.

## The trade the sweep reported but did not connect

`s5/adaptive` cites **4.26 sources per story against production's 6.51** — a 35% drop. I checked
where it lands, because the single-source rate alone (19.4% vs 21.7%) looks *better*:

```
share of stories by DISPLAYED source count        <=2 outlets
s4.6/adaptive   16.7  10.8  12.4  14.5   1.6  44.1     27.4%
s4.6/disabled   21.7   7.6   9.8  14.7   1.6  44.6     29.3%   <- production
s5/adaptive     19.4  16.1  15.1  19.4   4.8  25.3     35.5%
s5/disabled     17.4  12.0  11.4  14.7   4.9  39.7     29.3%
```

**`s5/adaptive` pushes 6.2pp more stories into the <=2-outlet band** — precisely where this same
day's bias-bar audit found the worst problem ("2 outlets shown: 17 stories, where the entire bias
bar is one duplicated report"). `s4.6/adaptive` moves the other way (27.4%), *improving* on
production while delivering the same quality gain.

## So the decision is a genuine fork, not a technical answer

**The thinking change is the win and it is model-independent** (0.162 vs 0.168 — a coin flip).
What differs is the cost of getting there:

- **`s4.6/adaptive`** — same quality, **better** sourcing than production, but **1197 s median**
  against a whole-run wall clock of ~19 minutes today. One stage would roughly double the run.
- **`s5/adaptive`** — same quality, 413 s, **+15% cost**, but 6.2pp more stories in the band where
  the independence claim is weakest.

That is a product call — reader-facing source display against run time — not something the
measurement settles. Recorded, not decided.

**Caveat on the primary metric:** it is graded by COHERENCE itself (held at sonnet-5/adaptive), so
it measures "fewer stories the production gate will strip" — the operational outcome — not
independent ground truth. A judge-favours-its-own-family bias is ruled out by `s4.6/adaptive`
scoring as clean as `s5/adaptive`. The deterministic lexical scorer disagrees about *which* factor
matters (model p=0.002, thinking p=0.95); both agree `s5/adaptive` is best-or-tied on their own
terms. Its controls passed: cross-run scoring raises the absent-rate ~9x, and it independently
recovers 5 of run 280's 7 flagged claims by lexical means alone.

## The sourcing question, settled: it IS a loss of independence

Sean's call was "measure sourcing before changing WRITE". Done, deterministically, zero model
calls (`scratch/sourcing/measure.py`). The two readings of the 6.51 → 4.26 drop were: **(a)** it
loses independent corroboration, or **(b)** it strips redundant reposts of the same wire — in which
case fewer cited sources would be *more* honest, not less.

Method: resolve each story's cited ids to (source_id, title), run the **production** wire collapse
(`digest.collapse_reposts`), then apply the bias-bar audit's own near-duplicate test (Jaccard >= 0.6
on normalised titles) to the survivors. What matters is the count of INDEPENDENT survivors.

```
arm              stories  raw cites  after wire  INDEPENDENT  <=2 independent
s4.6/adaptive        186       6.63        6.18         5.71            34.4%
s4.6/disabled        184       6.51        6.02         5.52            35.9%   <- production
s5/adaptive          186       4.26        4.08         3.79            42.5%
s5/disabled          184       5.85        5.39         4.94            37.0%
```

**It is reading (a).** `s5/adaptive` drops 2.24 raw citations and **1.73 of them survive both the
wire collapse and the duplicate test** — 77% of the drop is genuinely independent reporting, not
redundancy. It also pushes stories at **<=2 independent sources from 35.9% to 42.5% (+6.6pp)**,
which is exactly the band the bias-bar audit identified as the worst case ("the entire bias bar is
one duplicated report").

**`s4.6/adaptive` moves the other way: +0.19 independent sources against production**, while
delivering the same quality gain (0.162 vs 0.168 — a coin flip).

### Recommendation, revised

**Take `thinking: adaptive` on WRITE. Keep `claude-sonnet-4-6`.** The thinking change is the whole
win and it is model-independent; the model switch buys latency at the cost of reader-facing
independence, on a product whose stated differentiator is exactly that.

The remaining objection to `s4.6/adaptive` is latency — 1197 s median, 2694 s max, against a
~19-minute whole-run wall clock. I checked whether that is merely slow or actually unsafe:
`orchestrate._IDLE_TIMEOUT_S` is **120 s between SDK events**, so a long silent think would abort
the stage. **Empirically it does not**: the 2694 s call returned `ok=True`, and every adaptive
record that failed in either sweep failed on a session rate limit, not a timeout — including 6
failures in the `disabled` control arm, so it is not adaptive-specific. Thinking emits events.

So the cost is a daily batch that runs ~35-40 minutes instead of ~19, on a 10:25 UTC cron with no
downstream deadline. That is a price worth paying for +0.19 independent sources and a 53% cut in
coherence failures. **Not applied — Sean's call, and it is now a decision rather than a fork.**

---

# The embedding gate strips foreign-language corroboration — ship multilingual or not at all

*2026-08-31. 22 runs (260–282), 346 shipped stories, 3,022 citations. Local, no API cost.*

The cluster-level result was junk drawers 13% → 3%. **Measured at the reader, 82% of that win is an
artifact**, and the cause is a blind spot the encoder and the scoring metric happen to share.

## It does fix the case that started this

Run 282's desertification story, gate as configured:

```
KEPT  A211 (the story, cos 0.839)   A606 (WSJ, related, cos 0.839)
CUT   A65  (NATO, 0.114)            A256 (lawn mowers, 0.206)
```

Both junk citations removed; the WSJ piece the lexical metric wrongly scores at 0.057 is **kept**.
Clean separation. (Aside worth noting: production had filed that drought story inside a cluster
labelled *"US military force posture review and Europe troop withdrawal"* — it was a stray in a
NATO junk drawer.)

## But the reader-facing win almost vanishes under inspection

```
baseline junk-citation rate                       41.2%
gated, as the lexical metric scores it            37.3%   (-3.9pp, 22/22 runs, p=2.4e-07)
gated, after hand-reading the decisions           40.5%   (-0.7pp)
```

Hand adjudication of 40 blind decisions: gate precision **68.2%**. The lexical metric alone scores
**53.6%** — it was overstating the gate.

## The mechanism, verified independently

I re-derived this myself by **source language**, which is unambiguous, rather than by character set:

```
                citations   cut   cut rate
english source       2783   156      5.6%
NON-ENGLISH           239    98     41.0%    <- 7.32x

  clarin_mundo   28/50  = 56.0%
  der_spiegel    60/146 = 41.1%
  le_monde       10/43  = 23.3%
```

Reading all 98 non-English cuts: **80 (82%) are the same event reported in another language** —
*"Island: Isländer stimmen gegen EU-Beitrittsverhandlungen"* cut from *"Iceland votes No on EU
accession talks"*. Gate precision on non-English cuts: **10%**.

**The metric cannot see this, because it fails for the same reason the encoder does.** English-token
Jaccard scores 98/98 of those as junk, endorsing 88 removals a human judged wrong. Corrected
ledger: the 254 cuts are **125 junk + 129 relevant**, not 213 + 41. Cut precision 84% → **49%**.

This is the sharpest instance yet of a rule this session keeps re-learning: **a metric that shares a
blind spot with the method it grades is worse than no metric**, because it manufactures
significance instead of withholding it.

## The fix is measured, and it inverts the cluster-level ranking

| arm | non-EN cut rate | junk rate (hand-corrected) | cut precision | significance |
|---|---|---|---|---|
| MiniLM | 41.0% | 37.3% | 52% | **p=0.067 — not significant** |
| **multilingual-MiniLM** | **3.3%** | **36.6%** | **70%** | **p≤1e-5** |

Its *English* cut rate is higher (6.4% vs 5.6%), so this is not "cuts less" — it cuts better. And
under hand-corrected labels **only the multilingual arm is significant at all.**

At cluster level `mmnilm` scored **worse** (F1 0.743 vs 0.772, junk drawers 16 vs 5). So the
cluster metric and the reader disagree about which model to ship, and the GRAPH GATE says the
reader wins.

## Consequences

- **Do not ship the MiniLM gate on the strength of the cluster number.** If it ships, it ships
  multilingual: same cost, strictly better on every reader-facing axis.
- This interacts with two standing goals. `docs/2026-08-26-bias-bar-independence-audit.md` counts
  displayed outlets as an independence claim, and `project_source_diversity` names geographic
  coverage as the real hole. **An English-only encoder silently strips exactly the Spanish, German
  and French corroboration both depend on** — a fairness regression invisible to every metric we had.
- Reachability caps the ceiling regardless: 30% of junk citations sit in clusters the gate never
  splits, and 53% sit in-range but inside the anchor piece, where the embedding endorses them.

## Method notes worth keeping

The mapping assumption (SELECT picks the anchor piece) was defended three ways — 98.2% of citations
fall inside the mapped cluster, two anchor rules disagree on 4/346, and an independent mapping via
`selected.json`'s own `cluster_index` gives 37.5% against 37.3%. The agent also found and fixed a
defect in its own harness that had inflated the gate by ~0.5pp.

---

# CORRECTION: 72% of the junk problem was the metric. Change nothing but the encoder.

*2026-08-31. 120 blind hand adjudications, two disjoint samples. Full lesson:
`docs/lessons/the-metric-was-the-problem.md`.*

**Two numbers I reported earlier in this doc are wrong, and I propagated both.**

**1. "41.2% → 36.6%" is apples to oranges.** It pairs an *uncorrected* baseline against a
*hand-corrected* gated figure. Like for like it is **38.6% → 36.6%**.

**2. "30% of junk sits in clusters the gate never splits (below min_size)" conflated two things.**
That 30% is junk in clusters the gate *declines to split*. Junk in clusters *below* `min_size` is
**3.7%**. So `min_size` — the lever I nominated first — is dead, and the reasoning built on it was
void. Small clusters are also *tighter* (centroid spread 0.00–0.066 vs 0.109 at size 20+), so
firing on them is mostly wrong: cut precision 38–67% at sizes 2–4 against 68–77% at 10+.

**3. And the baseline itself is 3.6x smaller than measured:**

```
                 lexical metric      hand
baseline junk    1057  (35.0%)    297  (9.8%)
after the gate         (32.7%)         (6.7%)
```

The residual is **0.55 junk citations per story, 8.7 per run** in a list of 137.

## A rule that passed held-out validation and was still wrong

A floor rising with cluster size beat the incumbent on lexical labels **and transferred held-out**.
Hand labels on the cuts it *adds*: **23% precision against 58%** for cuts both rules make
(p=0.002) — it destroys **3.4 relevant citations per junk one**. The metric priced those same cuts
at 74%. That 51-point error was the whole improvement. **Generalisation tests protect against
overfitting, not against a systematically wrong label.**

## Run 282's desertification story was never a threshold problem

The multilingual gate splits it **correctly** — {drought, drought} / {NATO, lawn mowers} — and then
the deterministic anchor tie-break keeps the wrong half. Ties hit 3/346 stories (1%). No floor and
no `min_size` fixes that; it is a tie-break rule.

## Recommendation

**Ship the multilingual encoder. Change nothing else.** Average linkage, floor 0.35, `min_size` 4
dominated every alternative at every cost budget once the labels were honest. Treat citation junk
as closed at ~6.7%.

**No LLM cohesion gate.** Not on cost ($0.03–0.05 against $2.50–3.00/run) — on prize: 8.7 junk
citations per run, on a population the deterministic gate has already skimmed, where it must beat
58% precision on exactly the cases the embedding got confidently wrong, and where validating each
change needs the same hand adjudication that just showed no automated metric can score this
population.

## Shipping the multilingual model: it fits, measured

`paraphrase-multilingual-MiniLM-L12-v2` ships its **own** `onnx/` directory — cosine **1.000000**
against torch (max deviation 1.19e-07) over 3,950 articles, reproduced in a container carrying only
`onnxruntime`/`tokenizers`/`numpy`.

Cost is memory, and it is the **tokenizer**: a 250k-vocab Unigram model is +282 MB RSS on its own
(L6's WordPiece: +9 MB), and no quantization touches it. Peak **1155 MB vs L6's 456 MB**.

Production headroom, measured on the box today:

```
total 3819 MB   available 2726 MB   swap 0
newsroom container cap: 2g          pipeline peak observed: ~200 MB
  -> ~200 + 1155 = ~1355 MB against 2048 MB   (~700 MB headroom)
```

It fits. **But there is no swap, so an OOM is a hard kill, not a slowdown** — and fp16 `model_O4`
passes at **831 MB** for a 4x wall-clock hit that is irrelevant here (seconds on a ~20-minute run).
On a swapless box fp16 is the safer default; int8 is out (cosine 0.991, flips 0.098% of gate
decisions).

**Gotcha for whoever wires it:** `max_seq_length` is **128**, not L6's 256. Carry it explicitly or
vectors silently diverge.

---

# Independent re-adjudication (Fable, blind): the conclusion mostly holds, two claims do not

*2026-08-31. 160 blind verdicts frozen before any prior verdict was read. Cohen's kappa verified
independently by me from the two raters' files.*

## The single-rater hole is closed

```
paired verdicts   160
raw agreement     146/160 = 91.2%
Cohen's kappa     0.809          E2E 0.848 | OP1 0.715 | OP2 0.944
disagreements     8 prior-relevant/fable-junk, 6 the other way  -> not one-directional
```

Every headline number replicates within **0.1pp** under an independent rater: baseline 9.9% (prior
9.8%), residual 6.8% (prior 6.7%). The three systematic disagreement patterns are all judgement-call
classes (background sidebars, same-saga analysis), and 12 of 14 carry a borderline flag from at
least one rater.

## But "closed at 6.7%" does not survive

The sample was drawn from citations **the lexical metric flagged**. The other **1,710 of 3,022**
citations — English keeps the metric never flagged — were structurally assumed 0% junk. That was in
the original as a footnote; Fable went and measured it instead: a fresh 60-item sample of exactly
that stratum found **1/60 junk** (1.4% [0.3, 7.7]).

```
                    as reported     corrected
baseline junk          9.8%          ~10.6%
residual after gate    6.7%           ~7.6%   = 0.62/story = 9.8/run   (upper bound ~0.93/story)
"phantom" share         72%           67-70%
```

Direction and rough magnitude survive. **The point fact does not.** Report the residual as
~7.6%, lower-bounded by construction, upper bound ~1 junk citation per story.

The single junk item found in that stratum is instructive: Kim Yo-jong denying a *troop-deployment*
claim, cited under her *summit-contact* denial. Entity-heavy, different event — **invisible to the
lexical metric and to the embedding alike**, catchable only by a reader or an LLM.

## And "cut precision 52% -> 70%" does not survive

I repeated that figure. It comes from **mixed label tiers** — lexical labels for English, hand
labels for non-English. Under uniform blind-hand accounting the multilingual gate's own precision is
**57% [42, 71]**, against MiniLM's 68–77% on n=22. The confidence intervals swamp any
precision-improvement claim.

**Ship-multilingual still stands, on the other channel.** The non-English cut rate
**41.0% -> 3.3%** (98/239 -> 8/239) is deterministic, full-population, and reproduced independently.
The English-only encoder destroys ~88 correct foreign-language corroborations per this window. That
is the argument; drop the precision claim from the story.

## The argument against an LLM gate needs re-basing

Two of its three legs are weaker than stated:

- *"a population the gate has already skimmed"* — 53% of residual junk is IN_ANCHOR (the embedding
  confidently wrong), and the newly-measured unflagged-keep class is embedding-blind by
  construction. Both are precisely LLM-shaped, not gate-shaped.
- *"validating each change needs the same hand adjudication"* — **not defensible.** This day proved
  the cheap alternative: hand-reading the *delta* (35–60 items) is what killed the size_floor rule.
  The same delta-audit would price an LLM gate in an hour.

What does stand is **prize and priority**: ~10 junk citations per run at maybe 60% achievable
precision is a small prize against everything else open. So "don't build it now" is right;
"cannot be validated affordably" was wrong.

## Two things confirmed rather than overturned

The size_floor kill reproduces under Fable's independent labels — extra cuts **26% precise vs 57%**
for shared cuts (prior rater: 23% vs 58%). The "validated-but-wrong rule" finding is real.

And a protocol artifact worth fixing before any re-run: the sampling scripts printed the story
summary **truncated at 230/260 chars**, so both raters judged a partial view on at least one story
where the deciding sentence sat past the cut. Effect is small and junk-inflating — i.e. conservative
for the conclusion — but print the full summary next time.

## Net

Ship the multilingual encoder, on the corroboration evidence. Change nothing else. Report the
residual as ~7.6% with an upper bound near 1/story rather than "closed", and keep the LLM gate
shelved on priority grounds with its target class now precisely identified.

---

# Line closed: eight levers tested, one survives, and the residual is a definition not a defect

*2026-08-31. Consolidated result of the junk-citation work.*

## What was tried against the residual, and what happened

| lever | result |
|---|---|
| reweight the tag bag toward `primary_event` | **refuted** — AUC does not transfer; entities are more *reproducible* (0.903 vs 0.740) |
| embed the `primary_event` phrase | **dead end** — +0.3pp retrieval rank-1; TF-IDF already recovers it via IDF |
| lower `min_size` | **dead lever** — junk below size 4 is 3.7%, not the 30% claimed |
| a better split rule (`size_floor`) | **killed by hand labels** — 23% precision on its extra cuts vs 58%; destroys 3.4 relevant per junk |
| Haiku citation filter, 1-pass | **drops 29.7%** against a ~9.9% junk base rate |
| Haiku, 3-pass majority | **null vs 1-pass** (p=1.00) — passes only wobble on citations that are *good* |
| Sonnet citation filter | **drops 20.8%** — still 2x the junk that exists |
| temporal kernel (`_time_kernel`) | **null** |
| entity-overlap outlier detection | **at chance** |
| **multilingual encoder** | **survives** — the only measured improvement |

## The temporal kernel is null, but not for the reason I predicted

I guessed the 28-hour fetch window was too narrow for temporal decay to have anything to work with.
**Wrong.** Measured spread is 24.1–25.9h, median pairwise |dt| ≈ 7h, sd 7.1h; at σ=6 half of all
pairs get K<0.5. There is plenty of arithmetic.

**Publication time simply does not carry story identity here.** dt separating same-cluster from
different-cluster pairs: **AUC 0.551**. Against the hand gold, junk vs relevant: **AUC 0.475–0.510,
every value inside its own permutation null band.**

And the granularity trap fired harder here than anywhere else today. At σ=3 the same condition reads
**−9.06pp dropped in at threshold 0.80** and **+16.74pp at matched pairs** — a **25.8pp sign flip**
between two defensible matching choices, against a claimed effect of 9pp. σ=72 is a built-in
negative control (mean K = 0.990, essentially the identity) and still "finds" up to −1.04pp.

**Action:** leave it off, and either delete `published`/`sigma_hours` from `join_tags` or document
them as measured-and-rejected. Dead parameters that look like an available lever are a trap for the
next person — this one cost a day of speculation before anyone measured it.

## The arXiv entity inversion does not transfer

The paper found entities hurt clustering but help outlier detection. Tested here, entity overlap
against co-cited peers is **at chance**: AUC 0.497 (null band [0.401, 0.596]); English-only 0.510,
p=0.423; 31–38% precision across the entire frontier against 34.2% chance. The gate reads 67.3%
precision / 66.0% recall on the same 146 items against the stray rule's 37.8% / 34.0%.

Not complementary either: φ = 0.154, and the union is *less* precise than the gate alone (48.6% vs
67.3%). Sampling-bias checks (uniform draws, IPW reweighting) leave the ranking intact.

## What actually remains

**One shippable change:** the multilingual encoder, on the non-English cut rate **41.0% → 3.3%**.
Deterministic, full-population, independently reproduced. It stops the pipeline destroying ~88
correct foreign-language corroborations per window — which is also the only finding here that
serves the source-diversity goal rather than trading against it.

**And a product decision, not an engineering one.** Two MODEL raters (opus-5, fable-5) reached κ=0.809, and the
residual disagreement sits exactly where every model fails: **is an editorial, explainer, or
market-reaction piece about the story's own event a citation worth showing?** The raters say yes.
Every model — Haiku, Sonnet, and the embedding — says no.

That is why the models drop 2–3x more than the junk that exists: they are not miscalibrated
detectors of an agreed target, they are applying a stricter rule than the humans wrote down. Haiku's
hand-audited reasons are *accurate* ("editorial commentary", "background explainer", "stock
reaction") — it is answering a different question correctly.

**No amount of model quality resolves a definitional split.** Until that question is answered, the
residual is not measurable, and every filter built against it will keep destroying citations a
reader would have wanted. The next move is a decision, not an experiment.

---

# THE LABELS ARE NOT GROUND TRUTH — read this before trusting anything above

*2026-08-31, Sean's catch. It undermines most of the junk-citation findings in this document.*

Everything above described as "hand adjudication", "hand-corrected", "human-equivalent", or
"blind hand verdicts" was produced by **Claude agents applying a rule written by another Claude
agent**. The 160-verdict gold and its **kappa 0.809** measure two instances of the same model
family agreeing — not inter-human reliability. This is the Sonnet-vs-Sonnet self-agreement trap
`feedback_eval_ill_posed_metric` already warns about, and I walked into it while citing that lesson.

**The repo had already diagnosed this disease.** `project_eval_floor`:

> `why_judge_golden.json` (45 cases) is the ONE *independently* human-labeled golden — agreement
> 0.867. `coherence_golden.json` agreement=1.0 is **partly circular**: only ~21 cases were
> independent deep reads. Do NOT treat COHERENCE as a validated maximization target. The real work
> is more independent labels.

## Triage

**Survives (counts events, not judgements):** the non-English cut rate **41.0% -> 3.3%**;
the drop-rate-vs-base-rate arithmetic *given* a base rate; ONNX cosine fidelity; the temporal
kernel's AUC 0.551 co-membership result; token/cost/latency measurements throughout.

**Needs human confirmation:** "72% of the junk problem does not exist" (9.8% vs 35.0%); every
precision/recall figure; "editorial and market-reaction pieces are relevant citations"; the
`size_floor` kill.

**Void as stated:** any claim of the form "the raters say X" — it means "the model I prompted
said X".

## Independently found again, from the other direction

The per-model conformance audit reached the same place unprompted: `coherence.md`'s header cites
recall 0.761 -> 0.919, and that **survives only because the labels were revised after the sweep**,
partly from blind adjudication of flags the adaptive arm itself raised. The header should read
"on labels corrected after the sweep". One half of the revision (`S2_DISCOVERED`) was verified by
word-boundary grep of cited sources — mechanical and clean. The other half was not.

---

# Per-model conformance audit: my hypothesis was wrong, the inversion is the finding

**COHERENCE is NOT double-paying under adaptive thinking.** Visible output fell **3.1x**
(15,928 -> 5,140) as thinking went 0 -> 26,808. The reasoning moved cleanly.

**But the correlation inverted, on both models:**

```
r(visible_output, recall)      n     p
sonnet-5 / disabled   +0.584   17   0.005
sonnet-5 / adaptive   -0.611   12   0.015
opus-5   / disabled   +0.726   17   <0.001
opus-5   / adaptive   -0.522   12   0.053
r(thinking_tokens, recall)  = -0.236 (n.s.)
```

Under thinking-off, narration **was** the check. Under adaptive, a run that writes more visible
prose catches **less**, and thinking volume predicts nothing. `coherence.md:56` ("run the three
probes on each **before writing that story's result**") sequences against the channel that now
anti-correlates with the outcome.

## VERIFIED DEFECT: the stale-world-state auditor has no date

`coherence.md:37` demands: *"Check against the cited articles **and today's date**, NEVER your own
prior knowledge of who holds office."*

`{{CURRENT_DATE}}` appears in `write.md` and `repair.md`. **It does not appear in `coherence.md`**,
and `render_body` only substitutes where the token occurs. So the one stage auditing everyone
else's stale-world-state claims is the only stage never told what day it is. Cheapest real fix on
the list, and testable against a mechanically-labelled error class already in the planted sets.

## Other findings worth keeping

- **Thinking is now dark.** `display` defaults to `"omitted"` on Sonnet 5 (was `"summarized"` on
  4.6). ~27k thinking tokens/run leave no trace — and `display` is **free**, since thinking is
  billed either way. Turning adaptive on cost us the readable narration that used to explain a
  miss. `parse_agent_spec` can only build `{"type": ...}`, so it needs a small change to pass it.
- **WRITE should go to Sonnet 5, not 4.6, if it goes adaptive.** Same quality (0.168 vs 0.162) at
  2/3 the cost and 1/3 the latency — 4.6 under adaptive burns 24k-122k thinking tokens with no
  ceiling discipline (20-minute calls).
- **The thread audit must NOT get adaptive**, despite looking like COHERENCE: `_run_sonnet` passes
  `tools=[]`, so there is no re-reading channel and the 5.8x cache collapse cannot occur.
- **Effort:** every stage is unset (= `high`). Sonnet 5 is the first Sonnet-tier model with
  `xhigh`, and there is documented headroom (recall 0.919, two 0/27 blind spots).
- **A stale comment to fix:** `orchestrate.py:145` / `claude_cli.py:171` state the effort ladder as
  `low|medium|high|max`; SDK 0.2.148 has `xhigh`. They also imply `effort` works on Haiku 4.5
  because a local canary passed — the vendor table says it errors. A canary passing is not evidence
  a parameter is supported; it may be silently dropped.

Proposed diff (7 hunks, not applied): `scratch/prompt-guidance/2026-08-31-per-model-conformance.diff`

---

# BROKEN INSTRUMENT: the L2 regression gate cannot fire

`eval_regression.compute_l2_stats` reads `judge_pass` **and** `label_pass` from the same committed
fixture and makes no model calls. In `coherence_golden.json`, `label_pass == judge_pass` for
**386/386** — that is the same array twice, not high agreement. So `agreement_rate 1.0`,
`fail_precision 1.0`, `leak_count 0` are **constants**.

**Rewrite `.claude/agents/coherence.md` badly and `make eval` still prints 1.0.** The number only
moves if someone hand-edits the fixture. `label_rationale` is templated too: 3 boilerplate stems
cover 380 of 386 cases, one reading *"Independent verdict: UNFAITHFUL (agrees with judge…)"*. Six
cases have bespoke rationales; four of those are model-authored `AUDIT CORRECTION` edits.

The `_meta` is honest ("NOT 381 independent deep reads"). The number that escaped into
`eval_baseline.json` is not. This is the repo's own lesson firing on itself —
`an-audit-record-of-the-verdict-cannot-audit-the-verdict.md`: *"If you cannot write a failing test
that says 'this record shows a wrong decision', the record cannot show one."*

**Second gate defect:** `grade_selections` registers **11** L1 checks; `eval_baseline.json` lists
**10**, and `_compare_l1` files an unknown check as a *note*, not a regression. The ungated one is
`no_internal_article_ids` — born from the run-247 subscriber-facing ID leak. Fix:
`bin/eval-regression --update`.

# Eval framework: adopt nothing. Two candidates are quietly dead.

Checked today via `gh`: **`ragas` last merged 2026-02-24**, **`openai/evals` last merged
2026-04-14** — neither archived, so `isArchived: false` hides both stalls. Exactly the failure the
repo's prior-art rule exists to catch. Live: promptfoo (MIT, pushed today), inspect_ai (MIT), deepeval.

Neither worth adopting. promptfoo's labelling UI labels *cells of a promptfoo eval*, so it needs an
adapter costing more than the purpose-built page below, plus a Node runtime and default-on
telemetry — and its "Human Eval YAML" export **generates assertions from the existing pass/fail**,
freezing current verdicts: the circularity we are escaping, automated. inspect_ai is the closer
call for its `krippendorff_alpha` / clustered `stderr`, but the one thing it would have caught has a
free fix.

**That thing:** every CI computed today treats 3,022 citations as independent. They are **nested** —
3,022 citations in 346 stories in 22 runs. At ICC 0.10 a stated +/-3.0pp is really +/-4.0pp
(story-level) or +/-4.7pp (run-level). Fix costs nothing: **sample at most one citation per story**
and the cluster size is 1.

# The labelling protocol: ~100 labels, ~63 minutes

Labelling the quantity directly is impossible — 10% vs 7% needs **2,706 labels (~19 hours)**.
So label the **rater**, not the corpus: stratify on the model's verdict and take a census-weighted
estimate, where Sean's labels only pin PPV and NPV.

```
 60 labels -> +/-3.8pp      100 labels -> +/-3.0pp   <- separates 10% from 7%
150 labels -> +/-2.6pp      (allocation flat between 40/60 and 50/50)
```

The first **50** labels already answer "is the model rater usable at all" (agreement to +/-10pp).

**Step 0 outranks all 100 labels: ~10 definitional cases, 15 minutes.** Is a dedicated reaction
piece a citation you want? A market wrap? An explainer adding no new event? Then Sean writes the
rule in one paragraph and freezes it. The 100 labels are meaningless under a rubric he has not
endorsed — and "the raters say editorial pieces are relevant" reduces to *opus-5 and fable-5,
applying a rule opus-5 wrote, agreed with it*.

Interface: a generator emitting one **self-contained static HTML file** — keyboard-driven y/n/?,
`localStorage`, copy-JSONL button, no server, resumable, ~1 hour to build. Not a CLI: at 25-60s per
item the binding constraint is *reading*, and a terminal wrapping a 200-word summary against a
source excerpt degrades the label quality that is the entire product. Reuse `make_worksheet.py`'s
blind protocol (strips model identity, opaque ids, shuffles, withholds `map.json`) but **dedupe by
claim before rendering** — its Markdown repeats the same 400-char summary 16 times for 16 flags.

Honesty controls: strip model verdicts from the payload entirely (not CSS-hidden); 15 duplicates
bound your own flip rate at <20% by the rule of three (30 for <10%); allow `?` and report the
unresolvable rate; two 30-minute blocks, not one 60-minute slog.

**COHERENCE is different:** 60s/item means 100 labels is 100 minutes, and against fulltext ~250
minutes. Do 60 labels (~60 min, +/-5-6pp) or keep leaning on planted errors, which cost zero human
time and are already non-circular by construction.
