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
import json
from pathlib import Path

import claude_cli  # /app/src

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
    agent_path: Path, fixtures: Path, model_override: str | None = None
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
    return model, body, thinking, tools


KNOWN_FIELDS = ("headline", "summary", "why_it_matters")


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
    return {
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


async def run_agent_to_file(
    label: str, out_path: Path, model: str, body: str, thinking: dict, tools: list[str]
) -> None:
    """Run an agent through the production claude-agent-sdk path and require it to
    (re)write out_path. Shared by the coherence/repair evals so both exercise the
    exact same harness (model, system prompt, tools, thinking) production uses."""
    if out_path.exists():
        out_path.unlink()
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", default=str(AGENT))
    ap.add_argument("--fixtures", default=str(FIXTURES))
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--model", default=None, help="override coherence.md's frontmatter model")
    args = ap.parse_args()

    runs = max(1, args.runs)
    fixtures = Path(args.fixtures)
    labels = json.loads((fixtures / "labels.json").read_text(encoding="utf-8"))
    model, body, thinking, tools = load_agent_for_eval(Path(args.agent), fixtures, args.model)

    print(f"COHERENCE eval  model={model}  thinking={thinking['type']}  runs={runs}  fixtures={fixtures.name}")
    print(
        f"  labels: {len(labels['hard_positives'])} hard, {len(labels['borderline'])} borderline, "
        f"{len(labels['clean_fields'])} clean\n"
    )

    scores = []
    for i in range(runs):
        asyncio.run(run_agent_to_file("coherence", fixtures / REPORT_NAME, model, body, thinking, tools))
        s = score(fixtures / REPORT_NAME, labels)
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

    best_recall = max(len(s["hard_caught"]) for s in scores)
    worst_fd = max(len(s["false_drops"]) for s in scores)
    print(f"\n  best recall {best_recall}/{scores[0]['n_hard']}  worst false-drops {worst_fd}")

    # Egregious-regression gate (stochastic stage -> soft on recall in the middle,
    # hard on schema drift and precision).
    fail = []
    if any(s["unmapped"] for s in scores):
        fail.append("a report headline did not map to a labelled story (fixtures drifted?)")
    if any(s["malformed"] for s in scores):
        fail.append("a report entry had a malformed pass/failed_fields shape (schema drift?)")
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
