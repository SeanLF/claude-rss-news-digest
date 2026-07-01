# Improvement roadmap — synthesized from a 3-agent investigation (2026-07-01)

Three grounded research+analysis passes (clustering/coverage, prompt self-improvement, SDK/
observability) after the extract-join branch was validated + audited. This is the prioritized
backlog they produced. Each item: mechanism, value, effort, risk. **Nothing here is shipped except
the one item marked DONE.** The recurring meta-finding: *measure before you optimize* — the
highest-value moves are building the missing rulers, not running optimizers against saturated ones.

## Guiding discipline (do not skip)
- **Free structural screen before any paid gate.** Every clustering candidate below can be
  pre-screened deterministically (no LLM) on the on-disk tags for runs 205/206 via the
  `scratch/timedecay_structural.py` pattern (Δ articles-in-top-N clusters at *matched granularity*).
  That is exactly how time-decay was killed for ~$0. Only survivors go to the `tg_parallel.sh` +
  `tg_judge_product` product gate (n≥6, cross-family now that NIM works).
- **Don't optimize a saturated or circular metric.** (The self-improvement finding, below.)

---

## Track A — Clustering: recover the ~10% coverage dip
> **UPDATE (2026-07-01, free structural screen `scratch/coverage_screen.py` on runs 205+206):**
> **A1, A2, A3 are STRUCK — the cheap tag-space fixes cannot recover the dip, and the dip is not a
> prod problem anyway.** Two findings:
> 1. **The recall signal is not in tag space.** A1 (re-weight: entities×3→×2, primary_event×2→×3)
>    *reduced* head coverage on both days (Δ −2 to −22 — entities are the load-bearing same-story
>    signal). A3 (absorb small satellites into top-N heads) was inert (Δ≈0 at radius ≥0.45). The
>    diagnostic is decisive: **96% of non-head articles are tag-orthogonal (<0.3 cosine) to every
>    top-20 head** (271/281 on 205, 277/289 on 206). The under-cited articles are same-story but
>    lexically dissimilar (reactions / different-angle / different-entity), so *no* tag-cosine fix
>    — including A2's citation-expansion in tag space — can find them. Only a SEMANTIC signal
>    (A4 embeddings) or better extraction could. This is a $0 kill of A1/A2/A3, same as time-decay.
> 2. **The dip is Haiku-extraction-only; prod is already coverage-neutral.** `CLUSTER_EXTRACT_MODEL`
>    defaults to `claude-sonnet-4-6`, which the gate A/B showed recovers coverage to ≥ holistic. The
>    ~10% dip is the *Haiku*-extraction result. So Track A is really "enable cheaper Haiku extraction
>    without the coverage cost" — a POOL optimization, not a prod-quality fix. **De-prioritized.**
>    A4 (embeddings) is now the *only* structurally-viable path, and its value is pool-savings
>    (Haiku extraction), not fixing a live defect. Pursue only if the weekly pool gets tight.


The dip is an isolable head-cluster **recall** problem: extract-join's entity-dominated tag bag is
tuned for precision (it wins dedup 0 vs holistic) but under-recalls peripheral same-story articles,
so SELECT surfaces fewer sources/bias-buckets. Ruled out: **time-decay** (inert on the single-day
window, falsified). **Sonnet extraction** already recovers coverage to ≥ holistic at ~+$0.4/run —
the paid fallback; the point of A1–A3 is to recover it *cheaper* or stack under it.

- **A1 — Re-weight/enrich the tag bag** (CHEAPEST, try first). In `_tag_bag`: entities ×3→×2,
  primary_event ×2→×3, optionally fold in title tokens ×1; re-tune the join threshold to matched
  granularity. Zero added cost, pure string edit, free structural screen. *Risk:* title tokens can
  reintroduce over-merge (the failure EJ wins on) — the screen + product gate catch it.
- **A2 — Decoupled citation-expansion at SELECT/render** (SAFEST, highest conviction). Leave the
  partition byte-identical (dedup win untouched); *after* SELECT picks a head cluster, a
  deterministic post-pass attaches same-story articles from adjacent satellite clusters to that
  story's **citation list** (not the cluster) via cosine to the head centroid on the existing
  TF-IDF matrix, above a conservative bar. Recovers `sources_total`/`bias_spread` on exactly the
  surfaced stories; COHERENCE backstops a mis-attached citation. *Effort:* a step between SELECT and
  `merge.assemble_selections`/`resolve_article_ids`. This is the recommended build — it recovers the
  exact down-metric while *provably* preserving the dedup win.
