# Dedup PoC findings (2026-07-02)

Empirical validation of the wire-repost / dedup recommendations, against the prod-clone
`data/digest.db` (194 runs) and a 180-pair blind-judge study. Judges: 6 parallel subagents,
blind to similarity band, classifying dedup_log pairs as same-story / different-story and
extracting entity tags (production extraction rubric).

## A1 — the cross-day filter (`dedup.py`, title-TF-IDF @ 0.35) is net-harmful
Operational **false-positive rate = 65%** (band-weighted to the real dedup_log distribution).
- low band `<0.45` (65% of all firings): **74% FP** — dropping *different* stories
- mid band `0.45–0.70` (30%): 55% FP
- high band `≥0.70` (4.5%): 10% FP (trustworthy)

~24,214 filter actions over 194 runs ≈ 125 drops/run; at 65% FP that's ~80 legitimate,
distinct stories silently dropped per run *before Claude sees them*. Egregious real drops:
"Violet Grohl: Be Sweet to Me review" killed for a Trump/Abraham-Accords story; "Usborne
1980s Computer Books" killed for SpaceX finances; "You are not your job" killed for an
AI-hallucinations piece. Cause: ~8-word titles → sparse TF-IDF → collisions on shared
generic tokens ("what to know", "job", "part").

Caveat: "different story" ≠ always "harmful drop" (some dropped stories are low value, or the
story is covered by another non-filtered article). FP rate measures *precision of the drop*,
which is 35%. Even discounting, the loss is large.

## A2 — entity-overlap is a much better signal than title-TF-IDF
Predicting judge same-story label (n=178): **entity-Jaccard AUC 0.868 vs title-TF-IDF 0.766.**
Different-story mean entity-Jaccard 0.24 vs title-sim 0.43 (already above the 0.35 firing line —
that is *why* it misfires). We already compute these entity tags at CLUSTER time
(`cluster_extractjoin.py` → `{entities, keywords, primary_event}`).

Threshold sweep (weighted): title-TF-IDF cannot do both — T=0.35→35% precision/100% recall;
T=0.55→70%/29%; T=0.60→80%/23%; T=0.70→90%/12%. Tuning only slides a bad frontier.

## A3 — content fingerprinting (MinHash/SimHash) is OFF the critical path
Same-day reposts co-cluster *and* share near-identical titles → a title-normalized collapse
within the cluster handles them with no content fetch. MinHash would only add value for
reworded requotes, which aren't "duplicate links" anyway. Prior art noted, not built.

## A4 — same-day source-priority collapse is viable
86 cross-outlet verbatim repost groups across 17 cluster-tagged runs (~5/run). **70% contain
Reuters** → source-priority resolves directly. The 30% without a wire are (a) same-publisher
cross-feed dups (`scmp_china`+`scmp_world`, `haaretz_middle_east`+`haaretz_world`) and
(b) Straits-Times-as-serial-reposter. Both resolve by demoting known syndicators
(`straits_times`, `daily_maverick`). Canonical picks validated correct on real data.

## Rigour replication (addresses circularity + reproducibility)
The first pass had each judge extract entities AND judge same-story in one call (circular).
Replication: independent per-headline extraction (blind to label + to the paired headline) +
a SECOND blind judge pass. n=180, seeded, bootstrap CIs.
- **Reproducibility**: two independent judge passes agree 94%, **Cohen's kappa 0.87** (substantial).
  167 concordant high-confidence labels (69 same, 98 different).
- **Signal AUC vs concordant labels [95% CI]** (entities now INDEPENDENT of the label):
  title-TF-IDF 0.798 [0.72, 0.87]; entity-overlap 0.867 [0.81, 0.92]; **primary_event 0.923
  [0.88, 0.96]**; entity-or-pe 0.889. Delta (entity_or_pe − title) +0.089 [95% CI +0.008, +0.175]
  — **significant** (CI excludes 0). The entity/event advantage is real, not a circularity artifact.
  primary_event is the single strongest signal (matches what `cluster_extractjoin` already uses).
- **Dedup decision on concordant truth**: title-TF-IDF @0.35 = 41% precision / 100% recall
  (98 wrong drops). primary_event @0.2 = 82% precision / 87% recall. The better signal breaks the
  precision/recall tradeoff title-TF-IDF is stuck on.
- **Title-TF-IDF precision by threshold** (concordant truth): 0.35→41%, 0.55→83%, 0.70→89%,
  **0.80→100% (0 wrong), recall 22%**, 0.90→100%/10%.

