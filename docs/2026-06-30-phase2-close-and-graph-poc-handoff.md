# Phase 2 close + graph-first PoC — handoff (2026-06-30, session 2)

Follow-up to `docs/2026-06-30-sonnet5-eval-and-forward-plan.md`. Format: Landed / Result / Learnings / Strategic call / Next session / How-to / Open decisions.

## Landed this session
- **Phase 0/1 + SDK #378 verified & committed** (`b577a4a`): per-stage `effort`+`thinking` plumbed opt-in through `parse_agent_spec → _invoke_agent → run_agent → _build_options` (default unchanged = prod-safe); inert `effort: medium` removed from all 5 agent specs; `run_agent` teardown bounded with `asyncio.wait_for(aclose, 5)`. Review gate ran clean; 60 plumbing tests green. Prior handoff doc committed `2bd61bf`.
- **SDK version question answered:** `claude-agent-sdk` is **fully unpinned** in `newsroom/pyproject.toml`; BOTH `newsroom/Dockerfile` and `Dockerfile.ci` install via `uv pip install -r pyproject.toml` (the `uv.lock` 0.2.101 is unused). So prod and CI are **not skewed from each other** — both float to PyPI-latest (now 0.2.110) at build. Local dev venv + lockfile (0.2.101, what the #378 audit read) is what's stale. Real risk: unpinned SDK = a bad release breaks a fresh prod build; reinforces the canary-per-SDK-version plan (Phase 5).
- **Phase 2 "measuring stick" built** — `scratch/cluster_taskgrounded_eval.py` (gitignored). The task-grounded digest eval the prior handoff demanded, explicitly **NOT ARI**. Reusable; it's the go/no-go gate for the graph PoC.
- **Container-parallel fan-out proven** — `scratch/tg_parallel.sh`. 12 chains across two 6-way concurrent batches, one OAuth token, **0 failures, 0 rate-limit lines**.

## Result — Sonnet 5 CLUSTER is task-grounded NEUTRAL (question CLOSED)
n=6 per arm, downstream model fixed at prod's 4.6 so the CLUSTER *partition* is the only variable:

| signal | 4.6 partition | Sonnet 5 partition | read |
|---|---|---|---|
| coherence_fail | 0.67 `[0,2]` | 0.0 `[0,0]` | **inconclusive** — bands overlap; all four 4.6 flags fell in reps 1-3 (reps 4-6 = 0), a rare high-variance event, underpowered |
| sources_total | 97.3 `[83,113]` | 89.5 `[73,113]` | **mild edge to 4.6** (~8% more coverage), consistent direction |
| filler / kept / single_source | 0 / 17.0 / 5.0 | 0 / 16.8 / 4.7 | parity |
| chain_cost | $1.41 | $1.65 | Sonnet 5 ~+$0.24, **entirely in SELECT** ($0.41→$0.82; finer 298-cluster partition = more clusters to tier) |

**Verdict:** roughly quality-neutral. No faithfulness win survives n=6; mild coverage dip (finer clusters → fewer articles/story → fewer citations; matters because bias-diversity coverage is the product differentiator) + small downstream SELECT premium. The CLUSTER-swap case rests on the **objective cluster-stage wins** (cost-neutral +3.7%, 2.5× faster) minus that mild downstream cost — a speed-vs-coverage **product call, not a quality win**. Combined with the strategic call below: **don't act on this — the graph approach likely replaces holistic CLUSTER entirely.**

## Learnings (durable)
1. **My n=3 read was an overclaim — corrected by n=6.** At n=3 the 4.6 partition showed coherence_fail `[2,1,1]` vs Sonnet 5 `[0,0,0]` and I called it a "modest faithfulness WIN, clean separation." n=6 added 4.6 reps `[0,0,0]` → band `[0,2]`, overlapping Sonnet 5's 0. **The ARI-lesson trap repeating: a lucky draw looked like signal.** Always measure the reference's own rep band before declaring a delta; low-integer rare-event metrics need n≥6, and even then may be underpowered. (See `feedback_eval_ill_posed_metric`.)
2. **Parallelize on ONE token — no multiple tokens needed.** Concurrency under one OAuth is exactly what Claude Code subagent fan-out does. The 429s in the codebase (`why_judge` "sequential deliberately") come from a *high-request-rate tight loop*, NOT from concurrent agent sessions (low-rate, spaced). 12 concurrent chains → 0 rate-limit lines.
3. **The blocker to in-process parallelism is HARDCODED PATHS, not auth.** `.claude/agents/{select,write,coherence}.md` hardcode `/app/data/claude_input/` in the prompt; `claude_input_dir`/`cwd` only steer Python *validation*, so `asyncio.gather` with different dirs clobbers the shared files. Isolate at the **container** level: one container per chain, each with its own `./data` copy mounted at `/app/data`. (To parallelize in-process instead, you'd have to parameterize the path in 5 prod prompts + their invocation — riskier, do it behind the eval.)
4. **Run eval harnesses in the container via `.venv/bin/python`** — the SDK lives in `/app/.venv`, not system python. Mount `scratch` + `newsroom/src` + `.claude` over the image to run current code without rebuilding. `entrypoint.sh` does `exec "$@"` for non-dash args, so pass `.venv/bin/python scratch/…` directly.
5. **why-judge (`run_sync` → `asyncio.run`) can't run inside an event loop** — push filler scoring to `asyncio.to_thread` (fresh loop), or run it outside `asyncio.run` entirely.
6. **Usage/cost reality (use ccusage, not hand-math — my first estimate was ~3× low):** eval runs in Docker on the OAuth **subscription** (`.env` has `CLAUDE_CODE_OAUTH_TOKEN`, no `ANTHROPIC_API_KEY`) → counts against the weekly rate-limit pool server-side BUT is **invisible to host `ccusage`** (reads host `~/.claude`, not the container volume). The pool's `used_percentage` has **no absolute denominator** — $→% is empirical only. The weekly limit is driven by **interactive Opus sessions** (ccusage: June host ~$5.9k API-equiv, last-7d ~$1.16k), NOT the digest eval (~$1.7/chain ≈ 0.14% of a ~$1.2k/wk pool). Plan sizing: current pool ~$1.2k/wk ≈ ~$5.2k/mo API-equiv (≈ Max 5x, at the wall); Max 20x ≈ 4× → same usage ~24%.

## Strategic call — stop tuning current-arch clustering; go graph-first
- The CLUSTER *comparison* is **closed** (neutral). **Do not spend more on current-architecture cluster tuning** (cheap-clustering, more Sonnet-5 CLUSTER reps) — the graph redesign folds holistic CLUSTER away (Haiku per-article extract → deterministic join → thin refine), so that work is likely throwaway.
- **NOT wasted / keep:** (a) the **measuring stick** (`cluster_taskgrounded_eval.py`) — it's the go/no-go gate for the graph PoC and scores digests regardless of how clusters are formed; (b) **WRITE / SELECT / synthesis** model choices — those stages **survive** the redesign (they operate on subgraphs instead of clusters), so their A/Bs stay relevant, just **deferred** until the architecture is settled.
- Therefore the next real experiment is the **graph-first PoC**, not more current-arch stage tuning.

## ⚠️ The graph-first pipeline is ALREADY BUILT — reuse, don't rebuild
A sweep of `scratch/cluster-replay/` (50+ scripts) + `docs/2026-06-2{5,6,7,8}-*.md` shows the extract → join → refine pipeline was **built and validated (runs 204–205, June 26–28)** and **already cleared an initial task-grounded gate**. The next session's job is to **re-validate it with the NEW measuring stick on the current snapshot, then productionize** — NOT to build from scratch.

### Prior assets to REUSE (with results)
- **Extract (Stage 1):** `scratch/cluster-replay/extract_tags.py` — Haiku per-article `{entities, keywords, primary_event, time}`. >99% coverage (run 205: 464/465), ~$0.00015/article, batched (40/call), title-only fallback. Backends: Haiku (Docker) or DeepSeek (on-host, free). Output already on disk: `drafts/tags_haiku_{204,205,206,207}.json`. **The `primary_event` phrase is the load-bearing join signal — not generic entities.**
- **Join (Stage 2):** `join_materialize.py` (writes partition) + `join_eval.py` (TF-IDF on entity/tag bag, agglomerative) + `join_embed.py` (event-embedding + entity-Jaccard). **Result: TF-IDF join peak ARI 0.661 @ thresh 0.40, BCubed-F 0.913 (IN-BAND), pairwise-F1 0.663 — i.e. inside Sonnet's own 0.704±0.057 self-agreement noise.** Materialized partitions on disk: `drafts/clusters_extractjoin_haiku_{204,205}.json`.
- **Refine (Stage 3):** `refine_borderline.py` — thin cross-family split-only judge (~128 pairs/run vs ~29k holistic). **Result: raises precision 0.85→0.95 but recall cost offsets it → pairwise-F1 FLAT. It's a rebalance, not a gap-closer.** Output: `drafts/clusters_refined_extractjoin_haiku_204.json`.
- **Eval infra (already gold-free + de-biased):** `band_eval.py` / `band_eval_pairwise.py` (score any partition vs the reference band; stdlib-only), `adjudicate.py` + `judge_digests.py` + `test_judge_reconcile.py` (cross-family, order-swapped, position-bias-removed). **Prior task-grounded digest test: extract-join vs Sonnet = zero confirmed duplicates, zero confirmed misses.**
- **Embedding deflation harness:** `scratch/cluster-embed/embed_cluster_poc.py` — MiniLM 0.497 / model2vec 0.288 / TF-IDF 0.269 ARI. Use as a deflation floor, not a path.
- **Downstream / synthesis / threads (further out, validated):** `synth_poc.py`/`synth_batched.py` (96.4% faithfulness pooled, n=4), `late_bind.py` (entity-Jaccard soft-edge neighborhood), `evolving_thread.py`/`temporal_thread.py` (persistent graph across days; memory/fact wall is load-bearing). Docs: `docs/2026-06-27-graph-synthesis-direction.md`, `docs/2026-06-28-synthesis-forward-ideas-pocs.md`.

### DON'T REDO (learned + paid for)
1. **ARI-vs-one-gold** — broken ruler (4+ research strands agree). Use band + pairwise-F1 + the task-grounded digest gate.
2. **Single-pass judges w/o order-swap** — position bias flips verdicts 17–40%.
3. **Cheaper deterministic gates on over-merges** (`join_conjunction.py` FAILED) — residual errors are *quasi-identity/semantic* (e.g. "G7 AI-chip access" vs "US-China AI race"), not lexical. Only thin LLM refine touches them, and even that only rebalances.
4. **BCubed-F alone on singleton-heavy data** — inflates (all-singletons ≈ 0.804). Pair with pairwise-F1.
5. **Haiku as the holistic clustering model** — output cost explodes (`cluster-token-experiment.md` VHAIKU2). Haiku is for per-article *extraction* only.
6. **Trimming CLUSTER prompt narration** — reasoning is load-bearing; trimming adds cost.

### Next session — validate-with-new-gate, then productionize
1. **Regenerate extract→join on the CURRENT measuring-stick snapshot** (the on-disk partitions are runs 204/205 = 465 articles; the `data/claude_input/` snapshot is 290 articles → IDs won't match, so re-run `extract_tags.py` + `join_materialize.py` against this snapshot to get a matching `clusters.json`-shaped file).
2. **Score it with the NEW measuring stick** (`cluster_taskgrounded_eval.py`, parallel via `tg_parallel.sh`, n≥6) — drop the extract-join partition into `data/claude_input/sonnet5_ab/`, add to `PARTITIONS`, compare its **digest** to the holistic-CLUSTER digest. This is the apples-to-apples confirmation the prior work never got (it used `band_eval`/`judge_digests`, not the integrated SELECT→WRITE→COHERENCE chain). **GATE: digest quality ≥ holistic. NEVER ARI.**
3. **If it holds → close the productionization gaps** (below) and plan the actual pipeline integration (extract+join become the new CLUSTER stage; keep SELECT/WRITE/COHERENCE downstream).

### Open gaps the prior work left (the real remaining work)
- **Time-decay never modeled** — join used entity/tag TF-IDF with NO temporal signal; literature says entity-Jaccard + temporal proximity ≈ 92 BCubed-F. Add Gaussian time-decay (σ≈72h) to the join.
- **SQLite + `networkx` graph substrate not built** — joins are one-shot partitions; the persistent story-graph (nodes=articles, edges=same-event, carried across days for late-binding/threads) doesn't exist yet. This is the actual "graph" build.
- **Scale not benchmarked** — only run 204 fully materialized+refined; generalize to 205–207.
- **Learned join classifier untested** (Miranda's trick: small SVM over {entity-Jaccard, time-gap, event-cosine} vs hand-set threshold).
- **Rendering/length** — synthesis output exceeds email length; the tighten-to-digest stage doesn't exist.
- **PoC #2 (only if a substrate is needed):** LlamaIndex `PropertyGraphIndex` vs SQLite+`networkx`. GATE: adopt only if it beats the minimal stack on *capability*, not convenience.

**Keep (non-negotiable):** deterministic Python orchestration, small stages, ID-indirection, COHERENCE, the eval floor, the measuring stick. NO LLM orchestrator. Decouple grouping from ranking.

## How-to (reuse the built assets)
- **Run the measuring stick (sequential):**
  `docker compose --env-file .env run --rm --no-deps -v $(pwd)/scratch:/app/scratch -v $(pwd)/newsroom/src:/app/src -v $(pwd)/.claude:/app/.claude:ro -e REPS=3 digest-newsroom .venv/bin/python scratch/cluster_taskgrounded_eval.py`
- **Run it parallel (one container per chain):** `REPS=3 bash scratch/tg_parallel.sh` (use `OFFSET=3` to extend n without clobbering); combine → `scratch/tg_combined.jsonl`; aggregate → `python3 scratch/tg_aggregate.py scratch/tg_combined.jsonl`.
- **Point it at a new partition:** drop a `clusters.json`-shaped file in `data/claude_input/sonnet5_ab/`, add it to `PARTITIONS=<label>=<file>`.
- **Offline metric check (no model calls):** `SELFTEST=1 … .venv/bin/python scratch/cluster_taskgrounded_eval.py`.
- Fixed snapshot lives in `data/claude_input/` (+ `sonnet5_ab/` has both CLUSTER partitions and prior drafts). Harness backs up/restores the mutated files.

## Open decisions (unchanged — Phase 3 inputs, deferred)
1. **Recall semantics:** is `sources` for coverage (4.6's ~98%) or provenance (Sonnet 5's ~80%)? Recommend: coverage / high-recall (it's the bias-diversity differentiator); keep provenance as a separate internal citation self-check. Relevant to the mild coverage dip above.
2. **CLUSTER swap:** given neutral quality, is 2.5× faster clustering worth ~$0.24/chain downstream + mild coverage dip? Likely **moot** if graph-first wins — decide *after* the PoC.
3. **Phase 5 reliability** (tenacity retry, rate-limit sleep-to-reset via `RateLimitInfo.resets_at`, structured error classification, canary tests) — still its own session; don't entangle with feature work.
