---
title: Write the cost equation before scoping an optimization, then check the change moves a term in it
date: 2026-07-22
category: best-practices
module: any
problem_type: best_practice
severity: high
applies_when:
  - Proposing a cost, latency, or token-usage optimization
  - About to scope engineering time against an alarming metric
  - Reasoning about "how many users/readers/callers touch this"
tags: [cost, measurement, scoping, tokens, egress, lever-test]
---

# Write the cost equation first, then check your change moves a term in it

## The rule

Before scoping any optimization, write the cost as an explicit equation, then
test whether the proposed change reduces one of its terms. If it does not, the
change saves nothing regardless of how much surface it touches. Discard it
before writing code.

The failure mode is reasoning about the wrong noun. "How many clients read this"
feels like it belongs in a bandwidth equation. It does not, if a cache sits in
front:

```
egress = origin-miss count x bytes per miss
```

Client count, reader count, and request volume are absent from the right-hand
side. The CDN absorbs them. Only two levers exist: the miss rate, and the
payload size.

## Why this matters here

This repo's cost equation is unusual and worth stating explicitly, because it
inverts the intuition most SaaS advice assumes:

```
monthly cost = (model spend per run x runs per month)      <- fixed
             + (subscribers x cost per email)              <- ~0
             + infrastructure                              <- fixed
```

The digest is computed **once** and broadcast. Subscribers are not on the
expensive side of the equation. At ~$2.50-3.00/day model spend (verified
2026-06-24) plus a CX23, the cost per subscriber per month is roughly $8.20 at
11 subscribers, $0.09 at 1,000, and $0.01 at 10,000.

Two consequences follow directly:

- **Cost work belongs in the pipeline, not in delivery.** CLUSTER is ~38% of
  run cost; a subscriber is a rounding error. Optimizing per-subscriber anything
  is optimizing a term that is already zero.
- **Growth does not threaten the budget.** Any argument of the form "we cannot
  afford more readers" is arithmetically false here and should be checked
  against this equation before it drives a decision.

## Measurement discipline

When you do measure, make the numbers comparable:

- Compare **counts, not bytes**, when payload sizes vary over the day.
- **Never measure adjacent to a deploy** -- a deploy flushes caches and distorts
  every rate.
- Use **two independent traffic proxies** and check they agree; a single proxy
  that gets removed or changes meaning will silently corrupt a trend.
- **Verify a probe is uncontaminated** before trusting it. Grep for other
  callers of the thing you are using as a clean meter.
- **Do not trust a naive analytic model of the miss rate -- measure it.** TTL
  arithmetic predicts miss rates badly; real request distribution dominates.

## Provenance

Generalised from the lever test in
[koala73/worldmonitor](https://github.com/koala73/worldmonitor)'s
`docs/lessons/best-practices/egress-cost-tracks-origin-miss-rate-not-client-count.md`,
where skipping this arithmetic pointed engineering-weeks of planned work at two
targets that netted to zero. The arithmetic took thirty minutes.
