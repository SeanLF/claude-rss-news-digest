# Design: Dispatcher Agent Extraction

**Date:** 2026-04-01  
**Status:** Approved

## Summary

Extract the five inline subagent task blocks from the dispatcher prompt into individual `.claude/agents/*.md` files. The dispatcher shrinks to pure orchestration logic. Each subagent's role, instructions, schema, and rules live in a self-contained file with `initialPrompt` as the task trigger.

## Motivation

The current `news-digest-select.md` dispatcher is 290 lines / 13.5 KB. ~80% is subagent task content that is mixed with orchestration logic, making it hard to iterate on individual subagent prompts without touching the dispatcher. The `initialPrompt` frontmatter feature (Claude Code 2.1.83) enables self-contained agent definitions that are independently versioned and editable.

## File Structure

### New files

```
.claude/
└── agents/
    ├── cluster.md      -- CLUSTER: group articles by story
    ├── recap.md        -- RECAP: summarise recent RSS titles
    ├── select.md       -- SELECT: editorial tier/region assignment
    ├── write.md        -- WRITE: headlines, summaries, why_it_matters
    └── coherence.md    -- COHERENCE: verify headlines vs source articles
```

### Modified files

- `.claude/commands/news-digest-select.md` -- shrinks from ~290 to ~60 lines (orchestration only)
- `newsroom/Dockerfile` -- add `COPY .claude/agents/ /app/.claude/agents/`

### Agent file structure

Each agent file follows this pattern:

```markdown
---
name: <name>
description: <one-line for agent auto-selection context>
tools: Read, Write
initialPrompt: "Process today's articles. All input/output files are in /app/data/claude_input/."
---

[Role statement]
[Step-by-step instructions]
[Output schema]
[Rules]
```

- **Body (system prompt):** full task content currently inside `<cluster_task>` / `<select_task>` etc. XML blocks
- **`initialPrompt`:** task trigger prepended to the first user turn -- makes the agent self-contained regardless of what the dispatcher passes
- **`tools: Read, Write`:** locked down, same constraint as today
- **No `model` field:** all five agents inherit the session default (Sonnet). RECAP and COHERENCE intentionally left on Sonnet -- quality matters for both.

## Thin Dispatcher

The dispatcher retains only:

1. **Preamble:** working directory, input file list
2. **Step 1:** Launch CLUSTER and RECAP *simultaneously in a single tool-call message*. Verify both output files exist and contain valid content. Retry each once independently if missing.
3. **Step 2:** Launch SELECT (after both Step 1 outputs verified). Verify output. Retry once if not.
4. **Step 3:** Launch WRITE (after SELECT verified). Verify output. Retry once if not.
5. **Step 4:** Launch COHERENCE (after WRITE verified). Verify output. Retry once if not.
6. **Step 5:** Assemble -- read `draft_selections.json` + `coherence_report.json`, drop failed headlines, call `write_selections` MCP tool.
7. **Global rules:** never read article data in parent context; article IDs only (no URLs); retry behaviour.

## Parallelism

CLUSTER and RECAP are safe to run in parallel:
- Each writes to a different output file (`clusters.json` vs `recap.txt`)
- Claude Code gives each agent an independent file state cache (confirmed in source)
- Coordinator guidance: read-heavy tasks with distinct output files = safe to parallelise

**Footgun mitigation:** The dispatcher must instruct Claude to launch both agents *simultaneously in a single message*. Community-reported issue (#7406): Claude can verbally commit to parallel but serialize in practice. Explicit "simultaneously, in one message" phrasing is the most reliable counter.

**No `run_in_background: true`:** Community-measured ~30% output file success rate (issues #17011, #21352). Foreground only.

## Dockerfile

```dockerfile
COPY .claude/commands/ /app/.claude/commands/
COPY .claude/agents/ /app/.claude/agents/   # new
```

No other changes to the build or Python pipeline.

## What Does Not Change

- All file paths (`/app/data/claude_input/`)
- Output schemas for all five subagents
- Retry logic (verify file → retry once)
- MCP call at the end (`write_selections`)
- Python pipeline (`claude.py`, `run.py`) -- untouched
- Models (all Sonnet, session default)

## Testing

1. Run `docker compose run --rm digest-newsroom --no-email --no-record --force` and confirm all five intermediate files are written: `clusters.json`, `recap.txt`, `selected.json`, `draft_selections.json`, `coherence_report.json`.
2. Confirm the final `write_selections` MCP call fires (visible in pipeline output).
3. Run `make ci` to confirm no regressions.
