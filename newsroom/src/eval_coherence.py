"""Harness-faithful regression eval for the COHERENCE stage.

Runs the REAL `.claude/agents/coherence.md` (its body AND frontmatter model)
through the same claude-agent-sdk path production uses, against a frozen,
hand-labelled snapshot (``newsroom/tests/fixtures/coherence_faithful/``), and
scores the report against field-level labels: recall on confirmed hallucinations
and false-drops on clean fields.

Why this exists: a cheaper proxy (Claude Code "subagents") over-stated recall
~2.5x because the harness differed from production (model version, system prompt,
tools, thinking). This eval reproduces the production harness exactly, so the
number is trustworthy. See docs/2026-07-21-coherence-reframe-design.md.

Makes REAL model calls on the subscription -> opt-in only (``make eval-coherence``
/ ``bin/eval-coherence``), never in CI. The stage is stochastic, so this reports
a per-run scorecard over N runs (default 2) and exits non-zero only on an
EGREGIOUS regression (false-drops > 2 on a run, or recall 0 on every run) -- a
human judges the middle. It reads the live coherence.md, so it tests whatever the
prompt currently says.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import shutil
from pathlib import Path

import claude_cli  # /app/src
import orchestrate
import schema

# Default paths inside the container (bin/eval-coherence mounts the fixture dir).
AGENT = Path("/app/.claude/agents/coherence.md")
FIXTURES = Path("/app/eval-fixtures")
REPORT_NAME = "coherence_report.json"


def load_agent(path: Path) -> tuple[str, str, dict, list[str]]:
    """Return (model, body, thinking, tools) parsed from a `---`-delimited agent
    file, mirroring orchestrate.py so the eval matches production faithfully: tools
    come from the `tools:` frontmatter, and thinking defaults to disabled (the
    orchestrate `_THINKING` default) unless the agent opts into `thinking:`."""
    text = path.read_text(encoding="utf-8")
    _, frontmatter, body = text.split("---", 2)
    fields: dict[str, str] = {}
    for line in frontmatter.splitlines():
        k, _, v = line.strip().partition(":")
        if k.strip():
            fields[k.strip()] = v.strip().strip("\"'")
    model = fields.get("model", "")
    if not model:
        raise SystemExit(f"{path}: no model in frontmatter")
    tools = fields.get("tools", "Read, Write").replace(",", " ").split()
    thinking_val = fields.get("thinking")
    thinking = {"type": thinking_val} if thinking_val else {"type": "disabled"}
    return model, body.strip(), thinking, tools


# The prod input path the live agent prompts hardcode; the evals redirect it to the
# mounted fixture dir. Shared redirect contract for the coherence/repair evals.
_PROD_INPUT_MARKER = "/app/data/claude_input/"


def load_agent_for_eval(
    agent_path: Path,
    fixtures: Path,
    model_override: str | None = None,
    *,
    today: datetime.date | None = None,
) -> tuple[str, str, dict, list[str]]:
    """load_agent + redirect the agent's hardcoded prod input path to the mounted
    fixture dir, applying an optional model override.

    Asserts the marker is present so a prompt whose paths drifted fails HERE with a
    clear SystemExit rather than silently running against the wrong location."""
    model, base_body, thinking, tools = load_agent(agent_path)
    if model_override:
        model = model_override
    if _PROD_INPUT_MARKER not in base_body:
        raise SystemExit(
            f"{agent_path}: expected {_PROD_INPUT_MARKER!r} in body to redirect for the eval; prompt paths drifted"
        )
    body = base_body.replace(_PROD_INPUT_MARKER, f"{fixtures}/")
    # Render exactly as production does, or the harness measures a prompt production never sent.
    body = orchestrate.render_body(body, today=today)
    return model, body, thinking, tools


KNOWN_FIELDS = schema.COHERENCE_FIELDS
FAILURE_KINDS = schema.FAILURE_KINDS


def expected_kind(label_type: object) -> str | None:
    """What a label's error type implies: absence, padding, premise and invented specifics can
    only be checked outside the cited text; wrong entity, scope, quantifier and quote are a
    cited source saying otherwise. A fabricated LINK (LinkE) is either -- the source may give a
    different cause or none at all -- and an unknown or missing type says nothing, so both are
    None (not scored) rather than defaulting to one side."""
    t = label_type.lower() if isinstance(label_type, str) else ""
    if t.startswith(("oute-", "unsupported-", "invented-")):
        return "unsupported"
    if t.startswith(("ente-", "circe-", "quantifier-", "quote-")):
        return "contradicted"
    return None


def _norm(s: str) -> str:
    return "".join(c.lower() for c in (s or "") if c.isalnum())


_CANON_BY_NORM = {_norm(f): f for f in KNOWN_FIELDS}


def _canon_field(f: object) -> str | None:
    """Normalize a model-emitted field name to one of KNOWN_FIELDS, else None."""
    return _CANON_BY_NORM.get(_norm(f)) if isinstance(f, str) else None


def score(report_path: Path, labels: dict) -> dict:
    """Map a coherence report (keyed by headline) to field-level flags and score
    against labels.

    Robust to model-output messiness (the report is model-authored, so the eval
    must surface schema drift rather than silently mis-score):
    - only ``pass is True`` counts as a pass; any other non-``False`` value is
      recorded as ``malformed`` (not silently skipped);
    - ``failed_fields`` omitted or not a list => the whole story is treated as
      dropped (all three fields), mirroring what merge.py does downstream;
    - field names are normalized; an unknown one is recorded as ``malformed``;
    - a missing/empty ``results`` list raises (a broken or wrong-schema report,
      never silently an all-miss).
    ``unmapped`` and ``malformed`` are hard failures the caller gates on.
    """
    hard = {(f["idx"], f["field"]) for f in labels["hard_positives"]}
    border = {(f["idx"], f["field"]) for f in labels["borderline"]}
    clean = {(f["idx"], f["field"]) for f in labels["clean_fields"]}
    h2idx = {h.strip(): int(i) for i, h in labels["idx_headlines"].items()}
    norm2idx = {_norm(h): int(i) for i, h in labels["idx_headlines"].items()}

    data = json.loads(report_path.read_text(encoding="utf-8"))
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list) or not results:
        raise RuntimeError(
            f"coherence report has no non-empty 'results' list (broken run or schema drift): {report_path}"
        )

    flags: set[tuple[int, str]] = set()
    kinds: dict[tuple[int, str], str] = {}
    unmapped: list[str] = []
    malformed: list[str] = []
    for r in results:
        p = r.get("pass") if isinstance(r, dict) else None
        if p is True:
            continue
        if p is not False:
            malformed.append(f"pass={p!r} (headline={r.get('headline')!r})")
            continue
        h = (r.get("headline") or "").strip()
        idx = h2idx.get(h, norm2idx.get(_norm(h)))
        if idx is None:
            unmapped.append(h)
            continue
        ff = r.get("failed_fields")
        if isinstance(ff, list):
            canon = [_canon_field(f) for f in ff]
            if any(c is None for c in canon):
                malformed.append(f"unknown failed_fields {ff!r} (headline={h!r})")
            fields = [c for c in canon if c] or list(KNOWN_FIELDS)
        else:
            # omitted / non-list: merge.py drops the WHOLE story -> mirror that.
            fields = list(KNOWN_FIELDS)
        for field in fields:
            flags.add((idx, field))
        fk = r.get("failure_kinds")
        if isinstance(fk, dict):
            for f, kind in fk.items():
                c = _canon_field(f)
                if c is None or not isinstance(kind, str):
                    malformed.append(f"unknown failure_kinds entry {f!r}: {kind!r} (headline={h!r})")
                    continue
                if kind not in FAILURE_KINDS:
                    malformed.append(f"unknown failure_kinds value {kind!r} (headline={h!r})")
                    continue
                if (idx, c) not in flags:
                    # Crediting a kind on an unflagged field would count a missed positive as agreement.
                    malformed.append(f"failure_kinds names unflagged field {c!r} (headline={h!r})")
                    continue
                kinds[(idx, c)] = kind
    labelled = labels["hard_positives"] + labels.get("borderline", [])
    type_of = {(f["idx"], f["field"]): f.get("type", "") for f in labelled}
    if len(type_of) != len(labelled):
        raise RuntimeError("labels.json lists a field as both hard positive and borderline; its type is ambiguous")
    expected = {k: expected_kind(t) for k, t in type_of.items()}
    kind_agree = sorted(k for k, v in kinds.items() if expected.get(k) is not None and v == expected[k])
    kind_disagree = sorted(
        (k[0], k[1], expected[k], v)
        for k, v in kinds.items()
        if expected.get(k) is not None and v in FAILURE_KINDS and v != expected[k]
    )
    kind_unscored = sorted((k[0], k[1], type_of[k]) for k in kinds if k in type_of and expected.get(k) is None)
    kind_other = sum(1 for k in kinds if k not in type_of)
    return {
        "kinds": {f"{i}:{f}": v for (i, f), v in sorted(kinds.items())},
        "kind_labelled": len(kinds),
        "kind_agree": kind_agree,
        "kind_disagree": kind_disagree,
        "kind_unscored": kind_unscored,
        "kind_other": kind_other,
        "hard_caught": sorted(flags & hard),
        "hard_missed": sorted(hard - flags),
        "border_caught": len(flags & border),
        "false_drops": sorted(flags & clean),
        "unmapped": unmapped,
        "malformed": malformed,
        "n_results": len(results),
        "n_hard": len(hard),
        "n_border": len(border),
        "n_clean": len(clean),
    }


async def run_single_turn_to_file(out_path: Path, model: str, body: str, thinking: dict, fixtures: Path) -> None:
    """Single-turn variant: corpus inlined, no tools, Python writes the report.

    Imports the production builders from orchestrate rather than re-implementing them, so this
    measures the shipped path. The multi-turn arm above mirrors orchestrate instead, which is a
    liability -- a mirror can agree with itself while both drift from production.
    """
    if out_path.exists():
        out_path.unlink()
    corpus = orchestrate.build_coherence_corpus(fixtures)
    res = await claude_cli.run_agent(
        corpus,
        model=model,
        system_prompt=orchestrate.build_single_turn_body(body),
        permission_mode="acceptEdits",
        allowed_tools="",
        tools=[],
        cwd="/app",
        idle_timeout=180.0,
        thinking=thinking,
        max_turns=1,
    )
    if not res.ok:
        raise RuntimeError(f"coherence single-turn run failed: {res.error_summary()}")
    report = orchestrate.parse_coherence_report(res.text)
    if report is None:
        raise RuntimeError("coherence single-turn run returned no parseable report")
    out_path.write_text(json.dumps(report), encoding="utf-8")
    cw = res.usage.get("cache_creation_input_tokens", 0)
    cr = res.usage.get("cache_read_input_tokens", 0)
    print(
        f"  [single-turn] input={res.usage.get('input_tokens', 0)} cache_write={cw} "
        f"cache_read={cr} output={res.usage.get('output_tokens', 0)} cost=${res.total_cost_usd:.4f}"
    )


async def run_per_story_to_file(out_path: Path, model: str, body: str, thinking: dict, fixtures: Path) -> None:
    """Per-story single-turn arm: one call per story, each seeing only its cited sources.

    Results are merged in draft order so score() maps them like the multi-turn report.
    At most 4 stories in flight, the same width as the WRITE fan-out.
    """
    if out_path.exists():
        out_path.unlink()
    draft = json.loads((fixtures / "draft_selections.json").read_text(encoding="utf-8"))
    stories = [s for tier in ("must_know", "should_know") for s in draft.get(tier, [])]
    system_prompt = orchestrate.build_per_story_body(body)
    sem = asyncio.Semaphore(4)
    totals = {"input": 0, "cache_write": 0, "cache_read": 0, "output": 0, "cost": 0.0}

    async def one(story: dict) -> dict:
        async with sem:
            res = await claude_cli.run_agent(
                orchestrate.build_story_corpus(fixtures, story),
                model=model,
                system_prompt=system_prompt,
                permission_mode="acceptEdits",
                allowed_tools="",
                tools=[],
                cwd="/app",
                idle_timeout=180.0,
                thinking=thinking,
                max_turns=1,
            )
        if not res.ok:
            raise RuntimeError(f"per-story run failed on {story.get('headline')!r}: {res.error_summary()}")
        report = orchestrate.parse_coherence_report(res.text)
        results = (report or {}).get("results") or []
        if len(results) != 1:
            raise RuntimeError(f"per-story run returned {len(results)} results for {story.get('headline')!r}")
        u = res.usage or {}
        totals["input"] += u.get("input_tokens", 0)
        totals["cache_write"] += u.get("cache_creation_input_tokens", 0)
        totals["cache_read"] += u.get("cache_read_input_tokens", 0)
        totals["output"] += u.get("output_tokens", 0)
        totals["cost"] += res.total_cost_usd or 0.0
        return results[0]

    merged = await asyncio.gather(*(one(s) for s in stories))
    out_path.write_text(json.dumps({"results": list(merged)}), encoding="utf-8")
    print(
        f"  [per-story x{len(stories)}] input={totals['input']} cache_write={totals['cache_write']} "
        f"cache_read={totals['cache_read']} output={totals['output']} cost=${totals['cost']:.4f}"
    )


ARMS = ("tools", "read-text", "read-schema", "inline-grep")


def _report_from(res) -> tuple[dict | None, str]:
    """The report and where it came from: the schema-parsed final message, or text parsed in code."""
    so = getattr(res, "structured_output", None)
    if isinstance(so, dict) and isinstance(so.get("results"), list):
        return so, "structured_output"
    return orchestrate.parse_coherence_report(res.text), "text"


def _count(res, name: str) -> int:
    return sum(1 for n, _ in (getattr(res, "tool_calls", None) or ()) if n == name)


def _tier_of(fixtures: Path) -> dict[str, str]:
    """headline -> tier from the draft, so a story dropped without failed_fields is counted at its
    own field count (briefs carry no why_it_matters by design)."""
    try:
        draft = json.loads((fixtures / "draft_selections.json").read_text(encoding="utf-8"))
    except OSError, ValueError:
        return {}
    out: dict[str, str] = {}
    for tier in ("must_know", "should_know"):
        for st in draft.get(tier) or []:
            if isinstance(st, dict) and isinstance(st.get("headline"), str):
                out[_norm(st["headline"])] = tier
    return out


def failed_field_count(report: dict, fixtures: Path) -> int:
    tiers = _tier_of(fixtures)
    n = 0
    for r in report.get("results") or []:
        if not (isinstance(r, dict) and r.get("pass") is False):
            continue
        ff = r.get("failed_fields")
        if isinstance(ff, list) and ff:
            n += len(ff)
        else:
            n += 2 if tiers.get(_norm(r.get("headline") or "")) == "should_know" else len(KNOWN_FIELDS)
    return n


_MIN_PATTERN = 4


def unbacked_fail_count(report: dict, tool_calls) -> int:
    """Failing entries whose reason does not contain any Grep pattern of the accepted attempt.

    The report lands in the final message, so positional attribution is impossible; a Grep
    pattern that names the specific the reason quotes is the cheap attribution the transcript
    does allow. Substring only, patterns shorter than 4 characters ignored, no token union: the
    earlier token-overlap version let one Grep for "kill" back every failure in the report.
    Still approximate (a Grep for "source" backs any reason that says "source"), so it is
    reported, never gated."""
    raw = [
        t.strip().lower()
        for name, t in (tool_calls or ())
        if name == "Grep" and isinstance(t, str) and len(t.strip()) >= _MIN_PATTERN
    ]
    return sum(
        1
        for r in report.get("results") or []
        if isinstance(r, dict) and r.get("pass") is False and not any(p in (r.get("reason") or "").lower() for p in raw)
    )


async def run_arm_to_file(arm: str, out_path: Path, model: str, body: str, thinking: dict, fixtures: Path) -> dict:
    """One rep of an I/O-shape arm; the report lands at out_path and the metrics come back.

    read-text:   Read only; the report is the final message, parsed in code.
    read-schema: Read only; the final message is constrained by COHERENCE_REPORT_SCHEMA.
    inline-grep: corpus inline in the user turn AND on disk; Grep + Read to re-check before a FAIL.

    An unparseable reply is a fresh attempt (once), and every attempt is paid for: the metric the
    arms are compared on is cost per ACCEPTED report, retries included.
    """
    if arm not in ARMS or arm == "tools":
        raise ValueError(f"unknown arm {arm!r}; run_agent_to_file handles the shipped tool loop")
    if arm == "inline-grep":
        prompt = orchestrate.build_coherence_corpus(fixtures)
        system_prompt = orchestrate.build_inline_grep_body(body, str(fixtures))
        tools = ["Read", "Grep"]
        output_format = None
    else:
        prompt = "Begin."
        system_prompt = orchestrate.build_read_only_body(body)
        tools = ["Read"]
        output_format = (
            {"type": "json_schema", "schema": schema.COHERENCE_REPORT_SCHEMA} if arm == "read-schema" else None
        )

    if out_path.exists():
        out_path.unlink()
    m = {
        "arm": arm,
        "attempts": 0,
        "cost": 0.0,
        "duration_ms": 0,
        "input": 0,
        "cache_write": 0,
        "cache_read": 0,
        "output": 0,
        "reads": 0,
        "greps": 0,
        "writes": 0,
    }
    report = None
    accepted = None
    for attempt in (1, 2):
        res = await claude_cli.run_agent(
            prompt,
            model=model,
            system_prompt=system_prompt,
            permission_mode="acceptEdits",
            allowed_tools=" ".join(tools),
            tools=tools,
            cwd="/app",
            idle_timeout=180.0,
            thinking=thinking,
            output_format=output_format,
        )
        u = res.usage or {}
        m["attempts"] = attempt
        # Cost and tokens sum across attempts: every attempt is paid for.
        m["cost"] += res.total_cost_usd or 0.0
        m["duration_ms"] += getattr(res, "duration_ms", 0) or 0
        m["input"] += u.get("input_tokens", 0)
        m["cache_write"] += u.get("cache_creation_input_tokens", 0)
        m["cache_read"] += u.get("cache_read_input_tokens", 0)
        m["output"] += u.get("output_tokens", 0)
        if not res.ok:
            raise RuntimeError(f"{arm} run failed: {res.error_summary()}")
        report, m["report_source"] = _report_from(res)
        if report is not None:
            accepted = res
            break
        print(f"  [{arm}] attempt {attempt}: no parseable report" + (", retrying once" if attempt == 1 else ""))
    if report is None or accepted is None:
        raise RuntimeError(f"{arm}: no parseable report after 2 attempts")
    # Tool counts and the grep rule describe the ACCEPTED attempt only: a discarded attempt's
    # greps must not launder a FAIL the accepted report made without one.
    m["reads"] = _count(accepted, "Read")
    m["greps"] = _count(accepted, "Grep")
    m["writes"] = _count(accepted, "Write")
    out_path.write_text(json.dumps(report), encoding="utf-8")
    m["failed_fields"] = failed_field_count(report, fixtures)
    m["unbacked_fails"] = unbacked_fail_count(report, accepted.tool_calls) if arm == "inline-grep" else None
    print(
        f"  [{arm}] attempts={m['attempts']} input={m['input']} cache_write={m['cache_write']} "
        f"cache_read={m['cache_read']} output={m['output']} cost=${m['cost']:.4f} "
        f"reads={m['reads']} greps={m['greps']} writes={m['writes']} failed_fields={m['failed_fields']} "
        f"unbacked_fails={m['unbacked_fails']} report_source={m['report_source']}"
    )
    return m


def _metrics_from(res) -> dict:
    """Usage, cost and tool-call counts of one StageResult (tolerant: a test double may carry none)."""
    u = getattr(res, "usage", None) or {}
    calls = getattr(res, "tool_calls", None) or ()
    return {
        "attempts": 1,
        "cost": getattr(res, "total_cost_usd", 0.0) or 0.0,
        "duration_ms": getattr(res, "duration_ms", 0) or 0,
        "input": u.get("input_tokens", 0),
        "cache_write": u.get("cache_creation_input_tokens", 0),
        "cache_read": u.get("cache_read_input_tokens", 0),
        "output": u.get("output_tokens", 0),
        "reads": sum(1 for n, _ in calls if n == "Read"),
        "greps": sum(1 for n, _ in calls if n == "Grep"),
        "writes": sum(1 for n, _ in calls if n == "Write"),
    }


async def run_agent_to_file(label: str, out_path: Path, model: str, body: str, thinking: dict, tools: list[str]):
    """Run an agent through the production claude-agent-sdk path and require it to
    (re)write out_path. Shared by the coherence/repair evals so both exercise the
    exact same harness (model, system prompt, tools, thinking) production uses."""
    # One clean retry, as production's run_stage gives a stage: a rep that hangs is a lost
    # call, not a measurement, and without this it took every later rep with it.
    for attempt in (1, 2):
        if out_path.exists():
            out_path.unlink()
        try:
            res = await claude_cli.run_agent(
                "Begin.",
                model=model,
                system_prompt=body,
                permission_mode="acceptEdits",
                allowed_tools=" ".join(tools),
                tools=tools,
                cwd="/app",
                idle_timeout=180.0,
                thinking=thinking,
            )
            if not res.ok:
                raise RuntimeError(f"{label} run failed: {res.error_summary()}")
            if not out_path.exists():
                raise RuntimeError(f"{label} run wrote no {out_path.name}")
            return res
        except RuntimeError as e:
            if attempt == 2:
                raise
            print(f"  {label}: attempt 1 failed ({e}), retrying once")
    raise AssertionError("unreachable")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", default=str(AGENT))
    ap.add_argument("--fixtures", default=str(FIXTURES))
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--single-turn", action="store_true", help="inline the corpus, tools=[], parse result.text")
    ap.add_argument("--per-story", action="store_true", help="one single-turn call per story, cited sources only")
    ap.add_argument("--arm", choices=ARMS, default="tools", help="I/O-shape arm (default: the shipped tool loop)")
    ap.add_argument("--model", default=None, help="override coherence.md's frontmatter model")
    ap.add_argument(
        "--today",
        default=None,
        help="ISO date to anchor {{CURRENT_DATE}} to (default: UTC today). Set it to the date of "
        "the run a fixture came from; the checker is told to judge world state from it.",
    )
    args = ap.parse_args()

    runs = max(1, args.runs)
    fixtures = Path(args.fixtures)
    labels = json.loads((fixtures / "labels.json").read_text(encoding="utf-8"))
    today = datetime.date.fromisoformat(args.today) if args.today else None
    model, body, thinking, tools = load_agent_for_eval(Path(args.agent), fixtures, args.model, today=today)

    print(f"COHERENCE eval  model={model}  thinking={thinking['type']}  runs={runs}  fixtures={fixtures.name}")
    print(
        f"  labels: {len(labels['hard_positives'])} hard, {len(labels['borderline'])} borderline, "
        f"{len(labels['clean_fields'])} clean\n"
    )

    if (args.single_turn or args.per_story) and args.arm != "tools":
        ap.error("--single-turn/--per-story are arms of their own; do not combine them with --arm")
    arm_label = "per-story" if args.per_story else ("single-turn" if args.single_turn else args.arm)

    scores = []
    for i in range(runs):
        metrics: dict = {"arm": arm_label}
        if args.per_story:
            asyncio.run(run_per_story_to_file(fixtures / REPORT_NAME, model, body, thinking, fixtures))
        elif args.single_turn:
            asyncio.run(run_single_turn_to_file(fixtures / REPORT_NAME, model, body, thinking, fixtures))
        elif args.arm != "tools":
            metrics = asyncio.run(run_arm_to_file(args.arm, fixtures / REPORT_NAME, model, body, thinking, fixtures))
        else:
            res = asyncio.run(run_agent_to_file("coherence", fixtures / REPORT_NAME, model, body, thinking, tools))
            metrics.update(_metrics_from(res))
            metrics["report_source"] = "file"
            try:
                metrics["failed_fields"] = failed_field_count(
                    json.loads((fixtures / REPORT_NAME).read_text(encoding="utf-8")), fixtures
                )
            except OSError, ValueError:
                metrics["failed_fields"] = None
            print(
                f"  [tools] cost=${metrics['cost']:.4f} reads={metrics['reads']} greps={metrics['greps']} "
                f"writes={metrics['writes']} failed_fields={metrics['failed_fields']}"
            )
        # Keep every run's report: the 2026-09-03 per-story measurement lost the reasons behind
        # three false drops because each run overwrote the last.
        tag = str(i) if arm_label == "tools" else f"{arm_label}.{i}"
        shutil.copyfile(fixtures / REPORT_NAME, fixtures / f"coherence_report.{tag}.json")
        s = score(fixtures / REPORT_NAME, labels)
        s["metrics"] = metrics
        (fixtures / f"score.{tag}.json").write_text(json.dumps(s, default=str), encoding="utf-8")
        scores.append(s)
        print(
            f"  run {i}: recall {len(s['hard_caught'])}/{s['n_hard']}  "
            f"false-drops {len(s['false_drops'])}/{s['n_clean']}  "
            f"borderline {s['border_caught']}/{s['n_border']}  "
            f"unmapped {len(s['unmapped'])}  malformed {len(s['malformed'])}"
        )
        if s["hard_missed"]:
            print(f"          missed: {s['hard_missed']}")
        if s["false_drops"]:
            print(f"          FALSE DROPS: {s['false_drops']}")
        if s["unmapped"]:
            print(f"          UNMAPPED headlines: {s['unmapped']}")
        if s["malformed"]:
            print(f"          MALFORMED entries: {s['malformed']}")
        if s["kind_labelled"]:
            print(
                f"          kinds: {s['kinds']}\n"
                f"          kind agreement on hard positives: {len(s['kind_agree'])} agree, "
                f"{len(s['kind_disagree'])} disagree {s['kind_disagree']}, "
                f"{len(s['kind_unscored'])} unscored (label type not mapped) {s['kind_unscored']}, "
                f"{s['kind_other']} on unlabelled fields"
            )

    best_recall = max(len(s["hard_caught"]) for s in scores)
    worst_fd = max(len(s["false_drops"]) for s in scores)
    print(f"\n  best recall {best_recall}/{scores[0]['n_hard']}  worst false-drops {worst_fd}")

    # Egregious-regression gate (stochastic stage -> soft on recall in the middle,
    # hard on schema drift and precision).
    fail = []
    if any(s["unmapped"] for s in scores):
        fail.append("a report headline did not map to a labelled story (fixtures drifted?)")
    if any(s["malformed"] for s in scores):
        fail.append("a report entry had a malformed pass/failed_fields/failure_kinds shape (schema drift?)")
    if worst_fd > 2:
        fail.append(f"false-drops {worst_fd} > 2 (precision regression)")
    if best_recall == 0:
        fail.append("recall 0 on every run (detector is a no-op)")
    if fail:
        print("\n  REGRESSION: " + "; ".join(fail))
        return 1
    print("\n  OK (no egregious regression; review the scorecard for recall changes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
