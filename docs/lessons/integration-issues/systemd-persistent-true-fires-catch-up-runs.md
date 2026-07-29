---
title: systemd Persistent=true fires a catch-up run on boot, which can re-send a cancelled digest
date: 2026-02-06
category: integration-issues
module: deploy, systemd, broadcast
problem_type: integration
severity: high
applies_when:
  - The server reboots, including from an unattended apt upgrade
  - A digest has been cancelled manually and the timer window was missed
  - Adding or changing a systemd timer
tags: [systemd, timers, duplicate-runs, reboot, broadcast]
---

# systemd `Persistent=true` fires a catch-up run on boot

## The lesson

If a timer misses its window because the machine was down, `Persistent=true`
makes systemd fire the service **immediately on next boot**. This is documented
behaviour and usually what you want. It is not what you want when the run has
side effects that already happened.

Real incident: an `apt upgrade` during an unrelated seanfloyd.dev session
triggered a reboot. The day's digest had already been cancelled through the
Resend console. On boot, the catch-up run fired and sent a second, incomplete
digest to subscribers. That cascaded into data cleanup, failed re-trigger
attempts against the wrong Docker image name, and roughly thirty minutes of
incident response.

## Mitigations in place

- `has_completed_run_today()` duplicate-run guard in the pipeline, failing
  closed.
- `--force` to override deliberately.
- `abort_run()` cleans up failed runs (but see
  [[test-the-detectors-not-the-happy-path]] -- mark failed, do not delete).

## Guidance

Any timer whose service has **irreversible external side effects** -- sending
email, posting, charging -- needs an idempotency guard inside the service, not
just correct timer configuration. The timer is allowed to fire twice; the send
is not allowed to happen twice.

Watch for duplicates after any reboot or unattended upgrade.
