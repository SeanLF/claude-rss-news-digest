---
title: A pipeline under set -e reports only its last stage, so a parser failure becomes an empty result that equals another empty result
date: 2026-09-02
category: logic-errors
module: deploy
problem_type: logic_error
severity: high
applies_when:
  - A shell gate compares two values extracted with jq, grep, awk, or sed inside a pipeline
  - The script runs under `set -e` without `set -o pipefail`
  - The gate's failure mode is "it passed" rather than "it errored"
tags: [bash, set-e, pipefail, jq, deploy, smoke-test, negative-control]
---

The post-deploy MCP smoke fetched the discovery card and `tools/list` from prod and compared
their tool names:

```bash
card=$(printf '%s' "$card_json" | jq -er '.tools[].name' | sort) || { log_error ...; return 1; }
served=$(printf '%s' "$served_json" | jq -er '.result.tools[].name' | sort) || { ...; return 1; }
[ "$card" != "$served" ] && return 1
log_success "MCP contract intact"
```

`bin/deploy` runs `set -e` with no `pipefail`. A pipeline's status is its **last** command's,
and `sort` exits 0 on empty input. So when prod answered every path with a 200 HTML
interstitial, `jq -er` failed on both sides, both variables became empty, empty equalled
empty, and the gate reported the contract intact. The comment two lines above it explained
that the curls had been split out of pipelines for exactly this reason. The jq calls, which
decide the verdict, had not been.

The adversarial reviewer found it by pointing the extracted function at a Python server that
returns `<html>Just a moment...</html>` for everything. That is the negative control the
gate should have shipped with; a smoke that has never been shown to fail has only been shown
to print a green line.

## The rule

Every stage whose failure should fail the gate runs as its own statement, never as a pipeline
stage:

```bash
card=$(jq -er '.tools[].name' <<<"$card_json") || return 1
served=$(jq -er '.result.tools[].name' <<<"$served_json") || return 1
[ -n "$card" ] && [ -n "$served" ] || return 1
card=$(sort <<<"$card"); served=$(sort <<<"$served")
```

Three habits, in order of how often they were the missing one here:

1. **Extract, then transform.** `x=$(parser) || fail` and only then `x=$(sort <<<"$x")`.
   `set -o pipefail` is the other fix, but it changes the semantics of every pipeline in a
   900-line script; splitting the statement changes one.
2. **Reject empty on both sides before comparing.** Two empties are equal; equality is not
   the property being checked. `jq -e` exits 0 on `{"tools":[{"name":""}]}`, so this check
   is load-bearing, not decorative.
3. **Check the tool exists.** `jq` had no preflight, and the only earlier step that would
   have failed without it is skipped under `--skip-build`, the documented recovery path.

## The second lesson, smaller

The comparison itself proves less than its name says. Card and `tools/list` are built by one
function in one binary and cannot disagree by construction; the in-process test already
asserts that. What the remote check catches is the proxy or a regressed route serving
something else on one of several paths, which is why it now fetches every GET door, not the
one that was convenient. Name the check for what it detects, or a future reader will trust
it against a class of failure it was never able to see.
