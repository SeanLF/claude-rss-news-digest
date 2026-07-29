---
title: Verify a validation run actually contains the code and data under test, before spending on it
date: 2026-07-25
category: best-practices
module: docker, pipeline, dedup
problem_type: best_practice
severity: medium
applies_when:
  - Running the full pipeline locally to validate changes before a deploy
  - Any "end-to-end test" whose result you intend to trust
  - Reading per-source or per-run counts and concluding something about production
tags: [docker, validation, integration-test, db-clone, dedup, false-confidence]
---

Validating a large change with a full local pipeline run took **three attempts**. The first
two produced clean exit codes, rendered digests, and complete logs — and were worth nothing.
Both failures were silent, and both were checkable in seconds beforehand.

## Attempt 1: the image did not contain the code

`docker compose run --rm digest-newsroom ...` does **not** rebuild. It ran a five-day-old
image, so none of the day's source changes were present — and because `newsroom/Dockerfile`
`COPY`s `sources.json` into the image rather than mounting it, the run also used the *old*
source catalog. A run that appeared to validate 38 sources and a dozen code changes actually
exercised neither.

The check costs one command and no API tokens:

```
docker compose build digest-newsroom
docker compose run --rm --no-deps --entrypoint sh digest-newsroom \
  -c "grep -c wire_agency /app/src/prepare.py; python3 -c \"import json;print(len(json.load(open('/app/sources.json'))))\""
```

Assert the new symbol is present and the counts match what you expect *before* starting the
expensive part.

## Attempt 2: the database did not contain the data

The rebuilt image ran, but against the local `data/digest.db`, whose newest
`shown_narratives` row was **22 days old**. Cross-day dedup selects the blocklist with
`shown_at > datetime('now','-7 days')`, so it returned zero rows and dedup was completely
inert.

The visible symptom was a corpus of **1,268 articles against production's 366-604**, at
2.5x the clustering cost. That inflation then produced two SDK idle timeouts in SELECT and
WRITE — which production has **never** recorded — and it was tempting to report that as
"the new sources erode timeout headroom." It was not a finding. It was an artifact of a
stale database, and a real conclusion was nearly drawn from it.

The check, again seconds:

```sql
SELECT COUNT(*), MAX(date(shown_at)) FROM shown_narratives
WHERE shown_at > datetime('now','-7 days');
```

Zero rows means dedup is off and every downstream count is inflated.

## The rule

**A validation run has preconditions, and they deserve assertions of their own.** Before
spending time or tokens, verify the artifact under test is present (image contains the code
*and* the baked-in config) and the data under test is current (the DB has rows inside the
windows the code queries). A green run against the wrong inputs is worse than no run: it
manufactures confidence.

State the preconditions in the same breath as the result. "The pipeline completed" means
nothing without "on an image built at T, against a DB current to D."

## Two local-vs-production divergences that remain even after both fixes

Worth stating whenever a local run is used as evidence:

- **Egress differs.** `the_hindu` is Cloudflare-blocked from the production ASN and returns
  zero articles there, but fetches normally from a residential IP — locally it contributed
  60 articles and shipped in 27 source slots. The source mix of a local run is not
  production's.
- **`make db-clone` overwrites `data/digest.db`** and copies over SSH, which can truncate.
  Verify before trusting it: `page_count * page_size == filesize`, plus
  `PRAGMA integrity_check`, plus a sanity read of the newest row.

See also [[unit-tests-at-both-ends-of-a-seam-pass-while-the-seam-is-broken]] — the same
session, and the same underlying shape: every layer reported success while the thing under
test was absent.
