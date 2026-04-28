# Vertical Pricing Model -- Pilot Pricing Calculator

**Date:** April 2026
**Operator location:** Paris, France (CET) -- micro-entrepreneur BNC regime assumed
**Companion code:** [`pricing_calculator.py`](pricing_calculator.py)
**Grounded in:** [`cost-model.md`](cost-model.md), [`saas-economics.md`](saas-economics.md), [`verticals.md`](verticals.md)

---

## Executive summary

Two pilot customers are being scoped: Danny Dib (EU policy and AI regulation lawyer, daily cadence) and Hazem Hamrouni (B2B sales, talent mobility, EMEA startup ecosystem, weekly cadence). Both are friend-network leads, not enterprise procurement, so the question is "what should they actually pay" rather than "what would a sales team quote." The pricing calculator below puts hard numbers on both sides of that decision.

The headline numbers, at the planned Haiku swap for RECAP/COHERENCE and a 60% target gross margin on cost of goods sold (COGS, excluding operator time):

| Vertical | Cadence | COGS / month | Suggested price (60% GM) | Suggested price (80% GM) | Friend price honest floor |
|---|---|---:|---:|---:|---:|
| Danny -- EU regulation | daily | EUR 43 | EUR 117 | EUR 254 | ~EUR 50 |
| Hazem -- B2B sales | weekly | EUR 14 | EUR 40 | EUR 86 | ~EUR 20 |
| Existing news digest | daily | EUR 53 | EUR 145 | EUR 314 | -- (operator-only) |

The "friend price honest floor" is what a single subscriber would need to pay so the operator nets at least minimum-wage equivalent for the time spent, after French social charges and tax, treating operator hours as an expense rather than free labour. It is meaningfully higher than the COGS-based floor.

The most important structural finding: at one subscriber per vertical, "shared" mode is functionally personalised pricing. The favourable shared-digest economics described in [`saas-economics.md`](saas-economics.md) only kick in once the same digest serves a real audience (10+ subs), at which point per-subscriber pricing collapses to the single-digit-EUR range. For the pilot specifically, this means quoting Danny and Hazem as if each has their own bespoke pipeline -- because they do.

The second important finding: French micro-entrepreneur tax math takes roughly 25-26% off gross revenue (social charges + versement libératoire IR) before the operator sees a euro. That sets a real floor below which "doing this for money" doesn't make sense versus "doing this as a favour with hosting comped." TVA does not apply at pilot scale (well under the EUR 36,800 services threshold), and B2B intra-EU reverse charge would apply to Danny's firm if it's VAT-registered in Sweden.

---

## Cost components

### LLM API (the dominant variable cost)

[`cost-model.md`](cost-model.md) gives a mean of **$4.97 per run** at ~617 articles/day, all Sonnet across all subagents. The cost decomposes into a fixed floor (dispatcher + select + write + recap, roughly $1.20) and a CLUSTER + FACT-CHECK component that scales roughly linearly with article volume at about $0.0061 per article. The calculator models:

```
api_per_run_usd = (1.20 + 0.0061 * items_per_run) * (1 - 0.15 if haiku_swap else 1.0)
```

The 15% Haiku-swap discount is the planned but not-yet-shipped change for RECAP and COHERENCE, both of which are currently running on Sonnet according to `run_usage` data. This is the easiest cost reduction available and is treated as default-on in pilot pricing.

For the three example verticals, the per-run API cost is **$1.43** (Danny, 80 articles/day, daily), **$2.84** (Hazem, weekly, so 7 days of items pile up to ~351/run), and **$1.80** (existing digest, 150 articles/day, daily). Hazem's weekly cadence is interesting: cost per run is higher than Danny's because more items accumulate, but only ~4.3 runs/month means total monthly API cost is the lowest of the three.

### Resend email

Free tier covers the pilot comfortably -- both Danny and Hazem have one subscriber each. The model only kicks in the EUR 37/mo Pro Marketing fee once a vertical exceeds 1,000 contacts or 3,000 monthly emails, whichever comes first. For pilot pricing this is zero.

### Hosting and infra overhead

Hetzner CX23 at EUR 4.50/mo plus EUR 8/mo notional for SSL, DNS, monitoring, backups, and a domain renewal allocation. Amortised across an assumed four verticals (existing digest + Danny + Hazem + headroom), each vertical absorbs about EUR 3.12/mo of fixed infrastructure. This is small enough that getting it precisely right doesn't matter; getting it onto the line item does, so the operator remembers it exists when comping a fourth vertical "for free."

