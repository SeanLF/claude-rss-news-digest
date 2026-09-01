---
title: The Agent SDK ships system_prompt as one argv entry, so a large inlined corpus fails at connect time with an infra-shaped error
date: 2026-08-31
category: integration-issues
module: claude_cli, orchestrate
problem_type: integration
severity: medium
applies_when:
  - You are moving data out of the Read-tool loop and into the prompt to cut cache-read cost
  - A stage fails with "Failed to start Claude Code" or Errno 7 and you are about to blame Docker
  - You are deciding whether a payload belongs in system_prompt or in the user message
tags: [claude-agent-sdk, arg-max, subprocess, system-prompt, stdin, cost, coherence]
---

# `system_prompt` is an argv entry. The user message is not.

Inlining COHERENCE's ~289 KB corpus into `system_prompt` produced:

```
claude_agent_sdk._errors.CLIConnectionError: Failed to start Claude Code:
  [Errno 7] Argument list too long: '.../claude_agent_sdk/_bundled/claude'
```

The SDK's subprocess transport passes the system prompt as a single command-line argument.
Linux caps **one argument** at `MAX_ARG_STRLEN` = 128 KiB (32 pages), independently of the
total `ARG_MAX`. On this machine `getconf ARG_MAX` reports 1048576, so the obvious check says
there is room. There is not: the per-argument limit is the binding one.

**The user message has no such limit** — the transport sets up stdin for streaming and sends
messages through it. So:

- **system prompt** = the rules. Small, stable, cacheable.
- **user message** = the data. Arbitrarily large.

That is also the semantically correct split, and it is the one to reach for first. The corpus
went into `system_prompt` only because that is where the agent body already went — copying a
location instead of choosing a channel.

## Why this one is worth a lesson rather than a commit note

**It fails at connect time, before the model is reached, with an error that reads as
infrastructure.** "Failed to start Claude Code" plus an `Errno` invites you to look at Docker,
the bundled binary, the mount, the image — everything except the thing you just changed. The
first instinct is to retry.

The tell is that it is deterministic and instant. A resource or environment fault would be
intermittent or slow; this one fires immediately, every time, at the same size.

## The ceiling this puts on the plumbing refactor

Context plumbing is **73.7% of this pipeline's bill** (runs 274-280: cache write $19.95 +
cache read $6.24 of $35.29), because file-handoff stages re-send their corpus through the tool
loop — COHERENCE reads its own ~40k-token evidence base roughly 48 times per run on a fresh
input of 41.7 tokens. Moving data out of that loop is the single largest cost lever available.

This lesson does not block that. It constrains where the data may go: **the user message,
never the system prompt.** Any stage converted this way inherits the same rule.

A test pins it (`test_the_system_prompt_stays_under_the_single_argv_limit`), so the next person
to inline something gets a red test instead of a 20-minute detour through Docker.

## Re-run it

```bash
getconf ARG_MAX          # 1048576 here -- NOT the limit that binds
python3 -c 'print(32*4096)'   # 131072 -- MAX_ARG_STRLEN, the one that does
```
