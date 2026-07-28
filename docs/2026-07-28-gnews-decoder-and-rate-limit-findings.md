# Google News decoding: what broke, what the limits actually are

**2026-07-27 into 07-28.** Started as a review of the day's digest, became a root-cause of the
link resolver plus a measured account of Google's throttling. Several conclusions in here
replaced earlier ones from the same session; where that happened it is marked, because the
retracted versions were stated confidently and would otherwise be remembered.

## The resolver never worked, and it was our bug

`gnews.py` resolved **0 links in 25 consecutive digests** since shipping on 2026-07-03. Not a
Google change — a malformed request envelope:

```python
[[rpc]]      # what we shipped -> HTTP 400, every time
[[[rpc]]]    # correct         -> resolves
```

Three array levels, not two. The PoC measured 98.4% the day before and the shipped code was
never the code that was measured. With the one-character fix our original stdlib implementation
resolves **48/48**.

Two consequences worth keeping:

- **The canary that was supposed to catch this exists and never ran.** `test_gnews.py` has a
  live canary gated behind `GNEWS_LIVE=1`, which nothing sets — not CI, not a schedule. The
  docstring promised "a break surfaces loudly instead of degrading silently"; it degraded
  silently for 25 days. A canary with no trigger is not a canary.
- **`if upgraded:` meant total failure logged nothing.** The run that resolved zero links was
  the quietest run in the log. Total failure should be the loudest case.

### The published library already fixes it

Measured 2026-07-28, same tokens, same order, one container, three arms
(`scratch/gnews-rate-lab/compare/run`):

| arm | result |
|---|---|
| fork branch + fresh tokens | 6/6 |
| fork branch + stored tokens | 6/6 |
| **PyPI `googlenewsdecoder` 0.1.7 + fresh tokens** | **6/6** |

So the pipeline does not need the fork to be correct. `pyproject.toml` currently depends on
the plain PyPI name, which is the version measured above. The fork buys hardening — bounded
decompression, tag-keyed batch mapping, a transport seam — not basic function. Pinning it
would mean carrying a VCS dependency for benefits production does not yet use.

## Google's throttling, measured

Method: a container per VPN exit, all in one wall-clock window, live tokens from an RSS search,
tunnel egress verified against the host address. Harness in `scratch/gnews-rate-lab/`.

### It is a per-IP budget, not a rate limit

Every address runs clean until its budget is gone, then refuses. Rate is irrelevant — 10 req/s
and 0.5 req/s both ran clean to the same counts.

| population | budget observed |
|---|---|
| residential (Canadian ISP) | ~80, then immediate 429 |
| Proton VPN exits (n=9, one window) | 22–43, median 27 |
| one unusually fresh Proton exit | 96 and 70 clean |

This is why an adaptive rate limiter found no threshold: there is no rate to converge on.

### Only the article GETs are counted

Three conditions across nine exits, round-robin so no condition sits on one address:

| shape | GETs before refusal | POSTs made |
|---|---|---|
| GET only | 22, 30, 31 | 0 |
| GET + POST per article | 18 | 18 |
| GET + one batched POST | 25, 25, 31 | 1 each |

Mean 27.7 for pure GET, 27.0 for batch. **Every refusal was on a GET**, including the run that
had just made 18 successful POSTs. POSTs are free.

**This overturns the batching argument made earlier in the same session.** Batching collapses
48 POSTs into one, and POSTs were never the constrained resource. We make 48 GETs either way.
Batching is still worth having for round trips and wall-clock; it does not reduce exposure.

### Hypotheses that were formed and then killed

Recorded because each was stated with confidence before being falsified:

- **Rate limit** — killed by 70 requests at 10/s running clean while 33 at 0.24/s throttled.
- **Byte-based budget** — killed by gzip changing nothing (identical band with 175 KB and
  1.1 MB responses), despite gzip being a real 6.5x bandwidth saving.
- **Distinct-article novelty** — killed by 5 repeated articles throttling at 30 while 4,331
  distinct throttled at 33.
- **Consent wall as a soft precursor to the 429** — killed by 9 clean runs showing zero
  consent responses before refusal.
- **Parallel exits contaminating each other** — killed by three solo controls (22, 23, 24)
  landing inside the parallel band.

### Two real phenomena that are not the throttle

- **A "Before you continue" consent interstitial** is served to some addresses instead of the
  article: 661 KB with no signature versus 1.06 MB with one. It is intermittent and
  address-dependent. Decode fails with a message blaming Google's markup, which is misleading.
  Sending a consent cookie *may* clear it — observed once, never reproducible afterwards, so
  the library sends one as best-effort with the uncertainty written into the comment.
- ~~**Stored tokens stop resolving within days.**~~ **FALSE — retracted 2026-07-28.** Tokens
  sampled from the head, middle and tail of the 4,331-token corpus archived over weeks all
  resolved, 9/9. The claim was formed while our request envelope was malformed, i.e. while
  *everything* failed, and token age took the blame for a bug in our own request. This is the
  fourth conclusion in this document to have been drawn from the envelope bug's blast radius.
  Fresh tokens are still free from `news.google.com/rss/search?q=...` (~200 per query) and
  remain the right source for probes — because they are tokens you have not already *spent*
  against the per-IP budget, not because stored ones go stale.

## Production budget: measured, then spent by the measurement

Run from the Hetzner box on 2026-07-28, after dual-stacking, against fresh tokens from ten
search queries:

| address family | requests | result |
|---|---|---|
| IPv6 | **400** | no 429, ever |
| IPv4 | **200** | no 429, ever |