### Operator time (visible, not hidden)

Default is 2 hours/month per vertical at EUR 75/hour. The hourly rate is on the low side for Paris freelance technical work but reflects the reality that "checking the digest looks fine" is not billable-grade time. The 2 hours covers monitoring, source tweaks, Claude prompt iteration, and occasional bug triage.

This line is shown separately from COGS deliberately. There are two valid framings:

1. **Marginal pricing** (COGS-only): treat operator time as already-paid sunk cost or as opportunity cost the operator chooses to absorb. Reasonable for friend pricing or when the work is genuinely interesting.
2. **Fully-loaded pricing** (COGS + operator): treat the time as expense. This is what a real business would do, and it's what makes the difference between "I'm running a tiny SaaS" and "I'm subsidising my friends with my evenings."

The calculator outputs both numbers so the decision is conscious, not accidental.

### Payment processing

Stripe EU at 2.9% + EUR 0.25 per transaction. The fixed component matters at low price points -- on a EUR 10 charge, Stripe takes EUR 0.54, or 5.4%, not 2.9%. The calculator builds Stripe into the price-at-margin solver so the suggested price already nets the target margin after fees.

---

## Tax components (France-specific)

The operator is registered (or registering) as a micro-entrepreneur in the BNC regime (bénéfices non commerciaux -- non-commercial profits, the bucket for professional services like consulting and software). Three tax flows apply:

### TVA (VAT) -- usually skipped at pilot scale

The "franchise en base de TVA" threshold for services in 2025 is **EUR 36,800/year**. Below that, no TVA is charged to customers and none is remitted -- but no input TVA can be reclaimed either. The pilot revenue scenarios here (single subscribers at EUR 40-150/mo, so EUR 480-1800/year combined) are an order of magnitude below threshold. The calculator defaults to no TVA.

If a future scale-up crosses the threshold, the standard rate is **20%**. For B2B customers in other EU countries that are VAT-registered (likely the case for Danny's law firm if it's a Swedish corporate entity), the **intra-EU reverse charge** applies: the operator invoices net of TVA with a "TVA non applicable, art. 259-1 du CGI" mention, and the customer self-accounts for VAT under their national rules. For a B2C EU customer above threshold, French TVA at 20% applies and is remitted to the French treasury via the One Stop Shop scheme.

**Practical note:** verify Danny's firm has a Swedish VAT number before invoicing. If they're a sole-practitioner not VAT-registered, the reverse charge does not apply and -- if the operator is below the franchise threshold -- TVA simply isn't charged at all.

### Social charges -- the real bite

Micro-entrepreneur cotisations sociales for BNC libérales (PLNR -- professions libérales non réglementées) in 2025 are **23.1% of gross revenue**, plus a small 0.2% contribution to professional training (CFP). This is paid quarterly to URSSAF on declared revenue, and it's calculated on revenue, not profit -- which is the structural disadvantage of the micro regime.

The 23.1% rate is the post-2024-reform figure. There was a transition schedule that landed at 23.1% in 2025 and is scheduled to rise to 24.6% in 2026 (verify against URSSAF before final pricing). The calculator uses 23.1% as the steady-state assumption; if the 2026 hike has fully landed, add ~1.5 percentage points to the social charges line and reduce net take-home accordingly.

### Income tax -- two paths

**Versement libératoire de l'impôt sur le revenu (VFL):** if the operator's reference fiscal income from the prior year is below ~EUR 27,478 per fiscal "part" (2025 figure), they can elect VFL and pay income tax as a flat **2.2% of BNC revenue** alongside the URSSAF declaration. For pilot-scale revenue and a likely modest prior-year fiscal income, this is the right choice and the calculator defaults to it.

**Standard BNC regime:** if VFL isn't available, the income gets a 34% abattement (deemed expenses) and the remaining 66% is added to global household income and taxed at the progressive IR scale. At pilot scale and likely first-bracket marginal rate (~11%), this works out modestly higher than VFL -- about EUR 8.50/mo vs EUR 2.60/mo on Danny's hypothetical EUR 117/mo revenue. Not negligible but not catastrophic.

### Net take-home estimate

For Danny at the suggested EUR 117.26/mo (60% GM on COGS, VFL elected):

| Line | Amount (EUR/mo) |
|---|---:|
| Gross revenue (no TVA, below threshold) | 117.26 |
| Social charges (23.3% incl. CFP) | 27.32 |
| Income tax (VFL 2.2%) | 2.58 |
| **Net to operator** | **87.36** |

