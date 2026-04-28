# Executive Summary: Pipeline Generalisation & SaaS Viability
**Date:** 2026-04-03  
**Research basis:** 4 parallel subagent investigations (cost model, SaaS economics, verticals, architecture)  
**Full research:** `docs/research/` (cost-model.md, saas-economics.md, verticals.md, architecture-generalisation.md)

---

## What you have

A pipeline that costs **~$5/run** (range $2.55-$8.23 across 18 production runs), runs on Sonnet for all five subagents, and is deeply but not irreversibly coupled to one editorial taxonomy. The codebase is closer to generalisable than it looks -- three targeted layers need to change, and everything else is already content-agnostic.

---

## Cost model

**What drives cost:** CLUSTER at 51% (~$2.54/run). The other four subagents are ~$0.50 each. Cache efficiency is already maxed (79-97% cache-read rates) -- no optimisation there. The main risk is **CLUSTER's 10x cost variance**: same article count, costs ranging from $0.09 to $5.54, driven by how many conversation turns the agent needs, not article volume. Budget for the top of that range.

**Scaling levers in order of impact:**
1. **Weekly cadence**: cuts cost from $1,825/yr to $258/yr. 7x reduction. Works only if staleness is acceptable for the vertical.
2. **Haiku for RECAP + COHERENCE**: they're running Sonnet (memory note of Haiku is stale -- run_usage data shows Sonnet for all agents). Switching saves ~15% immediately, ~$0.75/run, essentially free.
3. **Reduce CLUSTER conversation turns**: single-pass read of all article files instead of iterative. Estimated 30-50% CLUSTER cost reduction if achievable. Medium effort.
4. **Article pre-filter**: tighter TF-IDF threshold before Claude sees anything. 20-30% CLUSTER cost reduction. Easier.

**Shared digest break-even** (API billing, one run serves N subscribers):

| Price/mo | Break-even subscribers |
|---|---|
| $5 | 31 |
| $10 | 16 |
| $20 | 8 |

After break-even, incremental margin is ~100% minus email sending costs.

---

## SaaS economics

The fundamental split is **shared vs. personalised**:

- **Personalised daily digest** at current costs = $120/mo AI per tenant -> $240/mo break-even at 50% margin. That's Feedly Enterprise pricing. Not a consumer product.
- **Shared digest** (N subscribers, one topic, one run) = Morning Brew model. At 100 subscribers x $10/mo, AI is 12% of revenue. 88% gross margin. This works.
- **Weekly personalised** = $17/mo AI cost per tenant -> break-even at $34/mo. Borderline viable for professionals who can expense it.

The Mailbrew multiple-acquisition carousel and Artifact's shutdown both point the same direction: B2C daily personalised digest economics are broken. What works is either shared editorial digest with a clear niche, or B2B pricing where the buyer is a company card.

One real risk: a German market study found **30-33% WTP reduction when AI involvement is disclosed**. Lead with the outcome (the digest) not the mechanism.

---

## Verticals

Three that stand out from the 11 assessed:

**1. EU Policy & Regulation** -- highest WTP in any category. Politico Pro charges ~EUR 10k/year. The EU-specific beat (AI Act, GDPR, DSA, DMA enforcement) is underserved at mid-market vs. US Congressional tracking. Official EUR-Lex, European Parliament, and European Commission RSS feeds are free and reliable. Content volume: 100-200 items/day for a focused domain. Fit: 4/5.

**2. Sustainability / ESG Consulting** -- CSRD alone brought 50,000+ firms into mandatory sustainability reporting scope. Corporate sustainability teams and boutique ESG consultancies pay $100-500/mo on a corporate card. No strong AI-curated digest. Regulatory synthesis across frameworks (CSRD, TCFD, GRI, SEC climate disclosure) is exactly where Claude adds value. Fit: 4/5.

**3. Mining / Resources Industry** -- most concrete near-term opportunity outside regulation. 10+ quality RSS feeds, 30-80 items/day, B2B audience comfortable paying $100-500/mo, no AI incumbent. Smaller audience but extremely loyal. Fit: 4/5.

**Skip**: developer tools (TLDR has locked the audience), sports (The Athletic has institutional coverage), economics (NBER/VoxEU free tier too strong).

---

## Architecture generalisation

**The hard blocker is `mcp_server.py`**: `SIGNALS_SCHEMA` hardcodes the five region keys as required JSON properties. Claude's output is rejected by schema validation if you change section names anywhere else.

**Five-step minimal change set:**

1. **Add `vertical.json`** -- tiers, sections, editorial rules, style block, source metadata fields (~50 lines, new file)
2. **Dynamic MCP schema** -- generate `SIGNALS_SCHEMA` and `SELECTIONS_SCHEMA` from vertical config (`mcp_server.py`, ~30 lines). **This is the blocker.**
3. **Jinja2 prompt templates** -- convert `select.md` and `write.md` to `.j2` with injectable blocks
4. **render.py reads from config** -- `REGION_CONFIG` and `REGION_ORDER` from vertical.json
5. **prepare.py source columns** -- use `source_metadata_fields` from config instead of hardcoded `bias`/`factuality`

**What stays fixed:** pipeline orchestration, RSS fetch, TF-IDF dedup, DB schema, CLUSTER/RECAP/COHERENCE agents, email delivery, usage tracking, Rust server.

**Migration:** Phase 0 = pure refactor (extract to config, no behaviour change) -> Phase 1 = dynamic MCP schema (one regression test) -> Phase 2 = second vertical under `--dry-run`.

---

## The thread

**Generalise the architecture regardless of whether SaaS happens.** The five-step refactor is low-risk, cleans up genuine technical debt, and is worth doing on its own.

**If pursuing a product**: shared digest for a professional niche (EU regulation is the best initial fit -- WTP proven, sources free, editorial task plays to Claude's strengths) is structurally sounder than personalised consumer. Weekly cadence changes unit economics materially.
