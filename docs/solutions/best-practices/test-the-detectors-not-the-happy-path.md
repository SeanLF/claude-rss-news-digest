---
title: A safety net is unproven until you have watched it fire
date: 2026-06-16
category: best-practices
module: healthcheck, db, broadcast
problem_type: best_practice
severity: high
applies_when:
  - Adding monitoring, alerting, or a dead-man's switch
  - Adding a fallback, retry, or abort path
  - Reviewing error handling that has never executed in production
tags: [observability, failure-paths, dead-mans-switch, alerting, incident]
---

# A safety net is unproven until you have watched it fire

## The lesson

On 2026-06-16 the daily digest failed silently for ~11 hours through four
compounding failures: CLUSTER hit the output-token ceiling (an SDK migration had
flipped extended thinking on for top-level queries), the dead-man's switch
crashed on a read-only data mount, its alert path crashed sourcing a `.env`
whose `DIGEST_NAME` contains an apostrophe, and a Resend read-timeout caused
`abort_run` to delete an already-delivered run.

The entire observability layer was one day old. Every piece of it failed on its
first activation. It had been tested by being written, not by being fired.

## Guidance

**Exercise the failure path deliberately before trusting it.** Break the thing
on purpose in a safe environment and watch the detector fire end to end,
including the alert delivery. Code that has only ever run in the happy path is
untested code with good intentions.

**Mark runs failed, never delete them.** `abort_run` deleting a row destroyed
the forensic trail for an email that had actually been delivered. Deletion on
error turns a recoverable incident into an unreconstructable one. Failure state
is data.

**Watch for quoting and escaping in alert paths specifically.** Alert code runs
rarely, under stress, in a different shell context than you tested. An
apostrophe in a config value took down the notification for the outage it was
built to report.

**Count the compounding.** A four-failure cascade is not four independent bugs;
it is one missing habit. Each layer assumed the layer beneath it worked.

## Detail

Full write-up: `docs/postmortems/2026-06-16-digest-outage.md`.

Follow-on incidents that share the shape: `docs/postmortems/2026-07-11-preheader-schema-outage.md`
(a hard cap aborting a run, with `--resume` silently lossy until fixed).

## Related

- [[timestamp-written-before-it-is-read]] -- the same class of mistake inside the
  pipeline rather than around it.