## KEY architectural finding: cross-day dedup is already handled semantically, twice
There are THREE cross-day dedup mechanisms; the crude one is redundant with two better ones:
1. **prepare-time `TfidfMatcher`** (title-TF-IDF @0.35, 7-day window, PRE-cluster) — the 65%-FP one.
2. **SELECT** reads `yesterday_headlines.txt` (select.md: only re-cover a prior story for a specific
   new fact; "same topic with new framing is not sufficient") — semantic, 1-day.
3. **THREADS** Haiku linker (post-select, multi-day) — "is this the same ongoing story?".
The crude filter not only mis-fires (65% FP) but CONFLICTS: dropping articles pre-cluster on weak
title similarity can starve SELECT and kill THREAD continuations (its own log filtered "Massive
Russian Strikes Pound Ukraine's Capital" against yesterday's Ukraine-strikes story — a developing
story it should not kill). A full entity/primary_event REBUILD of the prepare filter would largely
DUPLICATE the semantic work SELECT + THREADS already do.

## The cost/quality catch (found by verifying against real data — DON'T ship the threshold blind)
Real per-run numbers: the filter drops **~150-220 articles/run** at 0.35; at 0.80 only **~1-10**
remain filtered. So raising the threshold sends **~180 more articles/run into the extract→join
CLUSTER stage** (Haiku per-article extraction) — a **material token increase** (~+30-40% of cluster
input), directly counter to the token-reduction project. Two things are UNMEASURED and gate the call:
1. **Is the FP actually costing SHOWN content?** "Different story than the matched headline" (the 65%)
   does NOT prove the dropped article was digest-worthy. The digest shows ~19 of ~440 clustered; many
   FP-dropped articles might be minor pieces SELECT would skip anyway. If so, the filter is a
   reasonable cost/quality trade, not a clear bug. Needs a counterfactual measure (would any
   FP-dropped article have been SELECTED?).
2. **The token cost** of the extra clustering should be measured, not assumed.

So the cross-day filter is **over-aggressive by precision (established)** but its **product harm is
unproven**, and fixing it has a **real cost**. This is a quality-vs-cost PRODUCT decision (Sean's
token project makes it his call), NOT a confident unilateral ship. The rigorous signal work
(primary_event AUC 0.92, kappa 0.87) stands regardless.

## Counterfactual (RESOLVES the fork) — the FP is NOT harmless
Ran the gating measure: 97 confirmed FP-dropped articles (concordant "no"), each checked against
its own day's digest by 5 world-news-editor judges. Verdict:
- **26%** covered in the digest anyway (harmless)
- **52%** lost but genuinely minor/off-topic (album reviews, gadget posts, opinion — SELECT would skip)
- **23% LOST and would-belong** — real world news, dropped, never covered: Guinea-Bissau coup, 4 killed
  in Kenya fuel protests, new Dutch PM, US sanctions on Kabila, EU-Ukraine membership talks, Peru
  anti-Fujimori protests, Costa Rica president-elect, Brazil election poll, Japan arms-export shift,
  a Chinese-agent espionage conviction, Lutnick Epstein testimony, US intel cuts, Finland war-readiness.
These are exactly the "breadth across regions" stories SELECT's prompt prioritizes. Caveat: "would-belong"
is an UPPER BOUND on loss (not all would beat the top-19 cut), so true selection-loss is somewhat <23% —
but a coup + deadly protests dropped are unambiguous misses. **The 65% FP costs real editorial quality;
fixing it (+~40% CLUSTER input, ~$0.05/run) is justified.**

## Decisions
- **SHIPPED** (local main, deploy-ready): /today (`c6edcf4`), exact-URL dedup (`a8d64ed`), same-day
  collapse (`b912405`), GN resolver + deadline + async prefetch (`17196d7`/`5d5b548`/`ada1077`).
- **Cross-day filter: FIX IS JUSTIFIED (follow-up, not in the deploy).** The counterfactual settles it.
  Simplest fix: raise `DEDUP_SIMILARITY_THRESHOLD` 0.35 → ~0.80 (high-precision near-verbatim backstop;
  recovers the wrongly-dropped stories; accept the modest CLUSTER cost, now justified). More surgical
  option: entity/primary_event matching post-cluster. Deploy the shipped work first, then do this.
- **GN URL resolver**: spike done — batchexecute decode works 98.4% on real Reuters/Nikkei URLs,
  stdlib-only ~30 lines, best-effort + canary. Ready to build on Sean's go-ahead (not built — he asked
  to spike, not build).
- AFP requote: we don't fetch AFP (paid) → link the outlet we read; wire-first only fires when the wire
  (Reuters) is in our set. Wire-first-by-timestamp REFUTED → canonical by source identity.
