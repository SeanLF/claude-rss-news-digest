# Pre-deploy validation — extract→join branch (2026-07-01)

Item 4 of the improvements loop. Validates that branch `feat/extractjoin-cluster-stage` is
deploy-ready, so the deploy is a one-step go when Sean greenlights it. **Deploy is HELD — this
run did NOT merge to main or run `bin/deploy`.**

## (a) Full dry run on the freshly-built multi-stage image — PASS
Command: `docker compose --env-file .env run --rm --build digest-newsroom --no-email --no-record
--force` (rebuilt the image first, so this validates exactly what would deploy: extract→join
CLUSTER + the Item-1 WRITE date-injection fix + dormant time-decay code, all committed).

Result — clean end-to-end on a real fresh fetch:
- **Image builds** (multi-stage, Debian slim). Fetched 751 articles → 545 to Claude (206 TF-IDF
  deduped).
- **CLUSTER (extract-join):** 545 articles → **240 clusters** (thr 0.80, $0.75, Sonnet-4-6
  extraction). Healthy shape: 143 singletons (~60%, normal), max cluster 26 (no junk mega-cluster),
  top stories clean + distinct (Mexico World Cup crowd deaths, SCOTUS birthright ruling, HK
  handover, Venezuela quakes, US-Iran talks…). No degeneration, no coverage-guard trip.
- **Item-1 fix HOLDS on a real run:** the digest is about the Trump administration throughout —
  **0 "Biden" mentions**, 11 "Trump", 3 "administration" (all correct current-context), 0 stale
  office-holders. The date injected correctly ("Wednesday, July 1, 2026").
- **WRITE quality high:** 5 must_know + 11 should_know drafted; specific, non-filler
  `why_it_matters` (Mojtaba's clerical-legitimacy consolidation; SDF Kurds excluded from lawmaking;
  USMCA annual-review leverage; UK/UAE arms to the RSF; Trump crypto income vs Gulf Iran talks).
- **COHERENCE works:** flagged + dropped 1 should_know headline (Israel/Lebanon MoU) via the
  `pass:false` gate; assembled 5 must_know + 10 should_know.
- **Render:** wrote `digest-2026-07-01-1740Z.html` (44 KB, correct title/date, 76 headline rows),
  the ONLY template token left is the intentional `{{{RESEND_UNSUBSCRIBE_URL}}}` merge tag (filled
  by Resend at send). 0 "Biden" in the HTML.
- **Flags/broadcast:** `--no-email` → broadcast skipped; `--no-record` → no DB write; container
  exited 0. Total pipeline **$2.74** API-equivalent (in line with the historical ~$2.5-3/run).
- **Only non-fatal L1 flag:** one should_know `why_it_matters` at 61 words vs a soft 60-word cap
  (cosmetic, non-blocking) — normal WRITE-length noise, not a regression.

## (b) Cross-family judge re-score of the gate — DONE for the load-bearing dedup signal
NIM/Olla was down mid-session, then came back; started Olla (`bin/sdk-canary` unrelated — Olla is
the NIM proxy, keyed from 1Password) and re-scored the gate's `internal_dups` finding with
**Nemotron** (non-Anthropic) via the exact gate methodology (`scratch/xfam_dedup_probe.py`).
Result (full detail in `2026-07-01-graph-gate-preregistration.md`): re-scored BOTH primary signals
with the non-Anthropic panel (GLM→DeepSeek→Nemotron, first-to-answer under the throttle) via the
exact gate logic (same cached opus reference, judge-only swap; `scratch/xfam_full_rescore.py`;
n=6 holistic / n=5 EJ, 0 judge failures). **internal_dups: EJ 0.00 vs holistic 1.17 (dedup win
CONFIRMED, mean identical to the opus judge). miss_hard: EJ 2.20 vs holistic 2.17 = PARITY
(CONFIRMED — the over-merge/hidden-major fear does not reproduce cross-family).** The only cross-
family divergence is `miss_all` (EJ misses more soft-tail MINORS, 8.4 vs 5.7) = the known ~10%
coverage-dip signature on the non-gated secondary signal. Both primary gate signals now hold under
a different model family; the KEEP/PASS verdicts never hinged on this — it is confirmation.

