# Model Selection Mechanism

Research date: 2026-04-03  
Claude Code version: 2.1.91

---

## 1. Where model is currently configured

**Single model for all subagents. No per-subagent model selection exists today.**

The chain of control:

1. `newsroom/src/run.py` (line 143, 260) -- accepts `--model` CLI flag, defaults to nothing (None).
2. `newsroom/src/claude.py` (line 16) -- `generate_selections(model)` applies fallback: `_model = model or "sonnet"`.
3. `newsroom/src/claude_cli.py` (line 42) -- `_build_cmd` translates that into `claude --print --model <model>`.
4. The dispatcher (`/news-digest-select.md`) launches subagents using the Claude Agent tool (`name: cluster`, `name: recap`, etc.) with no model override.
5. All agent `.md` files in `.claude/agents/` have no `model:` frontmatter field.

**Result:** every session -- dispatcher, CLUSTER, SELECT, WRITE, RECAP, COHERENCE -- inherits the single `--model sonnet` flag passed to the parent `claude --print` process. The subagents get that model via inheritance (Claude Code v2.0.28 changelog: "claude can dynamically choose the model used by its subagents"; absent an explicit override they inherit the parent's model).

There is no per-subagent model configuration anywhere in the codebase.

---

## 2. Why RECAP and COHERENCE are on Sonnet (was Haiku ever wired up?)

**No. Haiku was only ever intended, never implemented.**

Project memory (MEMORY.md) notes: "Haiku" for RECAP and COHERENCE. The CLAUDE.md project file also says "(Haiku)" next to those two subagents. These are aspirational annotations added during architecture planning, not descriptions of live configuration.

Evidence from the codebase:

- `claude.py` `generate_weekly_recap()` (the *separate* weekly recap -- not the pipeline subagent) does correctly call Haiku: `run_sync(prompt, model="haiku", ...)`. This is the only place Haiku is actually used in the pipeline.
- The pipeline subagents `recap` and `coherence` (previously labelled `fact-check` in run_usage) have always run Sonnet since the subagent architecture was introduced (commit `1033839`, `6719ea6`).
- The `run_usage` DB shows every row for every subagent since tracking began has `model = claude-sonnet-4-6` with no exceptions.

The Haiku intention was never wired up because the mechanism to do so did not exist at the time the subagent pipeline was built. Claude Code gained per-agent model frontmatter support in **v1.0.64** ("Added model customization support - you can now specify which model an agent should use"). The current installed version (2.1.91) fully supports it.

---

## 3. Exact change needed to switch RECAP and COHERENCE to Haiku

Add a `model: haiku` field to the frontmatter of each agent file. No Python changes required.

**`.claude/agents/recap.md`** -- change:
```yaml
---
name: recap
description: Summarises recent RSS titles into a 2-3 sentence thematic recap of the past week. Run in parallel with the cluster agent.
tools: Read, Write
initialPrompt: "Process today's articles. All input/output files are in /app/data/claude_input/."
---
```
to:
```yaml
---
name: recap
description: Summarises recent RSS titles into a 2-3 sentence thematic recap of the past week. Run in parallel with the cluster agent.
model: haiku
tools: Read, Write
initialPrompt: "Process today's articles. All input/output files are in /app/data/claude_input/."
---
```

**`.claude/agents/coherence.md`** -- change:
```yaml
---
name: coherence
description: Fact-checks each headline against its source articles. Runs after the write agent completes.
tools: Read, Write
initialPrompt: "Process today's articles. All input/output files are in /app/data/claude_input/."
---
```
to:
```yaml
---
name: coherence
description: Fact-checks each headline against its source articles. Runs after the write agent completes.
model: haiku
tools: Read, Write
initialPrompt: "Process today's articles. All input/output files are in /app/data/claude_input/."
---
```

That is the complete change. Two files, one line added to each.

---

## 4. Cost savings estimate

Based on actual token usage from `run_usage` (18 runs for coherence / fact-check, 5 runs for recap):

| Subagent | Sonnet avg/run | Haiku avg/run | Saving/run |
|----------|---------------|--------------|------------|
| coherence | $0.695 | $0.232 | $0.463 |
| recap | $0.474 | $0.158 | $0.316 |
| **combined** | $1.169 | $0.390 | **$0.779** |

At 365 runs/year: **~$284/year** API-equivalent saving (down from prior $205 estimate, which used stale token data).

Note: these are API-equivalent costs for comparison purposes. The pipeline runs on a Claude subscription so actual cash cost is $0. The savings matter only if the subscription model changes, or for comparing relative model costs.

---

## 5. Risks and side effects

**COHERENCE on Haiku -- moderate risk:**

Coherence reads all `articles_*.csv` files plus `draft_selections.json` to fact-check every headline. This is a non-trivial reasoning task: it must cross-reference specific claims (names, numbers, dates) against article summaries and detect misattribution. Haiku 4.5 is significantly less capable than Sonnet on nuanced reasoning. Risk: higher false-negative rate (misses fabricated details) or higher false-positive rate (flags accurate headlines), either reducing digest quality or erroneously dropping stories.

Recommendation: test with `--dry-run --no-email --no-record --force` and inspect `coherence_report.json` across several days before deploying to production. Compare pass/fail rates and reasoning quality against existing Sonnet runs.

**RECAP on Haiku -- low risk:**

The recap task is simple: read recent RSS titles, write 2-3 thematic sentences. No cross-referencing, no fact-checking, no structured output schema. This is well within Haiku's capability. The `generate_weekly_recap()` function already uses Haiku for the same type of summarisation task and has worked correctly.

**Token volume note:**

COHERENCE is token-heavy (avg 88k cache_write + 344k cache_read per run) because it reads all article CSVs. Haiku's context window (200k) is sufficient -- 432k total input tokens is well within limits. The cache behaviour should carry over since the cache key includes the content, not the model.

**No Python changes needed:**

The `model: haiku` frontmatter is read by the Claude Code CLI directly. The `--model sonnet` flag on the parent dispatcher sets the dispatcher's model only; named subagents with explicit `model:` frontmatter override the inherited model. This is confirmed by Claude Code v1.0.64 release notes and the fix in v2.0.x for subagents sometimes not inheriting correctly.

**Usage tracking:**

`usage.py` pricing already handles Haiku correctly (line 121-122). The `run_usage` DB will automatically record `model = claude-haiku-4-5` (or whatever string the CLI reports) once deployed. The cost computation will use Haiku pricing. No changes needed.
