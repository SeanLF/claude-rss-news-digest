# Per-Run API Budget Cap Design

**Date:** 2026-04-14
**Status:** Design proposal -- not yet implemented
**Scope:** Multi-vertical Claude pipeline. Prevents a single vertical's runaway cost from blowing the daily budget.

---

## Executive Summary

**Recommendation:** Use claude-code's native `--max-budget-usd` flag as the primary hard cap, layered with a wall-clock timeout and a pre-flight article cap. Per-vertical configuration lives in `vertical.json`. When the cap fires, fall back to the previous day's digest with an alert email to the operator.

**Why this is enough:**

- The CLI ships a `--max-budget-usd` flag that aborts the print-mode session when API spend crosses the threshold. This is the missing primitive that previously forced expensive workarounds (JSONL tailing, SDK migration, etc.).
- A wall-clock timeout already exists in `claude_cli.py`; it just needs to be tuned per-vertical and surfaced in config.
- Pre-flight article caps prevent the most predictable runaway (a vertical with a noisy feed dumping 5,000 articles in one run).
- Graceful degradation is cheap because we already write the previous day's digest to the `digests` table -- the broadcast path can re-send it with a "freshness fallback" notice.

**What this does NOT solve:**

- `--max-budget-usd` is post-turn: the cap fires at the end of the turn that crossed the threshold, not mid-call. CLUSTER's worst case is a single 12M cache-read turn that pushes past $5 in one shot. The wall-clock timeout is the safety net for that case.
- Subscription accounting: when running on a Claude subscription, `--max-budget-usd` accounts API-equivalent dollars, not real dollars. That is what we want for budgeting against future API migration, but operators should know the displayed cap is notional.
- Per-subagent caps. The CLI applies the cap to the entire dispatcher session, not per Agent invocation. CLUSTER cannot be capped at $3 while WRITE gets $1 -- the whole session is one budget. This is acceptable: in practice, a runaway is almost always CLUSTER, and capping the session at $8 catches it without micromanagement.

---

## 1. claude-code CLI Primitives for Cost Control