## (c) Whole-branch adversarial audit (5 parallel agents, 2026-07-01)
Each commit was reviewed in isolation, but the branch as a WHOLE (a base-image migration +
clustering-stage replacement on a live service) had not been. Ran 5 deep agents in parallel:
clustering-stage correctness, Docker base-image migration (rebuilt on the real `python:3.14-slim`
amd64 target), orchestration+WRITE, production silent-failure/outage, and test-coverage gaps.

**Verdict: no deploy-blocker. Migration verified sound; one real bug + two hardening gaps FIXED.**

- **FIXED — coverage-guard bug (found independently by 3 agents).** `cluster_extractjoin.py`'s
  fallback guard measured `article_id in tags` (key-presence), not usable tags — so an extractor
  echoing the schema with EMPTY entities/keywords/primary_event (prompt drift, a degraded model, a
  bad `CLUSTER_EXTRACT_MODEL` swap) passed the guard and silently shipped an all-singleton
  degenerate partition — the exact failure the guard exists to prevent. Now gates on a join-usable
  tag token (`_TOKEN_RE.search(_tag_bag(...))`); empty-content items count as fallback (gate trips
  fail-closed if >25%, else title-only). TDD: a failing `test_stage_raises_on_empty_content_items`
  first, plus a partial-empty-fallback test.
- **FIXED — scientific stack unpinned in a compiler-less image (Docker agent, Medium/latent).**
  numpy/scipy/scikit-learn floated; a future rebuild resolving a version without a cp314 manylinux
  wheel would source-build and FAIL the prod build (slim has no compiler — a NEW failure mode since
  sklearn is new on this branch). Pinned all three in `constraints-prod.txt` to the wheel-backed
  versions verified on the slim/amd64 target (numpy 2.5.0 / scipy 1.18.0 / scikit-learn 1.9.0);
  rebuild confirmed clean + `import sklearn.cluster` OK; a test guards the pins.
- **FIXED — degraded-but-shipped runs were only a `logger.warning`.** The 0–25% fallback band
  ships a degraded (title-only) partition; bumped that log to `logger.error` so it surfaces in
  monitoring the same day. Added the first multi-batch extraction test (all prior fixtures were
  single-batch, so the per-batch failure-isolation the stage exists for was untested).
- **Verified SAFE (Docker migration, empirically rebuilt on target):** UID/GID volume compat
  (1001:1001 identical to Alpine → prod data volume stays read/writable), venv relocatable,
  sklearn/scipy/numpy manylinux wheels install (sklearn vendors its own libgomp), native `claude`
  CLI works, rollback safe (no new migrations). Fail-closed verified end-to-end: a cluster raise
  aborts the run BEFORE render/broadcast — a total extraction failure ships NO email, not a broken
  one.
- **Noted, not fixed (low/by-design):** the cluster branch has no whole-stage clean-slate retry
  (intentional — per-batch `with_retry_async` + fail-closed); no cluster-size cap vs the old
  holistic "≤25/cluster" (gate-accepted behavioural divergence); usage slightly under-counts failed
  batches. **Recommended follow-ups for the deploy (Sean's call, not applied):** add a post-build
  `docker run <image> .venv/bin/python -c "import sklearn; import cluster_extractjoin"` smoke test to
  `bin/deploy` (CI only builds the single-stage `Dockerfile.ci`, so venv-relocation isn't gated),
  and consider a real health-alert (not just a log) on any title-only fallback.

## Deploy readiness: GREEN (held for Sean)
The branch runs clean end-to-end on the image that would deploy, produces a high-quality digest,
and the Item-1 fix holds on real output. Nothing blocks deploy.

**When greenlit (Sean's call), the deploy sequence is:**
1. `deploy-check` skill (final safety pass — note it may need prod/SSH reachability, which was
   unavailable this AFK session).
2. Merge `feat/extractjoin-cluster-stage` → main.
3. `bin/deploy` — confirm the `-target` list includes the **newsroom service unit** (the
   Alpine→Debian base-image migration redeploys the whole newsroom service, not just code).
4. Watch the first cron run.

⚠️ NOT done here: no merge, no deploy. Held for Sean's explicit go.
