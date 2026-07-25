---
title: A rename is silent until every reference is updated, because the old name keeps working
date: 2026-02-06
category: best-practices
module: deploy, terraform, systemd
problem_type: best_practice
severity: high
applies_when:
  - Renaming a Docker image, service, volume, or systemd unit
  - Renaming anything referenced from outside the repo
tags: [deploy, docker, terraform, systemd, rename, silent-failure]
---

# A rename is silent until every reference is updated

## The lesson

Renaming the Docker image `news-digest` to `digest-newsroom` did not break
anything. The old image stayed in the registry and kept being pulled
successfully. Nothing errored, nothing alerted. Production ran four days of
stale code before anyone noticed.

This is the defining property of a rename in a distributed deploy: the old name
does not stop existing, so the failure mode is not an error, it is *continuing
to work correctly on the wrong thing*.

## Checklist after renaming an image, service, or unit

1. `bin/deploy` build and push names
2. Terraform locals and resource definitions (image references)
3. `bin/deploy` terraform `-target` list -- a resource absent from `-target` is
   never re-applied, so the systemd unit silently keeps the old value
4. Verify on the server: `bin/ssh "grep docker /etc/systemd/system/news-digest.service"`

Step 3 was the actual root cause. The deploy reported success because every
resource it was told about did apply.

## Generalisation

After any rename, do not ask "did the deploy succeed." Ask **"what is the
running system actually executing right now"**, and go read it from the server.
A green deploy proves the resources you listed converged, not that you listed
all of them.

## Related

- [[systemd-persistent-true-fires-catch-up-runs]] -- another case where the
  deploy tooling behaves exactly as configured and the surprise is elsewhere.
