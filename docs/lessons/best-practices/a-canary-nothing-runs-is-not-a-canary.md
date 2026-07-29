---
title: A canary nothing runs is not a canary; put the detector in the path that always executes
date: 2026-07-27
category: best-practices
module: gnews, digest, ci
problem_type: best_practice
severity: high
applies_when:
  - Adding a workaround that depends on an undocumented or third-party contract
  - Writing a test that is skipped by default (network, live API, env-gated)
  - Reviewing a "best-effort" path whose failure returns None and carries on
  - About to log a success count with `if count:`
tags: [canary, silent-failure, observability, gnews, skipif, best-effort, degradation]
---

`gnews.py` shipped a hand-rolled Google News decode with a documented safety net:

> "``tests/test_gnews.py`` ships a network-marked canary so a break surfaces loudly instead of
> degrading silently."

The canary existed. It was `@pytest.mark.skipif(not os.environ.get("GNEWS_LIVE"), ...)`, and
**nothing in the repo ever set `GNEWS_LIVE`** — not `make ci`, not `bin/ci`, not any schedule.
Google changed the RPC within about a day of the feature shipping. It then resolved **zero**
links across **25 consecutive production digests**, and every reader clicking a Reuters citation
got a Google News interstitial instead of the article. Nobody noticed for 25 days.

Two independent failures had to line up, and both are common:

## 1. The test had no trigger

A skipped test reports as "skipped", which reads like "fine" in a green run. Worse, the
docstring described the gate as a pytest marker (`-m gnews_live`) that did not exist, so anyone
trying to run it deliberately would have failed to.

If a check only runs when a human remembers, it is documentation, not a canary.

## 2. The caller logged only the success case

```python
if upgraded:
    logger.info("gnews: upgraded %d Google-News links", upgraded)
```

Total failure is `upgraded == 0`, so **the worse the outcome, the quieter the log.** Partial
failure was visible; complete failure was invisible. This is the same shape as the
`ZERO_*` invariants in `run_health.py` — the conditions worth alerting on are counts hitting
zero, which is exactly what a truthiness guard suppresses.

## The rule

**Put the break detector in the code path that runs every time, and make total failure the
loudest case, not the quietest.**

The replacement is a per-run tally in the module (`resolution_stats()` → `(attempted,
succeeded)`) that the caller checks unconditionally:

```python
attempted, succeeded = gnews.resolution_stats()
if attempted and not succeeded:
    logger.warning("gnews: resolved 0 of %d links -- the contract has probably moved again", attempted)
```

That cannot be skipped, needs no env var, and would have fired on day one instead of day 25.

## Checklist for a third-party/undocumented contract

- Instrument the **production** path with attempted/succeeded counts; alert on `attempted > 0 and
  succeeded == 0`. A rate near zero is a contract change; a rate merely down is throttling.
- Never write `if success_count:` around the only log line. Log the ratio, or branch so zero
  gets a `warning`.
- Keep a live test as a *manual reproduction tool*, but do not count it as the detector.
- Prefer a maintained library over hand-rolling the contract — but verify the maintenance is
  real before leaning on it (`still_active` reported googlenewsdecoder's last commit as 15
  months old, which is an argument for the in-process canary, not against the library).
- Ask directly: *if this silently returned nothing forever, what in the system would say so?*
  If the answer is a test that is skipped by default, there is no answer.

Related: [[an-unfiltered-sibling-tells-generator-from-validator]],
[[a-per-source-metric-measures-feed-configuration-until-proven-otherwise]].