Both figures are the script's own cap, not a limit — the ceiling was never found. **We need 48.**

### RETRACTED the same day. The measurement caused the condition it failed to find.

Minutes after that run, a five-URL decode from the deployed image resolved **0/5**, and the next
article fetch returned **HTTP 429**. So the budget is larger than 400 and *finite*, and the
ceiling script spent it. "No 429 within 600 requests" was never "no limit" — the script stopped
at its own caps.

Two compounding errors, both the same shape as every other one in this document:

- **It counted status codes, not usable responses.** The 0/5 failures came back as "Failed to
  fetch data attributes", i.e. Google served 200s with no signature in them *before* it started
  refusing. Those were counted as clean. A degraded page and a good one are indistinguishable
  to a check that only reads the status line.
- **It was a load test run against production**, on the one address whose budget the pipeline
  depends on, hours before a scheduled run.

What actually stands: the budget on this address is somewhere above 400 and below 600, which is
still far above the 48 a run needs — **when it has not just been spent by a probe.** Whether it
refills before the next run is the open question, and the only prior evidence (a VPN exit,
polled for 25 minutes) says recovery is not quick.

Do not run `v6ceiling` against production again. Probe from a VPN exit, or accept that the run
after a probe may resolve nothing.

Everything else in this document about budgets was measured from a laptop or a VPN exit. Those
numbers are real for those addresses and **do not describe production**. That is the same error
as the france24 diagnosis, in a different costume: reasoning about prod from a route prod does
not take. It is now the fourth time in this investigation.

What this retires:

- **Pacing, batching and "resolve only the first source per story" are all unnecessary.** There
  is no budget pressure to relieve. Resolve every article.
- **Routing production through a VPN would make things strictly worse** — a median-27 Proton
  exit in place of an address that took 400 without complaint.
- The per-address-versus-/64 question is moot. It came back inconclusive only because no
  address could be pushed to refusal.

Not retired: `AdaptiveRateLimit` and the 429 back-off stay, because "not throttled today" is
not "cannot be throttled". They cost nothing while unused.

## What this means for the pipeline

We make **48 GETs per run**, one per article, against a budget that measured 22–43 on VPN
exits and ~80 residential. Production is a **datacenter** address, the category Google treats
worst — the same reason france24's Akamai blocks it outright.

Levers that actually reduce GETs, in rough order of cost:

1. Resolve only the first cited source per story rather than every source.
2. Cache resolutions across runs, which helps where threads re-cite an article.
3. Accept unresolved links for the tail — costs link quality, not correctness.

**The one measurement that decides urgency has not been taken**: run the `degrade` probe from
the Hetzner box. Residential got ~80, VPN ~27; if Hetzner lands near 27 we have a live problem,
near 80 and we have headroom. Everything above was measured from addresses whose history we do
not control.

### Both open questions are now answered, and the answer removes a lever

Recovery probe, CM-6 (`74.118.126.39`), 2026-07-28:

```json
{"gets_before_429": 50, "search_feed_after_429": "ok (5 tokens)",
 "recovered_after_s": null, "gave_up_after_s": 1500}
```

- **The budget does NOT refill within a run.** Polled every 60s for 25 minutes after refusal
  and it never came back. Our pipeline takes ~20 minutes, so **pacing buys nothing** — once
  the budget is gone it is gone for the whole run. Spreading 48 GETs over the run was the
  cheap fix, and it is dead. The only remaining levers reduce the *number* of GETs.
- **Search feeds do not share the budget.** `news.google.com/rss/search` still returned
  tokens while article GETs were refused. So the Reuters and Nikkei feed fetches are free,
  and our true spend is the 48 article GETs, not more.

One more consequence: `embedded_url` short-circuiting (tokens that carry the publisher URL
inline, needing no request at all) is **not** a lever either. Zero of 4,331 archived tokens
and zero of 8 fresh ones were of that form — Google serves opaque handles now. Assume every
article costs the full round trip.

## Waiting on a decision

Nothing below has been committed, pushed or deployed. No production behaviour changed.

| item | state | needs |
|---|---|---|
| Upstream contribution | forked, branch pushed, 86 tests, CI + lint added | send the email; decide whether to collapse the legacy decoders first |
| `newsroom/src/gnews.py` | on the library adapter, against **PyPI**, not the fork | decide: stay on PyPI 0.1.7 (measured working), or pin the fork |
| `not_covered_blurb` cap 300 -> 500 | done, tested, green | commit |
| Hetzner budget | measured >400 and finite; the probe SPENT it and prod 429d immediately after | recheck before relying on it; do not re-run v6ceiling against prod |
| `scratch/gnews-rate-lab/` | working harness | keep as-is; gitignored, holds VPN credentials |

The upstream approach is deliberately **one branch, proposed privately by email**, rather than
a small security PR first. The maintainer merges without discussing (four external PRs merged,
one trivial PR closed) and has not answered an open security issue in ten months, so the choice
is between a big ask that might land and a small one that probably would -- and the project is
pre-1.0, which sanctions the breaking changes.

## The pattern underneath the bugs

Three times in one session the thing under test was not the code that shipped:

- the RPC envelope, where the PoC and the shipped code differed;
- `git checkout -- <path>`, which restores from the **index**, so a "revert to unpatched"
  check silently tested the patched code;
- a stale `build/` and `egg-info` making `pip install` build an older tree than the source
  beside it.

Each produced confident, wrong numbers that looked like results. That is the transferable
lesson, more than any measurement here.

See also `docs/solutions/` for the lessons extracted from this, and
`scratch/gnews-rate-lab/README.md` for how to re-run any of the measurements.