- **A3 — Head-cluster recall-expansion pass** (attacks the mechanism in-partition). Keep tight 0.80
  for the precision partition, then a looser second pass (radius ~0.55–0.65) that absorbs small
  satellites into top-N-by-mass heads only, capped. *Risk:* moves boundaries → must re-run the full
  dedup + miss_hard gate. Overlaps A2; A2 is safer, A3 carries the fuller cluster into threads/gists.
- **A4 — Additive static-embedding join signal (model2vec/potion)** (MEDIUM build, strongest lit).
  Prior-art number: entity-bag 86 → +entity-embeddings 94.8 BCubed (Saravanakumar 2021). Blend
  `sim = α·tag_cosine + (1−α)·embed_cosine` with a tiny (8–30 MB) CPU static model baked into the
  image. *Risk:* embeddings raise recall AND merge distinct stories — α must be tuned or it erodes
  dedup; a new dep on the constrained box.
- **A5 — Learned merge classifier** (BIGGEST, DEFER). Miranda/Priberam: learned pair-classifier 94.1
  F1 vs 82.8 for a scalar cosine threshold. The natural decision layer for the planned persistent
  SQLite+networkx story-graph. *Defer:* the only cheap training labels are Sonnet/holistic partitions
  → reintroduces the conformity-to-Sonnet circularity the product gate exists to escape. Wait for the
  graph substrate + real cross-day labeled pairs.

Refs: Saravanakumar arXiv:2101.11059 · Miranda arXiv:1809.00540 · MinishLab/model2vec.

## Track B — Evaluation: build the ruler, NOT a self-improvement loop
**Verdict: a WRITE/SELECT prompt self-improvement loop is not worth building now — and running one
would be a Goodhart trap.** The blocker is the ruler, not the optimizer: the one trustworthy
high-res WRITE metric (filler, why_judge golden 0.867) is *already saturated* (0.000 [0,0] at n=6,
recall 0.765 / precision 0.867), so an optimizer would just discover the judge's ~24% false-negative
region and bland out the ~13% it wrongly dislikes — making the digest worse as the metric improves.
The dimension with real headroom (faithfulness) has only the rare-event, partly-circular
`coherence_fail` count — a bad optimizer target.

- **B1 — Build the faithfulness/specificity ruler** (the high-value investment). write.md's ~40
  lines of anti-overstatement rules (added-precision, truncation-completion, unsupported-attribution,
  asserted-causation, stale-world-state) already *are* a defect taxonomy — but nothing measures
  compliance. Turn each into a labelable per-item defect class → a validated judge (mirror
  `eval_why_judge.py`), validated the way filler was: an independent **human** golden (~100–150 cases,
  reuse `scratch/error-analysis/`), report agreement/precision/recall, cross-family adjudication. This
  is a standing regression gate + monitoring metric regardless of any loop — which is why it dominates.
- **B2 — GEPA self-improvement loop** (ONLY after B1 shows headroom). If the ruler shows control has a
  non-trivial defect rate: GEPA (not OPRO/APE — reflective, few-shot, Pareto-multi-axis) over
  write.md, seeded from real failing items + judge `reason` strings. **Load-bearing gate:** a
  candidate may be *proposed to a human* only if it beats a frozen held-out split under a
  *different-family* judge by a margin clearing control's n≥6 band. Six overfitting guards (frozen
  held-out, cross-family gate, Pareto dominance, anti-metric watch, human terminal gate, re-label the
  loop's own output). The loop proposes; a human disposes. `eval_why_judge.py`'s docstring already
  names GEPA as the intended consumer.

Refs: GEPA arXiv:2507.19457 / dspy.GEPA · judge-disposition overfitting arXiv:2604.20726 · error-
taxonomy-guided optimization arXiv:2602.00997.

## Track C — SDK usage: cost/latency/reliability + observability
Reframe: billing is subscription (real $ = 0); the constraint is the **weekly usage pool** + **wall-
clock latency**, not dollars.

- **C1 — Parallelize the extraction batches** (LATENCY, high impact). `run_extractjoin_stage` runs
  ~13 sequential subprocess spawns; they're independent → `asyncio.gather` with a semaphore (start 4).
  Cluster-stage latency down ~3–5×. Precedent: `tg_parallel.sh` ran concurrent chains on one OAuth
  token with 0 429s. *Risk:* 429s under concurrency — gate with a semaphore + the existing per-batch
  retry. (orchestrate.py already has a CLUSTER+RECAP gather TODO for the same reason.)
- **C2 — Fatten the extraction batch** 40→80–100 (fewer spawns/cold prefixes; output stays well under
  the 32k ceiling). Combine with C1. A/B the partition on one snapshot first.