Investigated against the live CLI reference (https://code.claude.com/docs/en/cli-reference, fetched 2026-04-14).

### Flags relevant to budget enforcement

| Flag | Behaviour | Notes |
|------|-----------|-------|
| `--max-budget-usd` | "Maximum dollar amount to spend on API calls before stopping (print mode only)" | Native hard cap. Print mode (`-p`) only -- which is exactly what we use. **This is the primary primitive.** |
| `--max-turns` | "Limit the number of agentic turns... Exits with an error when the limit is reached" | Print mode only. Useful as a defence-in-depth limit, but turn count is a poor proxy for cost (a single CLUSTER turn at the end of a long session can read 12M cache tokens). |
| `--fallback-model` | Auto-fall back to a cheaper model when default is overloaded | Reliability feature, not cost. Doesn't cap spend -- just keeps the run alive. |
| `--effort` | low/medium/high/max effort level | Sonnet 4.6 / Opus 4.6 only. Tweaks reasoning depth. Cost-affecting but indirect; not a cap. |
| `--no-session-persistence` | Disables session save | Print mode only. Irrelevant to cost; matters for usage parsing (we depend on persistence for `parse_session_usage`). Do not enable. |

### Hooks that can interrupt a run

The hooks system (`PreToolUse`, `PostToolUse`, `Stop`, `SubagentStop`) can return exit code 2 or `{"continue": false}` to block tool execution or prevent stopping. A custom hook could in principle:

1. Read accumulated `run_usage` data after each subagent finishes (`SubagentStop` event).
2. If cumulative spend exceeds the cap, return a deny decision to abort the dispatcher.

This is more granular than `--max-budget-usd` (per-subagent, not per-session) but adds significant complexity. Skip for now; revisit if `--max-budget-usd` proves too coarse in production.

### What does NOT exist

- No `--max-input-tokens` or `--max-output-tokens` flag at the CLI level (`max_tokens` is per-message in the SDK, which doesn't help cap accumulated spend).
- No mid-stream interrupt signal beyond SIGTERM/SIGKILL on the subprocess.
- No per-subagent budget allocation flag.
- No `--budget-warn-usd` (soft) flag distinct from the hard cap.

---

## 2. External Enforcement Mechanisms (Ranked)

Even with `--max-budget-usd`, layered defences matter because the native cap fires post-turn, not mid-call.

### Tier 1: Use what exists (low effort, high effectiveness)

**A. Native `--max-budget-usd` (RECOMMENDED PRIMARY).**
Add the flag to `_build_cmd` in `claude_cli.py` and pipe a per-vertical value through. Cost: trivial; ~10 lines.
*Failure mode:* fires at end of expensive turn. Bounded miss is roughly one CLUSTER turn = up to $5 over cap on the worst day.

**B. Wall-clock timeout (RECOMMENDED SECONDARY).**
Already implemented in `claude_cli.run` (default 600s). `stream_sync` does NOT honour a timeout today -- it reads until the subprocess exits. Add one. Tuning: based on production data, runs complete in 2-15 minutes; set per-vertical default to 30 minutes (1800s) hard ceiling.
*Failure mode:* may kill a slow-but-cheap run. Acceptable -- a 30-minute run is anomalous regardless of cost.

**C. Pre-flight article count cap (RECOMMENDED TERTIARY).**
In `prepare.py`, after dedup but before `prepare_claude_input`, count articles. If over `vertical.max_input_articles` (default: 1000), truncate by source priority + recency. Cost: ~20 lines.
*Failure mode:* loses editorial coverage on news-heavy days. Tradeoff: predictable cost ceiling.

### Tier 2: Tail-the-JSONL approach (medium effort, marginal benefit over Tier 1)

A background thread tails the live session JSONL, accumulates token counts per assistant message, computes running cost, and SIGTERMs the subprocess when over cap.

**Why it's not Tier 1:** `--max-budget-usd` already does this internally with better signal (it sees the actual usage record before we can parse it from disk). The only reason to build it would be per-subagent limits or sub-turn granularity, neither of which the JSONL gives us anyway -- usage records appear after the assistant message completes.

Skip unless `--max-budget-usd` proves unreliable.

### Tier 3: Per-subagent dispatcher-level budgets (high effort, theoretically appealing)

The dispatcher prompt could be instructed to abort if a subagent file reports a partial result. But:

- The CLI doesn't expose per-Agent token usage to the parent, so the dispatcher can't see "CLUSTER spent $3, abort".
- Building this requires a hook on `SubagentStop` that reads usage from the subagent's own JSONL (which is in `~/.claude/projects/<id>/subagents/`), tallies cost, and returns `{"continue": false}` if the total exceeds the cap.

Worth doing only if a single vertical needs heterogeneous limits (e.g. "I trust CLUSTER with $5 but FACT-CHECK should never exceed $0.50"). Park.

### Tier 4: Migrate hot paths to the Anthropic SDK (very high effort)

Per `sdk-migration-feasibility.md`, this is a multi-week project. It would give per-call token control, but for the budget-cap problem, `--max-budget-usd` is sufficient. Not justified by this requirement alone.

---

## 3. Graceful Degradation When the Cap Hits

When `--max-budget-usd` triggers, claude-code exits with non-zero status and the dispatcher session ends partway through. State of intermediate files depends on which subagent was running:

| Cap fires during | Files present | Recoverable? |
|------------------|---------------|--------------|
| CLUSTER | `clusters.json` may be partial or absent | No -- restart needed |
| RECAP | `recap.txt` may be missing | Yes -- can run SELECT/WRITE without it (degraded continuity) |
| SELECT | `clusters.json` exists, `selected.json` partial | Possibly -- could fall back to "first N clusters by article count" |
| WRITE | `selected.json` exists, `draft_selections.json` partial | No -- WRITE is the value-add step |
| COHERENCE | `draft_selections.json` exists | Yes -- skip coherence, send draft as-is with a warning |

**Recommended degradation policy (ordered by preference):**

1. **If COHERENCE was the only step that failed:** publish the draft selections, log a warning to the operator, mark the run with `coherence_skipped = true`. Reader-facing impact: minimal.
2. **If RECAP failed only:** continue without recap continuity (SELECT will run with empty `recap.txt`). Reader impact: possibly more day-over-day repetition.
3. **If CLUSTER, SELECT, or WRITE failed:** abort the run, alert the operator, re-broadcast the previous day's digest with a banner: *"Today's digest was unavailable; here is yesterday's edition."* The previous digest is already in the `digests` table -- `find_latest_digest()` already exists.
4. **If degraded broadcast also fails (no previous digest):** send an operator-only alert email; subscribers receive nothing for that day.

A cheaper local-clustering fallback (TF-IDF) was considered. The 2026-03-19 PoC found TF-IDF unable to match Claude's narrative grouping (ARI ~0.30 vs Claude). For a fallback that ships to readers, this is too lossy. TF-IDF can stay as the dedup pre-filter only.

**Implementation notes:**
- `claude_cli.stream_sync` should detect the cap-exit signature in stderr ("budget exceeded") and raise a typed `BudgetExceeded` exception so the orchestrator can branch.
- The orchestrator catches `BudgetExceeded`, inspects which intermediate files exist, and chooses the degradation path.
- A `cap_hit` boolean and `cap_phase` text column on `digest_runs` records what happened.

---

## 4. Per-Vertical Monitoring Schema Changes

### `digest_runs` additions

```sql
ALTER TABLE digest_runs ADD COLUMN budget_cap_usd REAL;
ALTER TABLE digest_runs ADD COLUMN cap_hit BOOLEAN NOT NULL DEFAULT 0;
ALTER TABLE digest_runs ADD COLUMN cap_phase TEXT;  -- 'cluster', 'select', 'write', 'coherence', null
ALTER TABLE digest_runs ADD COLUMN degraded_mode TEXT;  -- 'none', 'skip_coherence', 'previous_digest', 'no_send'
```

`run_usage` already records per-subagent cost; combined with the new columns, a daily rollup query becomes:

```sql
SELECT
  vertical_id,
  date(run_at) AS day,
  COUNT(*)                                    AS runs,
  SUM(api_cost_total)                         AS total_cost,
  AVG(api_cost_total)                         AS avg_cost,
  MAX(api_cost_total)                         AS max_cost,
  AVG(budget_cap_usd)                         AS cap,
  ROUND(AVG(api_cost_total) * 100.0 / AVG(budget_cap_usd), 1) AS avg_pct_of_cap,
  SUM(cap_hit)                                AS cap_hit_count,
  SUM(CASE WHEN degraded_mode != 'none' THEN 1 ELSE 0 END) AS degraded_count
FROM digest_runs r
JOIN (SELECT run_id, SUM(api_cost_usd) AS api_cost_total FROM run_usage GROUP BY run_id) u
  ON u.run_id = r.id
WHERE run_at >= datetime('now', '-7 days')
GROUP BY vertical_id, day
ORDER BY day DESC, total_cost DESC;
```

The key signals to watch:
- `avg_pct_of_cap > 70%`: cap is too tight, raise it or fix the underlying cost driver.
- `cap_hit_count > 0` for any vertical: investigate the run.
- `degraded_count > 1/week`: reader experience is suffering, fix.

### Stats page (circulation)

Add a "Budget" column to the Recent Runs table on `/stats`:
- Green if cost < 50% of cap.
- Yellow if 50-90%.
- Red if > 90% or `cap_hit = 1`.
- Show degradation mode as a badge when not 'none'.

### Operator alert

Re-use the existing `send_health_alert` plumbing. Trigger when `cap_hit = 1`. Email body:
- Vertical name, run timestamp, cap value, actual spend.
- Which phase fired the cap.
- Which degradation mode was applied.
- Link to the stats page.

---

## 5. `vertical.json` Configuration Shape

```json
{
  "id": "news-digest",
  "display_name": "News Digest",

  "budget": {
    "hard_cap_usd": 8.00,
    "soft_warn_usd": 5.00,
    "wall_clock_seconds": 1800,
    "max_input_articles": 1000,
    "degradation": {
      "on_coherence_fail": "publish_draft",
      "on_other_fail": "previous_digest",
      "on_no_previous": "alert_only"
    }
  }
}
```

**Defaults (applied if `budget` block is absent):**
- `hard_cap_usd`: $10. Generous; any vertical that legitimately exceeds this needs scrutiny.
- `soft_warn_usd`: 60% of hard cap. Generates an operator email but does not abort.
- `wall_clock_seconds`: 1800 (30 min).
- `max_input_articles`: 1000 (current pipeline runs at ~600).
- `degradation`: as above.

**Per-vertical tuning guidance:**
- News digest: `hard_cap_usd: 8.00` (matches observed max + 50% headroom).
- A new vertical with no production data: start at $5 hard cap, $3 soft warn, observe for two weeks, raise if needed.
- A vertical known to have variable input volume: tighten `max_input_articles` rather than raising the dollar cap.

**Validation rules:**
- `soft_warn_usd < hard_cap_usd` (or omit soft).
- `hard_cap_usd > 0`.
- `wall_clock_seconds >= 60` (anything lower is almost certainly a typo).
- Reject startup if validation fails -- fail-fast on misconfiguration.

---

## 6. How Other Pipelines Handle This (Brief)

Public posts are sparse on per-job LLM cost limits specifically -- most "LLM ops" content focuses on aggregate billing alerts, not per-job hard caps.

- **Zapier AI:** publishes a per-step token cap and degrades with a "step skipped: token limit" message to the user. Implementation details not public.
- **Shortwave / Glean:** known to use Anthropic batch APIs for offline processing where cost predictability matters more than latency. Per-request cost is bounded by `max_tokens` on the SDK call, not a session-level cap.
- **LangChain / LlamaIndex:** community projects such as `langchain-budget` exist as middleware that wrap `BaseChatModel.invoke` and raise on threshold. Same pattern as our Tier-2 JSONL tailer, but at the SDK call site.
- **Anthropic's own Workbench:** caps per-message `max_tokens` but exposes no session budget. Confirms the SDK's `max_tokens` is per-message, not cumulative.

The pattern across the industry: when a tool exposes a session budget primitive, use it; otherwise, wrap the SDK call site with a counter and raise when the threshold trips. claude-code's `--max-budget-usd` puts us in the first category.

**On Anthropic SDK `max_tokens`:** caps output tokens for a single message. Does not cap input, cache reads, cumulative spend, or anything across multiple messages. Useless for our problem -- a CLUSTER session with 75 messages of 1k output tokens each can't be controlled with `max_tokens=1000`.

---

## 7. Implementation Sketch (Python pseudocode)

### `claude_cli.py` -- add the flags

```python
def _build_cmd(prompt: str, *, ..., max_budget_usd: float | None = None) -> list[str]:
    cmd = ["claude", "--print", "--model", model]
    ...
    if max_budget_usd is not None:
        cmd.extend(["--max-budget-usd", f"{max_budget_usd:.2f}"])
    ...

class BudgetExceeded(RuntimeError):
    """Raised when claude-code aborts due to --max-budget-usd."""
    def __init__(self, cap_usd: float, phase: str | None = None):
        super().__init__(f"budget cap ${cap_usd:.2f} hit in phase={phase}")
        self.cap_usd = cap_usd
        self.phase = phase

def stream_sync(..., max_budget_usd=None, timeout=1800):
    cmd = _build_cmd(..., max_budget_usd=max_budget_usd)
    proc = subprocess.Popen(cmd, ...)
    deadline = time.monotonic() + timeout if timeout else None
    try:
        for raw in proc.stdout:
            if deadline and time.monotonic() > deadline:
                _kill_proc(proc)
                raise TimeoutError(f"claude exceeded {timeout}s wall clock")
            ...
            yield json.loads(raw)
    finally:
        ...
    if proc.returncode != 0:
        stderr = proc.stderr.read()
        if "budget" in stderr.lower() and "exceed" in stderr.lower():
            raise BudgetExceeded(max_budget_usd, phase=_infer_phase_from_files())
        raise RuntimeError(...)
```

### `claude.py` -- pass the cap from vertical config

```python
def generate_selections(model=None, budget: BudgetConfig | None = None) -> None:
    cap = budget.hard_cap_usd if budget else None
    timeout = budget.wall_clock_seconds if budget else 1800

    for event in stream_sync(
        "/news-digest-select",
        model=model or "sonnet",
        permission_mode=_PERMISSION_MODE,
        allowed_tools=_MCP_TOOL,
        mcp_config=_MCP_CONFIG,
        max_budget_usd=cap,
        timeout=timeout,
    ):
        ...  # existing event handling
```

### `run.py` -- handle the exception

```python
try:
    generate_selections(model=args.model, budget=vertical.budget)
except BudgetExceeded as e:
    logger.error("Budget cap hit during %s: $%.2f", e.phase, e.cap_usd)
    db.mark_cap_hit(phase=e.phase)
    return _degrade(e.phase, vertical.budget.degradation)
except TimeoutError as e:
    logger.error("Wall-clock timeout: %s", e)
    db.mark_cap_hit(phase="timeout")
    return _degrade("timeout", vertical.budget.degradation)


def _degrade(phase: str, policy: DegradationPolicy) -> int:
    """Apply degradation policy. Returns process exit code."""
    if phase == "coherence" and policy.on_coherence_fail == "publish_draft":
        # Skip the coherence-drop step in digest.py; send draft as-is
        return _publish_draft_with_warning()

    if policy.on_other_fail == "previous_digest":
        prev = find_latest_digest()
        if prev:
            return _broadcast_previous(prev, banner="Today's digest was unavailable; here is yesterday's edition.")

    send_operator_alert(phase=phase, vertical_id=vertical.id)
    db.set_degraded_mode("alert_only")
    return 0  # not a process failure -- the alert IS the outcome


def _publish_draft_with_warning() -> int:
    """Skip COHERENCE drop step; publish draft_selections.json as-is."""
    selections = load_selections(CLAUDE_INPUT_DIR / "draft_selections.json")
    selections = resolve_article_ids(selections)
    digest = write_digest(selections, TEMPLATE_FILE)
    replace_placeholders(digest, selections, STYLES_FILE, extract_preheader(selections))
    db.save_digest(digest, preheader=extract_preheader(selections))
    db.set_degraded_mode("skip_coherence")
    if db.should_broadcast():
        send_broadcast(digest, prepare_for_email)
    return 0
```

### Pre-flight cap in `prepare.py`

```python
def prepare_claude_input(sources, dry_run=False, article_limit=None, max_input_articles=None):
    articles = collect_and_dedupe()
    if max_input_articles and len(articles) > max_input_articles:
        logger.warning(
            "Pre-flight cap: %d articles -> %d (cap=%d)",
            len(articles), max_input_articles, max_input_articles,
        )
        articles = sorted(articles, key=lambda a: (a.source_priority, -a.fetched_at))[:max_input_articles]
        db.mark_pre_flight_truncated(original=len(articles), kept=max_input_articles)
    write_csv_files(articles)
```

---

## 8. Open Questions

1. **Does `--max-budget-usd` cleanly abort or does it allow the in-flight tool call to finish?** Documentation says "stops" but doesn't specify granularity. Worth a one-off test: set cap to $0.50, run a CLUSTER-only invocation, observe behaviour.
2. **Does the cap apply to subscription accounting?** If running on a Claude subscription (current setup), the CLI must internally compute API-equivalent cost for the cap to work. Untested. May need to fall back to wall-clock + token tailing if subscription mode ignores the flag.
3. **Multi-vertical scheduling:** if vertical A has a soft warn, do we delay vertical B that day? Probably not -- soft warns are informational. But the daily rollup should make that decision visible.
4. **Cap for `generate_weekly_recap`:** the Haiku call already uses `max_turns=1, timeout=120`. A small `max_budget_usd=0.10` adds a third belt for trivial cost, probably not worth the config noise. Skip unless we see weekly recap balloon.

---

## 9. Effort Estimate

| Phase | Files touched | Estimated effort |
|-------|---------------|------------------|
| Add `--max-budget-usd` and timeout to `claude_cli.py` | 1 file | 1 hour |
| `BudgetExceeded` exception + stderr detection | 1 file | 1 hour |
| `vertical.json` schema + loader validation | 2 files | 2 hours |
| Wire `budget` config into `claude.py` and `run.py` | 2 files | 2 hours |
| Migration: `cap_hit`, `cap_phase`, `degraded_mode`, `budget_cap_usd` columns | 1 file | 30 min |
| Degradation paths (publish_draft, previous_digest, alert_only) | 2 files | 4 hours |
| Pre-flight article cap in `prepare.py` | 1 file | 1 hour |
| Stats page badge + Recent Runs column | Rust circulation | 2 hours |
| Operator alert email template | `broadcast.py` | 1 hour |
| Tests (cap-hit simulation, degradation paths) | new test file | 3 hours |
| **Total** | ~10 files | **~17 hours** (~2 working days) |

Phased delivery option:

- **Phase 1 (1 day):** `--max-budget-usd` + timeout + `cap_hit` column + alert. No degradation; failed runs just send the operator alert and skip that day's broadcast. Provides the hard ceiling immediately.
- **Phase 2 (half day):** previous-digest fallback. Reader experience improves for cap-hit days.
- **Phase 3 (half day):** publish-draft for coherence-only failures. Squeezes out the last bit of value from partially-completed runs.

Phase 1 alone solves the headline problem ("a single bad day pushes a vertical from $5 to $25"). Phases 2 and 3 are reader-experience polish.