That's a 25.5% combined tax-and-social drag. Worth mentally pricing in: the 60%-GM number is gross margin to the *business*, not take-home to the *person*. A "60% margin" quote at EUR 117/mo is really EUR 87 in the operator's pocket, before subtracting the operator-time line item that wasn't counted in COGS to begin with. If you do count operator time honestly, the EUR 117 quote is loss-making at 2 hours/month.

---

## Worked examples: three verticals

All numbers from the calculator with default assumptions: Haiku swap on for RECAP/COHERENCE, four verticals sharing host costs, 2 hours/month operator time at EUR 75/hour, VFL elected, below TVA threshold, 60% target gross margin.

### Comparison table

| Metric | Danny (EU reg, daily) | Hazem (B2B sales, weekly) | Existing digest (daily) |
|---|---:|---:|---:|
| Sources | 20 | 15 | 30 |
| Items/day | 80 | 50 | 150 |
| Runs/month | 30.4 | 4.3 | 30.4 |
| Items/run | 80 | 351 | 150 |
| API per run (USD) | $1.43 | $2.84 | $1.80 |
| API monthly (EUR) | 40.13 | 11.31 | 50.28 |
| Resend (EUR) | 0.00 | 0.00 | 0.00 |
| Hosting share (EUR) | 1.12 | 1.12 | 1.12 |
| Infra overhead (EUR) | 2.00 | 2.00 | 2.00 |
| **COGS (marginal, EUR)** | **43.25** | **14.44** | **53.40** |
| Operator time (EUR) | 150.00 | 150.00 | 150.00 |
| Fully loaded (EUR) | 193.25 | 164.44 | 203.40 |
| Suggested price (60% GM, COGS) | **117.26** | **39.59** | **144.62** |
| Suggested price (80% GM, COGS) | 254.41 | 85.90 | 313.77 |
| Suggested price (60% GM, fully loaded) | 521.57 | 443.90 | 548.93 |
| Net take-home @ 60% GM, VFL (EUR) | 87.36 | 29.50 | 107.74 |

### Break-even subscriber counts (shared mode, COGS basis)

If the same digest is sold to N subscribers, how many subscribers are needed at each price point to cover COGS?

| Price/sub/mo | Danny (daily) | Hazem (weekly) | Existing (daily) |
|---:|---:|---:|---:|
| EUR 10 | 4.6 | 1.5 | 5.6 |
| EUR 20 | 2.3 | 0.8 | 2.8 |
| EUR 50 | 0.9 | 0.3 | 1.1 |
| EUR 100 | 0.4 | 0.1 | 0.6 |

These are revealing. At EUR 10/mo a daily vertical needs ~5 subscribers just to cover COGS (not operator time). The Mailbrew/Meco consumer price band ($4-8/mo) doesn't survive contact with the cost model unless audiences scale into the dozens. The numbers also confirm what `saas-economics.md` already concluded: shared digests at consumer pricing need real audiences.

---

## Sensitivity analysis

### What if Danny's article volume doubles?

Going from 80 to 160 items/day (a real possibility once EUR-Lex, Politico Europe, AI Act trackers, and a few national regulators are added):

- API per run: $1.43 -> $1.85 (+30%)
- API monthly: EUR 40.13 -> EUR 51.73 (+29%)
- Suggested 60% GM price: EUR 117 -> EUR 149 (+27%)

Cost is sublinear with article count because the fixed per-run floor doesn't move and the variance in CLUSTER cost-per-article (see [`cost-model.md`](cost-model.md) Section 7) means the linear model is itself an upper-ish midpoint. A doubling of sources is comfortably absorbed at the suggested price band; quadrupling to 320 items/day starts to hurt and would push cost into the EUR 70-80/mo range.

### What if Hazem grows to a small team of 5?

Same digest, five subscribers, shared mode:

- COGS unchanged at EUR 14.44/mo (one run still serves the team)
- Suggested price drops to **EUR 8.46/sub/mo** at 60% GM
- Total revenue: EUR 42.29/mo, net take-home (after social + VFL) ~EUR 31.50/mo

This is the shared-digest economics doing the work. Five-seat-team pricing at EUR 40-50/mo total feels honest for the value delivered and is well within "expense it" range for a B2B sales team. The unit economics finally start looking like a SaaS rather than a favour.

### What if the operator wants 80% gross margin instead of 60%?