- **C3 — Verify extraction prompt-caching actually hits** (a check, not a change): read
  `cache_read_tokens` vs `cache_write_tokens` on a prod `cluster` run_usage row; ~0 cache_read means a
  prefix is drifting. (Now easy — see C6.)
- **C4 — Move COHERENCE from Sonnet to Haiku** (usage-pool; open TODO). Mechanical per-headline
  fact-check = Haiku register (RECAP already moved). Config/frontmatter-only; validate on a couple
  snapshots (COHERENCE golden is partly circular). `claude-haiku-4-5` already pinned in usage.py.
- **C5 — Haiku extraction as a documented pool-pressure valve** (`CLUSTER_EXTRACT_MODEL=claude-haiku-
  4-5`): the ~10% dip is in-band and buyable back if the weekly pool ever gets tight. No work; keep
  the knob.
- **C6 — Persist per-stage latency (`run_usage.duration_ms`) — ✅ DONE this session.** duration_ms was
  computed by the SDK and logged but discarded; now persisted (migration + threaded through), so
  per-stage latency + the cache_read:write ratio (C3) are queryable. Follow-up: surface both in the
  Rust `/stats` dashboard.
- **C7 — OTel to a hosted free-tier backend** (OPTIONAL phase-2, needs a backend decision). Claude
  Code is the OTel emitter; wiring is pure env (`CLAUDE_CODE_ENABLE_TELEMETRY=1` + `OTEL_*`, per-stage
  `OTEL_RESOURCE_ATTRIBUTES="stage=…"` in `_build_options`). Exports per-turn `llm_request` spans
  (model+latency+tokens) — useful for the multi-turn SELECT/WRITE. **Do NOT self-host SigNoz/
  ClickHouse on the 4GB CX23**; push to Grafana Cloud/Honeycomb/Dash0 free tier. Never set the
  `console` exporter (SDK uses stdout as its channel). For a 5-stage daily batch, C6 + `/stats`
  covers ~80% of the need; C7 only if you want per-turn traces.
- **C8 — Retry simplification: DEFER, guard with the canary.** 2.1.198's native transient-streaming-
  retry is tempting, but it's count-bounded (our budget is a 4h wall-clock outage ride-out) and
  doesn't retire the #378 teardown workaround (orthogonal CPU-spin bug, still unfixed in the SDK).
  Add a `bin/sdk-canary` assertion that fires when the SDK surfaces structured `api_retry` messages,
  then reassess. Low priority; current path works.

Refs: Agent SDK observability docs · Claude Code prompt-caching docs · CC changelog 2.1.193/2.1.198.

---

## Recommended ordering (lowest-regret first) — UPDATED after the screen
0. ~~A1/A3 free structural screen~~ — **DONE**; killed A1/A2/A3 (tag-space can't recover the dip) and
   reframed Track A as a pool-optimization (prod's Sonnet extraction is coverage-neutral). Track A is
   now DEFERRED (only A4-embeddings could enable cheap-Haiku-with-coverage, and only if the pool bites).
1. **C6 follow-up + C3** — surface latency/cache-ratio in `/stats`, confirm caching hits. (Cheap, the
   data now exists after the duration_ms commit; `/stats` is Rust.)
2. **C1/C2 (parallelize + fatten extraction)** — the real remaining win: latency ~3-5×. NOTE: this
   CHANGES the extraction stage's runtime behaviour, so it invalidates the sequential-stage dry-run
   the deploy was validated on — do it as its own tested effort (re-run the dry run), ideally AFTER
   the current deploy ships, not piled onto the deploy-held branch.
3. **B1 (faithfulness ruler)** — the durable eval investment; unblocks B2. Labeling-heavy (needs a
   ~100-150-case HUMAN golden — the crux; building the judge without it just reintroduces circularity).
   Its own effort, no deploy risk (eval infra only).
4. **C4 (COHERENCE→Haiku)** — pool saving; quick config change, needs a validation snapshot (COHERENCE
   golden is partly circular).
Defer: A4 (embeddings — only if the pool gets tight), A5 (graph-substrate phase), B2 (only if B1 shows
headroom), C7 (OTel — needs a backend), C8 (retry — watch).

**Key sequencing note:** everything added to the deploy-held branch this session was *additive/behaviour-
preserving* for the pipeline (docs, tests, a nullable column, an ops script, prompt guards) — safe to
ship with the extract-join deploy. The remaining high-value builds (C1 parallelize, A4 embeddings, C4
model swap) all CHANGE runtime behaviour and should be their own re-validated efforts, not last-minute
additions before the deploy.
