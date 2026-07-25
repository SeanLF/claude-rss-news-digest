---
title: The Claude CLI buffers session JSONL writes, so usage parsing needs CLAUDE_CODE_EAGER_FLUSH=1
date: 2026-03-16
category: integration-issues
module: usage, orchestrate
problem_type: integration
severity: medium
applies_when:
  - Reading a subprocess's log file immediately after it returns
  - Per-subagent token/usage accounting comes back empty or short
  - Adding a new environment to run the pipeline in
tags: [claude-cli, usage, jsonl, buffering, terraform, env]
---

# Buffered session logs need eager flush before you read them

## The lesson

The Claude CLI buffers JSONL session-log writes by default. Without
`CLAUDE_CODE_EAGER_FLUSH=1`, the session file may still be incomplete when the
usage parser reads it immediately after `generate_selections()` returns. Usage
rows come back partial or empty, with no error.

**This variable is set in terraform (`news-digest.tf`), not in this repo's
`docker-compose.yml`.** Anyone reproducing usage accounting locally, or standing
up a new environment, has to set it themselves or silently get bad numbers.

## The general shape

"The process returned, therefore its output file is complete" is false for any
process that buffers. If you read a file written by a subprocess, either force
the writer to flush, or wait on an explicit completion signal. Process exit is
not a flush barrier for files the process wrote through a buffered handle.

## Related

- Session JSONL lives in the `news-digest-claude` volume at `/home/appuser/.claude/`.
- `usage.py` holds no pricing table; since `3690210` the SDK's `total_cost_usd`
  from `orchestrate.py` is the source of truth.