For Danny: EUR 117 -> EUR 254/mo. For Hazem solo: EUR 40 -> EUR 86/mo. For the existing digest: EUR 145 -> EUR 314/mo.

80% gross margin is the traditional SaaS benchmark from [`saas-economics.md`](saas-economics.md), but it assumes a software business amortising fixed engineering across many customers. For a single-tenant pilot it pushes prices into territory that requires either (a) institutional buyers or (b) a story about why your service is differentiated enough to clear that price. Neither pilot tenant qualifies; 80% GM is the wrong target at this stage.

### What if the operator absorbs operator-time as opportunity cost?

This is the "I would be doing this anyway because it's interesting" framing. Then COGS-based pricing is honest and Danny at EUR 117/mo, Hazem at EUR 40/mo lands at the right gross margin without pretending the time isn't real. The trade-off: this only works if the operator genuinely doesn't resent the time later. The 2-hour estimate is also conservative -- the first 6 months will easily eat 5+ hours/vertical/month for prompt tuning and source iteration.

### What if TVA kicks in?

If pilot revenue grows past EUR 36,800/year (e.g., 5+ paying customers at EUR 600+/year average), the operator must register for TVA. For Danny at EUR 117/mo:

- New gross invoice (B2C in France): EUR 140.71, of which EUR 23.45 is TVA owed to the treasury
- Net to operator unchanged at EUR 87.36 if customers absorb the TVA increase
- If operator absorbs the TVA (price stays EUR 117 inclusive): net drops to EUR 72.85, a 16% haircut

For Danny's law firm specifically, if they're VAT-registered in Sweden, intra-EU reverse charge means the invoice stays at EUR 117, no TVA collected by the operator, no haircut. Most B2B EU pilots will hit this case and it's painless; B2C EU customers are the ones who feel the price increase.

---

## Pricing narrative for the pilot

The pilot has two tenants and a personal vertical. The right pricing question isn't "what would maximise revenue" -- there are no economies of scale to chase yet -- but "what price point makes both sides feel the deal is fair, sustainable, and honest about what's being exchanged."

### Danny -- EU regulation (daily)

WTP signals from the [`verticals.md`](verticals.md) policy/regulation section are strong. Politico Pro normalises EUR 8,000-10,000/yr enterprise pricing for EU regulatory tracking; Bloomberg Government is in similar territory. Danny is an individual lawyer (or part of a small firm), not a Politico Pro buyer, but the WTP ceiling exists and the comparison is real.

Three honest price points:

1. **Friend price: EUR 30-50/mo.** Below COGS+operator-time, but above pure COGS. Operator nets EUR 22-37/mo after social and VFL. This is "I'm doing this because we're friends and I want to see if it's useful, but I'm not paying out of pocket for your hosting." Reasonable as an opening 3-month pilot.
2. **Honest break-even with operator time: EUR 60-80/mo.** Covers COGS plus a token contribution to the 2 hours/month of attention. Operator nets ~EUR 45-60/mo. Below the 60% GM number but above the "this isn't worth doing" floor. This is the sustainable long-term price for a single subscriber.
3. **Market price: EUR 120-200/mo.** The 60% GM number through to a "this is a real product" 80% GM number. Defensible against Politico Pro comparison, especially if the digest is genuinely covering EU AI/regulatory ground that Politico Pro covers shallowly. Requires the product to feel polished -- not a friend's side project.

The flip from friend price to market price happens when (a) Danny is ready to pay rather than hint that he might, (b) the digest is operating reliably enough that the operator can honestly pitch it as a service rather than an experiment, and (c) the source list has been tuned for his actual workflow rather than a generic "EU regulation" assumption. Realistic timeline: 2-3 months in. Suggested move: start at EUR 30/mo for 3 months, then offer EUR 75/mo as the "this is now a real subscription" price with no apology.

### Hazem -- B2B sales / EMEA startups (weekly)

Weaker WTP signals than Danny. There's no obvious "Politico Pro of B2B sales intelligence" comparable -- the closest analogues are Lenny's Newsletter ($150-350/yr, but human-written and community-attached), Pavilion membership ($2,000+/yr but for the network, not the content), and a long tail of free Substacks. The vertical is also weekly, which means lower API cost but also lower psychological "this is part of my workflow" stickiness.

Three honest price points:

