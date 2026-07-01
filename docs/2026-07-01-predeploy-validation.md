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
Result (appended to `2026-07-01-graph-gate-preregistration.md`): **extract-join = 0 internal
duplicate groups under Nemotron too, holistic = 2.5 (Nemotron is even harsher on holistic than the
opus judge)** — the dedup win is confirmed ACROSS families, not just within Anthropic. Scoped to
n=2/arm (NIM free-tier throttled to its concurrency ceiling). The `miss_hard` cross-family re-score
stays deferred (heavier coverage-judging path, NIM throttled; the gate already treats miss as
parity, not an EJ win). The KEEP/PASS verdicts never hinged on this — it is confirmation.

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
