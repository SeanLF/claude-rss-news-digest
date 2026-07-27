---
title: A reachability test that does not pin the address family tests a path production never takes
date: 2026-07-27
category: best-practices
module: feeds, deploy, infrastructure
problem_type: integration
severity: high
applies_when:
  - A source works when you test it by hand but fails in the pipeline
  - Diagnosing an HTTP 403/blocked feed from the production host
  - About to conclude "it's fine from prod" or "it must be transient"
  - Deciding whether to replace a source that a WAF appears to be blocking
tags: [ipv6, ipv4, dual-stack, docker, akamai, 403, feeds, france24, network, reproduction]
---

`france24` returned HTTP 403 in production while returning 200 to every manual test from the
same box, on the same URL, within the same minute. It took three investigations to explain,
because the first two ran the test in a network context production never uses.

## What was actually happening

The Hetzner host is dual-stack. The newsroom container is not: Docker's default bridge is
IPv4-only, and `/etc/docker/daemon.json` never opted in. Akamai blocks the box's **IPv4**
address and serves its IPv6 address normally. So:

| call | result |
|---|---|
| `curl -4` (forced IPv4) | **403** |
| `curl -6` (forced IPv6) | **200** |
| host `python3` urllib, unforced | **200** — `getaddrinfo` prefers IPv6 |
| `feeds.fetch_feeds` in the container | **403** |
| same fetch, `docker run --network host` | **200, 24 articles** |

An unforced client on a dual-stack host picks IPv6. Every by-hand test therefore exercised a
path the IPv4-only container can never take, and reported success for a request production was
guaranteed to fail.

## Why the wrong conclusions were reachable

Both failed investigations produced *evidence*, and the evidence was internally consistent:

- Test the exact production call (`urllib`, same User-Agent) from the host: 200. Concluded
  "transient, not a block."
- Observe 403 at 10:25 UTC and 200 at 20:10 UTC, twice: concluded "time-correlated."

The second is the instructive one. A theory was falsified, a *new* theory was formed from the
same instrument, and the new theory was also wrong — because the instrument, not the theory,
was the defect. Reproducing under the real conditions (the production image, on the production
host, in a container) settled it in one run and also produced the fix.

## The rule

**Reproduce inside the deployment artefact before theorising.** Here that is
`docker run --entrypoint <interpreter> <the production image>` calling the production function,
not a shell on the host calling something that resembles it.

When a network test disagrees with production, enumerate what differs between the two clients
before proposing anything about the *server*:

- address family (`curl -4` vs `curl -6`) — the one that bit us, and invisible unless forced
- container vs host egress (NAT, missing IPv6 route, different source address)
- TLS/JA3 fingerprint and header order (`urllib` sends no `Accept` and `Accept-Encoding: identity`)
- DNS resolver and resolution order

Checking headers first is tempting and was wasted effort here: five header variants including a
full Chrome set all returned 403 from the container, because the request was never the problem.

## Corollaries

- **A control makes the rig honest.** `the_hindu` failing in the same test is what proved the
  harness could still observe a block. Later, forcing `curl -6` against it — 403 on both
  families — is what confirmed its diagnosis was genuinely different and dual-stack would not
  rescue it. Test the control both ways too; asserting "blocked everywhere" without ever having
  tried IPv6 is the same unforced assumption in a new costume.
- **"Works when I try it" is not evidence about production** unless you can name every
  difference between your client and production's.
- Prefer a fix validated by the same harness that reproduced the failure. `--network host`
  and an IPv6-enabled user-defined network were both confirmed by re-running the identical
  38-feed fetch and diffing per-source counts — one line changed, so nothing regressed.

Related: [[verify-the-validation-run-contains-the-code-under-test]].
