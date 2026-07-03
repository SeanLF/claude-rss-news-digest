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

## Decisions
- **BUILD same-day source-priority collapse** (render layer) — the original wildfire ask. Safe.
- **Cross-day filter**: interim threshold raise (harm reduction, reversible) → real fix is
  entity/`primary_event` matching riding on the extract→join substrate (needs pipeline reorder:
  extract before dedup, or move dedup post-cluster). The latter is the recommended follow-up
  project and the on-ramp to the persistent story-graph substrate.
- AFP requote: we don't fetch AFP (paid) → link the outlet we read; "prefer wire origin" only
  fires when the wire (Reuters) is in our source set. Wire-first-by-timestamp REFUTED (reposts
  sometimes carry earlier RSS `published` than the wire's feed entry) → canonical by source
  identity, not timestamp.