1. **Friend price: EUR 15-25/mo.** Above COGS, below the 60% GM number. Operator nets EUR 11-18/mo. Lowest sustainable price for a solo subscriber.
2. **Honest single-sub price: EUR 35-50/mo.** The 60% GM number through to a small operator-time contribution. Defensible if Hazem genuinely uses it weekly to source intel for B2B sales conversations, not just curiosity.
3. **Team pricing (where this gets interesting): EUR 8-15/sub/mo for a team of 5-10.** This is where the shared-digest economics actually do the work. EUR 50-150/mo total revenue from a Hazem-shaped buyer (sales team, EMEA startup) is plausibly an "expense it as part of competitive intel tooling" purchase.

The flip from friend price to market price for Hazem happens when there's a team to sell to, not just an individual to please. Suggested move: start at EUR 20/mo solo, with an explicit "if you want to add 2-3 of your team, the price is EUR 40 total" hook that nudges toward the right unit economics from the beginning.

### The honest "just to break even" floor

Treating operator time at EUR 0 (opportunity cost absorbed) and aiming at COGS recovery only:

- Danny: ~EUR 45/mo covers COGS + Stripe + ~5% buffer for cost variance
- Hazem: ~EUR 16/mo covers the same
- Existing digest: ~EUR 56/mo covers the same (relevant if the operator ever wants to charge themselves a notional rent for the personal vertical)

Below these numbers, the operator is paying out of pocket to host other people's digests. Above them, every additional EUR is contribution margin -- but until operator time is being meaningfully compensated, "profit" is technically wages-in-arrears.

### Pricing-flow recommendation for the pilot

1. **Start both pilots free for 30 days.** Removes friction, sets expectation that there's a paid version coming.
2. **Quote EUR 30/mo (Danny) and EUR 20/mo (Hazem) as the post-trial price.** Below the calculator's market suggestion but above pure COGS for both, and signals "I'm not free, but I'm not gouging."
3. **Re-quote at month 3.** If the digest is actually being read and used, raise to EUR 60-75/mo (Danny) and EUR 30-40/mo (Hazem). If not, have an honest conversation about whether to continue.
4. **Hazem-specific: explicitly offer team pricing.** "Add 2 teammates for EUR 40 total" is structurally better economics than "add 2 teammates for EUR 60 total" because it gets to the shared-digest unit economics faster.
5. **Don't apologise for the price.** The calculator shows the COGS, the tax pass shows the take-home -- there's no slack in these numbers to feel guilty about. EUR 75/mo is actually a friend discount for a daily AI-curated digest of EU regulation; the same product priced as Politico Pro is 10x more.

---

## Calculator usage and limitations

The Python implementation in [`pricing_calculator.py`](pricing_calculator.py) takes a `VerticalInput` dataclass and returns a `Quote` with full cost breakdown, suggested prices at the target gross margin, break-even subscriber counts at common price points, and a French-tax pass showing net take-home.

Key knobs that can be tweaked at quote time:

- `target_gross_margin` -- defaults to 0.60; pilot context can justify 0.40-0.50.
- `haiku_swap` -- defaults to True; flip to False if the swap hasn't shipped yet to get a more conservative API estimate.
- `operator_hours_per_month` and `operator_hourly_eur` -- defaults to 2 hours at EUR 75/hour; raise both for the first 3 months of any new vertical.
- `verticals_sharing_host` -- defaults to 4; reduce to 2-3 if the existing digest is the only other vertical actually running.
- `above_tva_threshold` -- defaults to False; set True once annualised revenue clears EUR 36,800.
- `use_versement_liberatoire` -- defaults to True; verify the operator's prior-year fiscal income qualifies.

Limitations and known gaps:

- API cost variance is real (10x range observed in production). The calculator uses a midpoint; budget for ~30% variance on the API line.
- The Haiku-swap discount is an estimate based on RECAP+COHERENCE token shares from `run_usage`. Once the swap ships, recalibrate against actual data.
- French tax rates are 2025 figures. The 2026 social-charges rate is scheduled to rise to ~24.6%; verify before final invoicing.
- The operator-hourly rate is a placeholder. Adjust to actual opportunity cost.
- No model of subscriber churn, CAC, or LTV. The pilot doesn't have enough scale for those numbers to matter; revisit when there are 5+ paying tenants.
- Stripe's per-charge fixed fee is modelled but not annual subscription discounts (Stripe drops per-transaction fees on annual billing). If pilots go annual, the calculator slightly under-prices.

The calculator is a sanity check, not a quote engine. For each pilot conversation, run it with the actual inputs, look at both the COGS and fully-loaded numbers, and pick a price that's honest about which framing is being applied.
