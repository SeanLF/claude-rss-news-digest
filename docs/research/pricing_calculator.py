"""Reference pricing calculator for the news-digest SaaS pilot.

This is a planning aid, not production code. It encodes the cost model from
docs/research/cost-model.md and the France micro-entrepreneur tax math
(BNC regime, 2025-2026 rates) so we can sanity-check tenant pricing before
quoting Danny, Hazem, or any future pilot subscriber.

Usage:
    from pricing_calculator import VerticalInput, price_vertical

    danny = VerticalInput(
        name="EU regulation",
        sources=20,
        items_per_day=80,
        cadence="daily",
        subscribers=1,
        shared=True,
        target_gross_margin=0.60,
    )
    quote = price_vertical(danny)
    print(quote.summary())

All money figures are EUR unless suffixed _usd. USD->EUR conversion is a
fixed assumption (see USD_EUR) -- update if the rate moves materially.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# ---- assumptions ----------------------------------------------------------

USD_EUR = 0.92  # EUR per USD, April 2026 ballpark; refresh as needed.

# Cadence -> runs per month (30.4 days/mo basis).
CADENCE_RUNS_PER_MONTH = {
    "daily": 30.4,
    "3x_weekly": 13.0,
    "weekly": 4.33,
    "biweekly": 2.17,
}

# Per-run cost baseline from cost-model.md: mean $4.97 across 18 production
# runs at ~617 articles/day, all Sonnet. We model cost as a small fixed
# overhead plus a per-article component so smaller verticals scale down.
RUN_FIXED_USD = 1.20         # dispatcher + select + write + recap floor
RUN_PER_ARTICLE_USD = 0.0061  # CLUSTER+FACT-CHECK scale-with-volume share
HAIKU_SWAP_DISCOUNT = 0.15   # planned RECAP+COHERENCE -> Haiku saves ~15%

# Resend pricing.
RESEND_FREE_TIER_CONTACTS = 1000     # Pro Marketing tier kicks in beyond this
RESEND_PRO_MONTHLY_USD = 40.0
RESEND_PER_EMAIL_USD = 0.001         # transactional overage estimate

# Hosting + infra.
HETZNER_MONTHLY_EUR = 4.50           # CX23
INFRA_OVERHEAD_MONTHLY_EUR = 8.00    # SSL, DNS, monitoring, backups
DEFAULT_VERTICALS_SHARING_HOST = 4   # amortise across N tenants on one box

# Operator time. Visible on purpose -- we want this to be a decision, not a
# hidden subsidy. Default to a token amount per vertical per month.
DEFAULT_OPERATOR_HOURS_PER_MONTH = 2.0
DEFAULT_OPERATOR_HOURLY_EUR = 75.0

# Stripe EU.
STRIPE_PERCENT = 0.029
STRIPE_FIXED_EUR = 0.25

# France micro-entrepreneur (auto-entrepreneur) BNC regime, 2025-2026.
# Sources: URSSAF, impots.gouv.fr, service-public.fr.
TVA_FRANCHISE_THRESHOLD_EUR = 36_800   # services threshold, 2025
TVA_RATE = 0.20                        # standard French TVA
MICRO_BNC_SOCIAL_RATE = 0.231          # 23.1% URSSAF cotisations on BNC libérales (PLNR), 2025
MICRO_BNC_CFP_RATE = 0.002             # contribution formation pro
MICRO_VFL_RATE = 0.022                 # versement libératoire IR for BNC, if eligible
MICRO_BNC_ABATTEMENT = 0.34            # standard 34% abattement if not on VFL


# ---- inputs and outputs ---------------------------------------------------

Cadence = Literal["daily", "3x_weekly", "weekly", "biweekly"]


@dataclass
class VerticalInput:
    name: str
    sources: int
    items_per_day: int
    cadence: Cadence = "daily"
    subscribers: int = 1
    shared: bool = True               # True = one run feeds N subscribers
    target_gross_margin: float = 0.60
    haiku_swap: bool = True           # assume the planned cheap-model swap
    operator_hours_per_month: float = DEFAULT_OPERATOR_HOURS_PER_MONTH
    operator_hourly_eur: float = DEFAULT_OPERATOR_HOURLY_EUR
    verticals_sharing_host: int = DEFAULT_VERTICALS_SHARING_HOST
    above_tva_threshold: bool = False  # set True once annual revenue clears it
    use_versement_liberatoire: bool = True
    notes: str = ""


@dataclass
class CostBreakdown:
    runs_per_month: float
    items_per_run: float
    api_per_run_usd: float
    api_monthly_usd: float
    api_monthly_eur: float
    resend_monthly_eur: float
    hosting_monthly_eur: float
    infra_overhead_monthly_eur: float
    operator_monthly_eur: float
    cogs_monthly_eur: float            # excludes operator time (true marginal cost)
    fully_loaded_monthly_eur: float    # includes operator time


@dataclass
class TaxBreakdown:
    gross_revenue_eur: float
    tva_collected_eur: float           # what customer pays on top, if applicable
    revenue_ex_tva_eur: float
    social_charges_eur: float
    income_tax_eur: float
    net_take_home_eur: float


@dataclass
class Quote:
    vertical: VerticalInput
    cost: CostBreakdown
    suggested_price_eur_monthly: float          # at target margin, COGS basis
    fully_loaded_price_eur_monthly: float       # at target margin, includes operator
    breakeven_subscribers_at_price: dict[float, float]
    tax: TaxBreakdown
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        c = self.cost
        t = self.tax
        v = self.vertical
        lines = [
            f"=== {v.name} ===",
            f"  cadence: {v.cadence}, {v.sources} sources, ~{v.items_per_day} items/day",
            f"  shared={v.shared}, subscribers={v.subscribers}, target GM={v.target_gross_margin:.0%}",
            "",
            f"  runs/month:        {c.runs_per_month:.1f}",
            f"  items/run:         {c.items_per_run:.0f}",
            f"  API per run:       ${c.api_per_run_usd:.2f}",
            f"  API monthly:       EUR {c.api_monthly_eur:.2f} (${c.api_monthly_usd:.2f})",
            f"  Resend:            EUR {c.resend_monthly_eur:.2f}",
            f"  Hosting (share):   EUR {c.hosting_monthly_eur:.2f}",
            f"  Infra overhead:    EUR {c.infra_overhead_monthly_eur:.2f}",
            f"  Operator time:     EUR {c.operator_monthly_eur:.2f}  ({v.operator_hours_per_month}h x EUR {v.operator_hourly_eur})",
            f"  COGS (marginal):   EUR {c.cogs_monthly_eur:.2f}",
            f"  Fully loaded:      EUR {c.fully_loaded_monthly_eur:.2f}",
            "",
            f"  Suggested price (COGS, {v.target_gross_margin:.0%} GM):  EUR {self.suggested_price_eur_monthly:.2f}/mo",
            f"  Suggested price (fully loaded):           EUR {self.fully_loaded_price_eur_monthly:.2f}/mo",
            "",
            "  Break-even subscribers at price points (COGS basis):",
        ]
        for p, n in self.breakeven_subscribers_at_price.items():
            lines.append(f"    EUR {p:>5.0f}/mo -> {n:.1f} subs")
        lines += [
            "",
            "  Tax pass (assuming pricing meets demand):",
            f"    gross revenue:   EUR {t.gross_revenue_eur:.2f}/mo",
            f"    TVA collected:   EUR {t.tva_collected_eur:.2f}/mo",
            f"    social charges:  EUR {t.social_charges_eur:.2f}/mo",
            f"    income tax:      EUR {t.income_tax_eur:.2f}/mo",
            f"    net take-home:   EUR {t.net_take_home_eur:.2f}/mo",
        ]
        if self.warnings:
            lines.append("")
            lines.append("  Warnings:")
            for w in self.warnings:
                lines.append(f"    - {w}")
        return "\n".join(lines)


# ---- core calculation -----------------------------------------------------

def estimate_api_cost_per_run_usd(items_per_run: int, haiku_swap: bool = True) -> float:
    """Approximate API-equivalent cost for one pipeline run.

    Linear-with-volume model anchored to cost-model.md observations:
        - At 617 articles, observed mean $4.97/run.
        - Fixed floor ~$1.20 covers the per-call overhead of dispatcher,
          select, write, recap regardless of corpus size.
        - CLUSTER + FACT-CHECK scale roughly with article count.

    Real-world variance is high (Section 7 of cost-model.md); treat this as
    a midpoint estimate, not a guarantee.
    """
    raw = RUN_FIXED_USD + RUN_PER_ARTICLE_USD * items_per_run
    if haiku_swap:
        raw *= (1 - HAIKU_SWAP_DISCOUNT)
    return raw


def compute_costs(v: VerticalInput) -> CostBreakdown:
    runs_per_month = CADENCE_RUNS_PER_MONTH[v.cadence]
    days_between_runs = 30.4 / runs_per_month
    items_per_run = v.items_per_day * days_between_runs

    api_per_run_usd = estimate_api_cost_per_run_usd(int(items_per_run), v.haiku_swap)
    runs_billed = runs_per_month if v.shared else runs_per_month * v.subscribers
    api_monthly_usd = api_per_run_usd * runs_billed
    api_monthly_eur = api_monthly_usd * USD_EUR

    emails_per_month = runs_per_month * v.subscribers
    if v.subscribers <= RESEND_FREE_TIER_CONTACTS and emails_per_month <= 3000:
        resend_monthly_eur = 0.0
    else:
        # Pro Marketing flat fee, ignore overage at pilot scale.
        resend_monthly_eur = RESEND_PRO_MONTHLY_USD * USD_EUR

    hosting_monthly_eur = HETZNER_MONTHLY_EUR / max(v.verticals_sharing_host, 1)
    infra_overhead_monthly_eur = INFRA_OVERHEAD_MONTHLY_EUR / max(v.verticals_sharing_host, 1)
    operator_monthly_eur = v.operator_hours_per_month * v.operator_hourly_eur

    cogs = (
        api_monthly_eur
        + resend_monthly_eur
        + hosting_monthly_eur
        + infra_overhead_monthly_eur
    )
    fully_loaded = cogs + operator_monthly_eur

    return CostBreakdown(
        runs_per_month=runs_per_month,
        items_per_run=items_per_run,
        api_per_run_usd=api_per_run_usd,
        api_monthly_usd=api_monthly_usd,
        api_monthly_eur=api_monthly_eur,
        resend_monthly_eur=resend_monthly_eur,
        hosting_monthly_eur=hosting_monthly_eur,
        infra_overhead_monthly_eur=infra_overhead_monthly_eur,
        operator_monthly_eur=operator_monthly_eur,
        cogs_monthly_eur=cogs,
        fully_loaded_monthly_eur=fully_loaded,
    )


def price_at_margin(cost_eur: float, target_gm: float) -> float:
    """Price needed so that (price - stripe_fee - cost) / price >= target_gm.

    Solves for price:  price * (1 - stripe%) - stripe_fixed - cost = price * gm
    -> price * (1 - stripe% - gm) = cost + stripe_fixed
    """
    denom = 1 - STRIPE_PERCENT - target_gm
    if denom <= 0:
        # Target margin not achievable at any finite price given Stripe take.
        return float("inf")
    return (cost_eur + STRIPE_FIXED_EUR) / denom


def compute_taxes(monthly_revenue_ex_tva: float, v: VerticalInput) -> TaxBreakdown:
    """Apply French micro-entrepreneur BNC math to a monthly revenue figure.

    Caveats:
      - Assumes annual revenue stays under the micro-BNC ceiling
        (EUR 77,700 for services, 2025).
      - VFL eligibility requires prior-year reference fiscal income below a
        threshold (EUR 27,478 per part, 2025). We assume eligible at pilot scale.
      - Reverse charge (B2B intra-EU) means TVA "collected" is zero from the
        customer's invoice but the customer self-accounts. We model that the
        operator does NOT remit TVA on those flows.
    """
    annual_revenue = monthly_revenue_ex_tva * 12
    tva_applies = v.above_tva_threshold or annual_revenue > TVA_FRANCHISE_THRESHOLD_EUR

    if tva_applies:
        tva_collected = monthly_revenue_ex_tva * TVA_RATE
    else:
        tva_collected = 0.0

    revenue_ex_tva = monthly_revenue_ex_tva  # by construction

    social = revenue_ex_tva * (MICRO_BNC_SOCIAL_RATE + MICRO_BNC_CFP_RATE)

    if v.use_versement_liberatoire:
        income_tax = revenue_ex_tva * MICRO_VFL_RATE
    else:
        # Standard BNC: 34% abattement, then progressive IR.
        # Pilot-scale: assume marginal rate ~11% (first bracket above the
        # decote). We use a simple 11% on the post-abattement base.
        taxable = revenue_ex_tva * (1 - MICRO_BNC_ABATTEMENT)
        income_tax = taxable * 0.11

    net_take_home = revenue_ex_tva - social - income_tax

    return TaxBreakdown(
        gross_revenue_eur=monthly_revenue_ex_tva + tva_collected,
        tva_collected_eur=tva_collected,
        revenue_ex_tva_eur=revenue_ex_tva,
        social_charges_eur=social,
        income_tax_eur=income_tax,
        net_take_home_eur=net_take_home,
    )


def price_vertical(v: VerticalInput) -> Quote:
    cost = compute_costs(v)
    warnings: list[str] = []

    # Suggested price: price one subscription such that, at the configured
    # subscriber count, gross margin lands at the target. For shared digests
    # COGS is fixed and revenue scales with subs; for personalised, COGS
    # already scales with subs in compute_costs.
    if v.shared:
        cogs_per_sub = cost.cogs_monthly_eur / max(v.subscribers, 1)
        loaded_per_sub = cost.fully_loaded_monthly_eur / max(v.subscribers, 1)
    else:
        cogs_per_sub = cost.cogs_monthly_eur / max(v.subscribers, 1)
        loaded_per_sub = cost.fully_loaded_monthly_eur / max(v.subscribers, 1)

    suggested = price_at_margin(cogs_per_sub, v.target_gross_margin)
    fully_loaded_price = price_at_margin(loaded_per_sub, v.target_gross_margin)

    # Break-even subscriber counts at common price points (COGS basis).
    breakeven = {}
    for price in (10.0, 20.0, 50.0, 100.0):
        # subs * (price * (1-stripe%) - stripe_fixed) >= cogs
        per_sub_net = price * (1 - STRIPE_PERCENT) - STRIPE_FIXED_EUR
        if v.shared:
            cogs_to_cover = cost.cogs_monthly_eur
            if per_sub_net > 0:
                breakeven[price] = cogs_to_cover / per_sub_net
            else:
                breakeven[price] = float("inf")
        else:
            # Per-tenant COGS; break-even is "does one customer cover their own run?"
            breakeven[price] = 1.0 if per_sub_net >= cogs_per_sub else float("inf")

    # Tax pass uses the suggested price * subscribers as monthly revenue.
    monthly_revenue = suggested * v.subscribers
    tax = compute_taxes(monthly_revenue, v)

    if not v.shared and v.subscribers > 1:
        warnings.append(
            "Personalised mode multiplies API cost by subscriber count; "
            "consumer pricing is rarely viable here."
        )
    if cost.cogs_monthly_eur > suggested * v.subscribers * 0.5:
        warnings.append(
            "COGS exceeds 50% of suggested revenue -- gross margin will be tight."
        )
    if v.subscribers == 1 and v.shared:
        warnings.append(
            "Single subscriber on shared mode: this is functionally personalised "
            "pricing. The shared-mode advantage only materialises with N>1."
        )

    return Quote(
        vertical=v,
        cost=cost,
        suggested_price_eur_monthly=suggested,
        fully_loaded_price_eur_monthly=fully_loaded_price,
        breakeven_subscribers_at_price=breakeven,
        tax=tax,
        warnings=warnings,
    )


# ---- worked examples ------------------------------------------------------

def example_verticals() -> list[VerticalInput]:
    return [
        VerticalInput(
            name="Danny -- EU regulation (daily, solo)",
            sources=20, items_per_day=80, cadence="daily",
            subscribers=1, shared=True,
            target_gross_margin=0.60,
            notes="EU AI/policy lawyer; B2B intra-EU reverse charge likely applies",
        ),
        VerticalInput(
            name="Hazem -- B2B sales / EMEA startups (weekly, solo)",
            sources=15, items_per_day=50, cadence="weekly",
            subscribers=1, shared=True,
            target_gross_margin=0.60,
        ),
        VerticalInput(
            name="Existing news digest (daily, ~30 sources)",
            sources=30, items_per_day=150, cadence="daily",
            subscribers=1, shared=True,
            target_gross_margin=0.60,
        ),
    ]


if __name__ == "__main__":
    for v in example_verticals():
        q = price_vertical(v)
        print(q.summary())
        print()
