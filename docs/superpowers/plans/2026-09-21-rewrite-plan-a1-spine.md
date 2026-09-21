# Rewrite plan A1: prerequisites and the TypeScript-on-Temporal spine

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. One task per commit; the repo's review gate requires a reviewer run after every commit (`feature-dev:code-reviewer` for mechanical tasks; `adversarial-reviewer` for tasks 1, 2, 7, 8, 9b, 12b).

Revision 2, after an adversarial review of revision 1 (15 findings, all applied; the dispositions are at the end).

**Goal:** Land everything plan A's fan-out depends on: the two Python-side prerequisites the spec orders first, then a TypeScript package that runs a `DigestWorkflow` end to end on a local Temporal with stub activities, a real artifact store, a real stage runner, and the gate harness.

**Architecture:** The pipeline becomes one Temporal workflow whose activities persist blobs to SQLite and return pointers; the model-calling activity wraps the TypeScript Agent SDK with tools scoped per stage and the result returned as the final message. This plan builds the spine (contracts, store, runner, workflow skeleton, local Temporal, gate harness); plan A2 ports the stages onto it one activity at a time.

**Tech Stack:** Node 22, TypeScript 5 strict, `@temporalio/{workflow,worker,client,common,testing}` 1.24, `@anthropic-ai/claude-agent-sdk` 0.3.278 (bundles the CLI; subscription login inherited), `better-sqlite3` 13, `zod` 4, `vitest` 5, `promptfoo` 0.123, Docker Compose for Temporal 1.32.0 + Postgres 16. Python side: the existing `newsroom/` package.

**Spec:** `docs/superpowers/specs/2026-09-21-four-systems-rewrite-design.md` (read §1, §2.1, §2.2, §7, §8 before starting).

## Global Constraints

- **Python tests run in Docker with the entrypoint overridden**, because the `ci` service's entrypoint is `bin/ci` and ignores pytest arguments: `docker compose run --rm --entrypoint python3 ci -m pytest -q <path>[::test]` (verified: runs only the named tests). `bin/ci` (the pre-commit hook, ~90 s) runs everything.
- Frozen contracts (spec §1): article ids `A{n}`; no URL reaches a model stage; `schema.SELECTIONS_SCHEMA` and `schema.COHERENCE_REPORT_SCHEMA` shapes **exactly as `newsroom/src/schema.py` states them** (a `source` is `{article_id}` only; `why_it_matters` is tolerated on a brief); fail-closed; the parsing boundary; one UTC run date read once.
- New TypeScript code lives under `digest/` at the repo root: `digest/src/{contracts,store,runner,workflow,activities,gate,cli}`, tests beside sources as `*.test.ts`. No `digest/` file imports from `newsroom/`.
- **What this plan changes in production, stated exactly:** task 1 changes what a production run *records* (four more artifacts; the spec's A2 decision). Task 3 moves three prompt texts from Python constants to files read at call time. Nothing else touches a production path; the TypeScript package is not deployed by this plan.
- Every TypeScript library API name used in tasks 7 to 12 is verified against the installed `.d.ts` files in task 5 before any of those tasks starts; a name that is not there is corrected in this plan first.
- Library gate: `still_active --sbom=<cyclonedx of digest/package-lock.json> --fail-if-critical` passes before task 6 (spec §8.4).
- No deletion of Python in this plan. WAL stays off (spec §5).
- Model calls only in tasks 1 (step 7), 2 (step 8), 8 (step 7) and 12b (step 5), each opt-in with its cost stated, never in CI.
- Every agent definition pins a model. Sonnet for mechanical tasks, Opus for reviews.
- Commit messages carry rationale (why), not narrative (what), plus the session's attribution lines.

---

### Task 1: Archive the repair path's outputs and measure the recheck band (spec §8.2, known gap A2)

**Files:**
- Modify: `newsroom/src/db.py:768-800` (`_TRACE_ARTIFACTS`)
- Modify: `newsroom/tests/test_archive_closure.py:36-45` (`UNARCHIVED_REPAIR_OUTPUTS`)
- Test: `newsroom/tests/test_db.py`

**Interfaces:**
- Consumes: `db.archive_run_artifacts(claude_input_dir: Path, models=None)` (archives every name in `_TRACE_ARTIFACTS` that exists, plus `articles_*.csv`, for the run in `db._state`); `db.get_run_artifacts(run_id) -> dict[str, str]`; the `fresh_db` fixture in `test_db.py` (temp DB with migrations and a started run).
- Produces: four more rows per run in `run_artifacts` when the repair path ran: `repaired_fields.json`, `recheck_draft.json`, `recheck_report.json`, `repair_resolution.json`.

- [ ] **Step 1: Write the failing test**

Append to `newsroom/tests/test_db.py` (it already imports `db`, `json`, `sqlite3` and defines `fresh_db`):

```python
def test_archive_run_artifacts_keeps_the_repair_paths_outputs(fresh_db, tmp_path):
    """Probe 0 gap A2: run 303 dropped two repaired stories and the recheck verdict could not be
    read back, because none of the repair path's four outputs was archived."""
    d = tmp_path / "claude_input"
    d.mkdir()
    for name in ("repaired_fields.json", "recheck_draft.json", "recheck_report.json", "repair_resolution.json"):
        (d / name).write_text(json.dumps({"from": name}), encoding="utf-8")
    db.archive_run_artifacts(d)
    arts = db.get_run_artifacts(db._state.run_id)
    assert {"repaired_fields.json", "recheck_draft.json", "recheck_report.json", "repair_resolution.json"} <= set(arts)
    assert json.loads(arts["recheck_report.json"]) == {"from": "recheck_report.json"}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `docker compose run --rm --entrypoint python3 ci -m pytest -q newsroom/tests/test_db.py -k keeps_the_repair_paths_outputs`
Expected: FAIL on the subset assertion (the four names are not archived).

- [ ] **Step 3: Add the four names to `_TRACE_ARTIFACTS`**

In `newsroom/src/db.py`, inside the `_TRACE_ARTIFACTS` tuple, after `"fulltext_health.json"`:

```python
    # The repair path's model outputs. Not derivable (re-deriving means paying the model again),
    # and the recheck verdict was the one thing run 303's two drops could not be read back from.
    "repaired_fields.json",
    "recheck_draft.json",
    "recheck_report.json",
    "repair_resolution.json",
```

- [ ] **Step 4: Move the four names out of `UNARCHIVED_REPAIR_OUTPUTS`**

In `newsroom/tests/test_archive_closure.py` replace the `UNARCHIVED_REPAIR_OUTPUTS = {...}` block with:

```python
# Closed: the repair path's four model outputs are archived (db._TRACE_ARTIFACTS). The dict stays so
# DERIVED_IN_PROCESS below keeps its shape.
UNARCHIVED_REPAIR_OUTPUTS: dict[str, str] = {}
```

- [ ] **Step 5: Run the two test files**

Run: `docker compose run --rm --entrypoint python3 ci -m pytest -q newsroom/tests/test_db.py newsroom/tests/test_archive_closure.py`
Expected: PASS. If `test_archive_closure` reports a prompt-named file that is neither archived nor derived, the list is wrong, not the test.

- [ ] **Step 6: Commit**

```bash
git add newsroom/src/db.py newsroom/tests/test_db.py newsroom/tests/test_archive_closure.py
git commit -m "feat(archive): the repair path's four outputs are archived; a dropped-after-repair story is readable again"
```

- [ ] **Step 7: Measure the recheck band (model calls, ~$5, ~20 min; opt-in; spec §8.2)**

Run: `bin/eval-repair --recheck-runs 3` (read `bin/eval-repair` and `newsroom/src/eval_repair.py:260-330` for the flag's exact form; it unions N recheck passes on the `coherence_faithful` fixture). Copy the printed per-pass kept/dropped counts into `docs/proposed/2026-09-2x-recheck-band/README.md` with the command used. Commit as `docs(eval): the recheck's own band, three passes`.

---

### Task 2: A curation re-run on archived inputs, with model calls, for the same-day band (spec §7.4, §8.3)

**Files:**
- Create: `newsroom/src/rerun_run.py`, `bin/rerun-run`
- Test: `newsroom/tests/test_rerun_run.py`

**Interfaces:**
- Consumes (real signatures, read them): `db.get_run_artifacts(run) -> dict[str, str]`; `orchestrate.orchestrate_selections(*, claude_input_dir: Path, model_override=None, cwd=None, resume=False, on_usage: Callable[[dict], None] | None = None, today: datetime.date | None = None) -> list[dict]` (keyword-only; runs CLUSTER, RECAP, SELECT, WRITE, COHERENCE and the best-effort repair phase; `on_usage` receives each usage row as its stage completes); `merge.assemble_selections(claude_input_dir: Path) -> Path` (writes `selections.json`); `rerun_stage._run_date(run) -> date | None`.
- Produces: `rerun_run.restore_inputs(archive, into) -> list[str]`, `rerun_run.summarise(run, reps) -> dict`, `rerun_run.rerun(run, *, reps, work) -> dict`; `work/rep<i>/` (the input tree after each rep) and `work/summary.json` with `cost_usd`, `wall_s`, `stories` per rep.
- **What this measures, stated:** the curation span (orchestrate + merge), which is the model-bound 19 of the run's 20 minutes. Fetch (12 s), fulltext (44 s), threads and render (~4 min on run 303) are outside `orchestrate_selections`; the new system's comparable number is the same span of workflow time, and the whole-run comparison adds those measured constants from the journal.

- [ ] **Step 1: Write the failing test**

```python
# newsroom/tests/test_rerun_run.py
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import rerun_run


def test_restore_writes_every_archived_input_and_never_a_stage_output(tmp_path):
    archive = {
        "articles_1.csv": "article_id,title\nA1,x\n",
        "sources.csv": "id,name\nreuters,Reuters\n",
        "weekly_recap.txt": "recap",
        "recent_rss_titles.csv": "t\n",
        "yesterday_headlines.txt": "",
        "recent_digest_headlines.txt": "",
        "clusters.json": '{"stale": true}',
        "selections.json": '{"stale": true}',
        "thinking_write_s00.txt": "reasoning",
    }
    written = rerun_run.restore_inputs(archive, tmp_path)
    assert (tmp_path / "articles_1.csv").exists() and (tmp_path / "sources.csv").exists()
    assert not (tmp_path / "clusters.json").exists() and not (tmp_path / "selections.json").exists()
    assert not (tmp_path / "thinking_write_s00.txt").exists()
    assert sorted(written) == ["articles_1.csv", "recent_digest_headlines.txt", "recent_rss_titles.csv", "sources.csv", "weekly_recap.txt", "yesterday_headlines.txt"]


def test_summary_carries_the_band_fields():
    reps = [{"cost_usd": 5.0, "wall_s": 1200.0, "stories": 16}, {"cost_usd": 5.5, "wall_s": 1300.0, "stories": 15}]
    s = rerun_run.summarise(300, reps)
    assert s == {"run": 300, "reps": 2, "cost_usd": [5.0, 5.5], "wall_s": [1200.0, 1300.0], "stories": [16, 15]}


def test_one_rep_drives_orchestrate_then_merge_and_sums_usage(tmp_path, monkeypatch):
    calls: list[str] = []

    async def fake_orchestrate(*, claude_input_dir, on_usage, today, **_):
        calls.append("orchestrate")
        on_usage({"api_cost_usd": 1.25})
        on_usage({"api_cost_usd": 0.75})
        return []

    def fake_assemble(claude_input_dir):
        calls.append("merge")
        p = claude_input_dir / "selections.json"
        p.write_text(json.dumps({"must_know": [{"headline": "a"}], "should_know": [{"headline": "b"}]}))
        return p

    monkeypatch.setattr(rerun_run.orchestrate, "orchestrate_selections", fake_orchestrate)
    monkeypatch.setattr(rerun_run.merge, "assemble_selections", fake_assemble)
    import asyncio

    r = asyncio.run(rerun_run._one_rep({"sources.csv": "id\n"}, tmp_path / "rep0", None))
    assert calls == ["orchestrate", "merge"]
    assert r["cost_usd"] == 2.0 and r["stories"] == 2 and r["wall_s"] >= 0
```

- [ ] **Step 2: Run it to verify it fails**

Run: `docker compose run --rm --entrypoint python3 ci -m pytest -q newsroom/tests/test_rerun_run.py`
Expected: FAIL, `ModuleNotFoundError: rerun_run`.

- [ ] **Step 3: Write `newsroom/src/rerun_run.py`**

```python
"""Re-run a run's curation span from its archived inputs, with model calls, N times.

The gate's speed-and-cost band (spec §7.4) needs the OLD system's same-day spread, and replay.py
makes no model calls. This restores every archived input of run N into a scratch input dir and
drives orchestrate.orchestrate_selections (CLUSTER..COHERENCE + repair) then merge.assemble_selections
over it with the run's date pinned. Fetch, fulltext, threads and render are outside this span.
Usage (in the container, see bin/rerun-run): rerun_run.py --run 301 --reps 3
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import shutil
import time
from pathlib import Path

import config
import db
import merge
import orchestrate
import rerun_stage

WORK = Path("/app/data/rerun-run")

# Stage outputs the archive also holds; a re-run must produce these, never read them.
STAGE_OUTPUTS = frozenset(
    {
        "clusters.json", "cluster_tags.json", "cluster_health.json", "recap.txt", "selected.json",
        "article_fulltext.json", "fulltext_health.json", "cluster_cohesion.json", "draft_selections.json",
        "write_branches.json", "preheader.txt", "coherence_report.json", "repaired_fields.json",
        "recheck_draft.json", "recheck_report.json", "repair_resolution.json", "selections.json",
        "thread_assignments.json", "thread_links.json", "models.json",
    }
)


def restore_inputs(archive: dict[str, str], into: Path) -> list[str]:
    into.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for name, content in archive.items():
        if name in STAGE_OUTPUTS or name.startswith("thinking_"):
            continue
        (into / name).write_text(content, encoding="utf-8")
        written.append(name)
    return written


def summarise(run: int, reps: list[dict]) -> dict:
    return {
        "run": run,
        "reps": len(reps),
        "cost_usd": [r["cost_usd"] for r in reps],
        "wall_s": [r["wall_s"] for r in reps],
        "stories": [r["stories"] for r in reps],
    }


async def _one_rep(archive: dict[str, str], work: Path, today: datetime.date | None) -> dict:
    restore_inputs(archive, work)
    rows: list[dict] = []
    t0 = time.monotonic()
    await orchestrate.orchestrate_selections(claude_input_dir=work, on_usage=rows.append, today=today)
    selections_path = merge.assemble_selections(work)
    wall = time.monotonic() - t0
    selections = json.loads(selections_path.read_text(encoding="utf-8"))
    stories = len(selections.get("must_know", [])) + len(selections.get("should_know", []))
    cost = round(sum(float(r.get("api_cost_usd") or 0.0) for r in rows), 4)
    return {"cost_usd": cost, "wall_s": round(wall, 1), "stories": stories}


async def rerun(run: int, *, reps: int, work: Path) -> dict:
    archive = db.get_run_artifacts(run)
    if "sources.csv" not in archive or "weekly_recap.txt" not in archive:
        raise SystemExit(f"run {run} predates the closed archive (2026-09-18); pick run >= 300")
    today = rerun_stage._run_date(run)
    results: list[dict] = []
    for i in range(reps):
        rep_dir = work / f"rep{i}"
        if rep_dir.exists():
            shutil.rmtree(rep_dir)
        results.append(await _one_rep(archive, rep_dir, today))
        print(f"  rep {i}: {results[-1]}")
    summary = summarise(run, results)
    work.mkdir(parents=True, exist_ok=True)
    (work / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", type=int, required=True)
    ap.add_argument("--reps", type=int, default=3)
    args = ap.parse_args()
    if db.current_db_path() is None:
        db.init(config.DB_PATH, config.MIGRATIONS_DIR, apply_migrations=False)
    summary = asyncio.run(rerun(args.run, reps=max(1, args.reps), work=WORK / f"run{args.run}"))
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

`orchestrate_selections` records usage to the DB only when `on_usage` is None (read `orchestrate.py` near line 1555 to confirm before relying on it); with the callback given, nothing is written to `run_usage`. The `shutil.rmtree` is on a directory this module created under `WORK/run<N>/rep<i>` in the container's data volume; it never receives a caller-supplied path.

- [ ] **Step 4: Write `bin/rerun-run`**

```bash
#!/bin/bash
# Re-run a run's curation span from its archived inputs, with model calls, N times: the old
# system's same-day cost/time band (spec §7.4). Opt-in, never in CI. ~$5 and ~20 min per rep.
# Usage: bin/rerun-run --run 301 [--reps 3]
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
exec docker compose run --rm \
  -v "$(pwd)/newsroom/src:/app/src" \
  -v "$(pwd)/.claude/agents:/app/.claude/agents" \
  -e PYTHONPATH=/app/src \
  --entrypoint /app/.venv/bin/python3 \
  digest-newsroom /app/src/rerun_run.py "$@"
```

Then `chmod +x bin/rerun-run`.

- [ ] **Step 5: Run the tests**

Run: `docker compose run --rm --entrypoint python3 ci -m pytest -q newsroom/tests/test_rerun_run.py`
Expected: PASS.

- [ ] **Step 6: Check the module layering**

`rerun_run` imports `merge`, `orchestrate`, `rerun_stage`, `db`, `config`. `CLAUDE.md`'s layering puts `merge` below `orchestrate` and `rerun_stage` at the eval level; a new eval-level module may import all of them. Confirm no module below imports `rerun_run` (`rg -n "import rerun_run" newsroom/src` returns nothing).

- [ ] **Step 7: Commit**

```bash
git add newsroom/src/rerun_run.py newsroom/tests/test_rerun_run.py bin/rerun-run
git commit -m "feat(eval): re-run a run's curation span on archived inputs with model calls; the same-day band the gate needs"
```

- [ ] **Step 8: Measure the band (model calls, ~$15, ~1 h; opt-in)**

Run: `bin/rerun-run --run 301 --reps 3`
Copy `data/rerun-run/run301/summary.json` to `docs/proposed/2026-09-2x-same-day-band/summary.json` with a README stating the three costs and wall clocks, the run's original `run_usage` total, and the non-model constants from the journal that a whole-run comparison adds (fetch, fulltext, threads, render). Commit as `docs(eval): the old system's same-day curation band on run 301`.

---

### Task 3: The three prompts embedded in Python become files (spec §1, prompts row)

**Files:**
- Create: `newsroom/src/prompts.py` (leaf module: no internal imports), `.claude/agents/thread-synthesis.md`, `.claude/agents/thread-audit.md`, `.claude/agents/cohesion.md`, `.claude/agents/cluster-extract.md`
- Modify: `newsroom/src/thread_synthesis.py` (`AUDIT_SYSTEM` at line 80 and the synthesis system prompt passed near line 295), `newsroom/src/cohesion.py:43` (`JUDGE_SYSTEM`), `newsroom/src/cluster_extractjoin.py` (`EXTRACT_SYSTEM`)
- Modify: `CLAUDE.md` module layering (add `prompts` to the leaf line)
- Test: `newsroom/tests/test_prompts.py`

**Interfaces:**
- Produces: `prompts.load_prompt_text(name: str) -> str`: the body of `.claude/agents/<name>.md` with the `---` frontmatter stripped, read **at call time** from `prompts.AGENTS_DIR` (`Path(os.environ.get("AGENTS_DIR", ".claude/agents"))`, the same cwd-relative default `orchestrate._AGENTS_DIR` uses, so production and the container agree). The three modules expose functions `judge_system()`, `audit_system()`, `synthesis_system()`, `extract_system()` that call it; the constants are removed.

Why not `orchestrate.load_prompt_text`: `orchestrate` imports `cohesion` and `cluster_extractjoin`, so those modules cannot import `orchestrate` (cycle), and `config` is declared a leaf that may not import `orchestrate`'s parser. `prompts.py` is a second, tiny frontmatter strip; the test below holds it equal to `orchestrate.parse_agent_spec(...).body` on every shipped agent file so the two cannot drift.

- [ ] **Step 1: Write the failing test**

```python
# newsroom/tests/test_prompts.py
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import cluster_extractjoin
import cohesion
import orchestrate
import prompts
import thread_synthesis

AGENTS = Path(__file__).resolve().parents[2] / ".claude" / "agents"


@pytest.fixture(autouse=True)
def _agents_dir(monkeypatch):
    monkeypatch.setattr(prompts, "AGENTS_DIR", AGENTS)


def test_every_model_stage_prompt_is_a_file():
    for name in ("thread-synthesis", "thread-audit", "cohesion", "cluster-extract"):
        assert (AGENTS / f"{name}.md").exists(), name


def test_prompts_strip_agrees_with_orchestrates_parser_on_every_agent_file():
    for path in sorted(AGENTS.glob("*.md")):
        assert prompts.load_prompt_text(path.stem) == orchestrate.parse_agent_spec(path).body, path.name


def test_modules_read_their_prompt_from_the_file():
    assert cohesion.judge_system() == prompts.load_prompt_text("cohesion")
    assert thread_synthesis.audit_system() == prompts.load_prompt_text("thread-audit")
    assert thread_synthesis.synthesis_system() == prompts.load_prompt_text("thread-synthesis")
    assert cluster_extractjoin.extract_system() == prompts.load_prompt_text("cluster-extract")


def test_no_prompt_constant_survives():
    for mod, name in ((cohesion, "JUDGE_SYSTEM"), (thread_synthesis, "AUDIT_SYSTEM"), (cluster_extractjoin, "EXTRACT_SYSTEM")):
        assert not hasattr(mod, name), f"{mod.__name__}.{name} still exists"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `docker compose run --rm --entrypoint python3 ci -m pytest -q newsroom/tests/test_prompts.py`
Expected: FAIL, `ModuleNotFoundError: prompts`.

- [ ] **Step 3: Write `prompts.py`**

```python
"""Prompt text for the model stages whose prompts used to live in Python constants.

Leaf module: no internal imports (cohesion, thread_synthesis and cluster_extractjoin import
this, and orchestrate imports them). Reads at call time from the same cwd-relative directory
orchestrate uses, so a run's prompts are exactly the files the image carries.
"""

from __future__ import annotations

import os
from pathlib import Path

AGENTS_DIR = Path(os.environ.get("AGENTS_DIR", ".claude/agents"))


def load_prompt_text(name: str) -> str:
    text = (AGENTS_DIR / f"{name}.md").read_text(encoding="utf-8")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise ValueError(f"{name}.md has no frontmatter")
    return parts[2].strip()
```

`orchestrate.parse_agent_spec` strips the body with the same `split("---", 2)` then `.strip()`; the agreement test above is what holds that true.

- [ ] **Step 4: Create the four files**

For each, create the `.md` with frontmatter and the constant's text verbatim as the body. Lift the text with Python so nothing is retyped:

```bash
docker compose run --rm --entrypoint python3 ci - <<'EOF'
import sys; sys.path.insert(0, "newsroom/src")
import cohesion, thread_synthesis, cluster_extractjoin
from pathlib import Path
def write(name, desc, model, body):
    Path(f".claude/agents/{name}.md").write_text(f"---\nname: {name}\ndescription: {desc}\nmodel: {model}\n---\n\n{body.strip()}\n", encoding="utf-8")
write("cohesion", "Judges whether a cluster is one event or several (the cohesion gate).", "claude-sonnet-4-6", cohesion.JUDGE_SYSTEM)
write("thread-audit", "Fact-checks a thread installment's claims against their cited full text.", "claude-sonnet-4-6", thread_synthesis.AUDIT_SYSTEM)
write("cluster-extract", "Extracts entities and tags from a batch of articles for the deterministic join.", "claude-sonnet-4-6", cluster_extractjoin.EXTRACT_SYSTEM)
EOF
```

The synthesis prompt is built in code near `thread_synthesis.py:295`; read the variable passed as `system_prompt=system` there. If it is a constant, lift it the same way as `thread-synthesis`; if it is assembled per call from a template plus data, lift the **template** (the static part) into the file and keep the per-call assembly in code (`synthesis_system()` returns the template). The `model:` line is the model each module passes at its call site (read them; `claude-sonnet-4-6` above is the current value for cohesion and threads per `run_usage`).

- [ ] **Step 5: Replace the constants with call-time functions**

In `cohesion.py`, delete `JUDGE_SYSTEM = """..."""` and add:

```python
import prompts

def judge_system() -> str:
    return prompts.load_prompt_text("cohesion")
```

and change the call site `system_prompt=JUDGE_SYSTEM` to `system_prompt=judge_system()`. Same for `thread_synthesis.audit_system()` / `synthesis_system()` and `cluster_extractjoin.extract_system()`. Add `prompts` to the leaf line of the module layering in `CLAUDE.md`.

- [ ] **Step 6: Run the suite**

Run: `bin/ci`
Expected: PASS. `test_archive_closure` scans `.claude/agents/*.md` for filenames; the new files name no input files. If a test elsewhere imported one of the deleted constants, update it to the function.

- [ ] **Step 7: Commit**

```bash
git add .claude/agents/thread-synthesis.md .claude/agents/thread-audit.md .claude/agents/cohesion.md .claude/agents/cluster-extract.md newsroom/src/prompts.py newsroom/src/cohesion.py newsroom/src/thread_synthesis.py newsroom/src/cluster_extractjoin.py newsroom/tests/test_prompts.py CLAUDE.md
git commit -m "refactor(prompts): the three prompts embedded in Python are files read at call time; every model stage's prompt is text the rewrite carries"
```

---

### Task 4: TypeScript scaffold and CI steps

**Files:**
- Create: `digest/package.json`, `digest/tsconfig.json`, `digest/vitest.config.ts`, `digest/eslint.config.js`, `digest/src/index.ts`, `digest/src/smoke.test.ts`, `digest/Dockerfile.ci`
- Modify: `bin/ci` (three TypeScript steps), `docker-compose.yml` (`ci-ts` service), `.gitignore`

**Interfaces:**
- Produces: `npm test`, `npm run lint`, `npm run typecheck`, `npm run sbom` under `digest/`; `bin/ci` runs the first three in the `ci-ts` container.

- [ ] **Step 1: Write the failing test**

```ts
// digest/src/smoke.test.ts
import { describe, expect, it } from "vitest";
import { version } from "./index.js";

describe("scaffold", () => {
  it("exports a version", () => {
    expect(version).toMatch(/^\d+\.\d+\.\d+$/);
  });
});
```

- [ ] **Step 2: Create the package**

`digest/package.json` (pin every version exactly; if a pinned devDependency does not exist, `npm view <pkg> version` and pin what it prints):

```json
{
  "name": "digest",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "engines": { "node": ">=22" },
  "scripts": {
    "build": "tsc -p tsconfig.json",
    "typecheck": "tsc -p tsconfig.json --noEmit",
    "lint": "eslint src",
    "test": "vitest run",
    "sbom": "npx --yes @cyclonedx/cyclonedx-npm --output-format JSON --output-file sbom.cdx.json"
  },
  "dependencies": {
    "@anthropic-ai/claude-agent-sdk": "0.3.278",
    "@temporalio/activity": "1.24.0",
    "@temporalio/client": "1.24.0",
    "@temporalio/common": "1.24.0",
    "@temporalio/worker": "1.24.0",
    "@temporalio/workflow": "1.24.0",
    "better-sqlite3": "13.0.3",
    "zod": "4.6.5"
  },
  "devDependencies": {
    "@temporalio/testing": "1.24.0",
    "@types/better-sqlite3": "7.6.13",
    "@types/node": "22.10.0",
    "eslint": "10.0.0",
    "typescript": "5.7.0",
    "typescript-eslint": "8.20.0",
    "vitest": "5.0.1"
  }
}
```

`digest/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "NodeNext",
    "moduleResolution": "NodeNext",
    "strict": true,
    "noUncheckedIndexedAccess": true,
    "exactOptionalPropertyTypes": true,
    "outDir": "dist",
    "rootDir": "src",
    "types": ["node"]
  },
  "include": ["src"]
}
```

`digest/vitest.config.ts`:

```ts
import { defineConfig } from "vitest/config";
export default defineConfig({ test: { include: ["src/**/*.test.ts"] } });
```

`digest/eslint.config.js`:

```js
import tseslint from "typescript-eslint";
export default tseslint.config(...tseslint.configs.recommendedTypeChecked, {
  languageOptions: { parserOptions: { project: "./tsconfig.json", tsconfigRootDir: import.meta.dirname } },
});
```

`digest/src/index.ts`:

```ts
export const version = "0.1.0";
```

Append to `.gitignore`: `digest/node_modules`, `digest/dist`, `digest/sbom.cdx.json`.

- [ ] **Step 3: Run the checks locally**

Run: `cd digest && npm install && npm test && npm run typecheck && npm run lint`
Expected: 1 test passes; typecheck and lint clean.

- [ ] **Step 4: Add the CI container and steps**

`digest/Dockerfile.ci`:

```dockerfile
FROM node:22-slim
WORKDIR /app/digest
COPY digest/package.json digest/package-lock.json ./
RUN npm ci
COPY digest/ ./
```

In `docker-compose.yml`, after the `ci-rust` service:

```yaml
  ci-ts:
    build:
      context: .
      dockerfile: digest/Dockerfile.ci
    working_dir: /app/digest
```

In `bin/ci`, the Rust steps run on the host side of `run_in_docker` (read `bin/ci:30-60` and `bin/ci:124-198`: the Python steps run inside the `ci` container, the Rust steps shell out to `docker compose`). Add three entries next to the Rust ones, in the same `(name, cmd, cwd)` shape:

```python
        ("Tests: TypeScript", ["docker", "compose", "run", "--rm", "--build", "ci-ts", "npm", "test"], REPO),
        ("Types: TypeScript", ["docker", "compose", "run", "--rm", "ci-ts", "npm", "run", "typecheck"], REPO),
        ("Style: TypeScript", ["docker", "compose", "run", "--rm", "ci-ts", "npm", "run", "lint"], REPO),
```

- [ ] **Step 5: Run `bin/ci` end to end**

Run: `bin/ci`
Expected: every step passes including the three new ones.

- [ ] **Step 6: Commit**

```bash
git add digest/package.json digest/package-lock.json digest/tsconfig.json digest/vitest.config.ts digest/eslint.config.js digest/src/index.ts digest/src/smoke.test.ts digest/Dockerfile.ci bin/ci docker-compose.yml .gitignore
git commit -m "build(digest): TypeScript package and CI steps"
```

---

### Task 5: The library gate on the real lockfile, and the API-name check (spec §8.4; Global Constraints)

**Files:**
- Create: `docs/proposed/2026-09-21-ts-candidates/lockfile-gate.md`, `docs/proposed/2026-09-21-ts-candidates/api-names.md`, `digest/scripts/check-api-names.sh`

**Interfaces:**
- Produces: a passed `still_active` run on the lockfile SBOM, and `api-names.md` listing, for every library identifier tasks 7 to 12 use, the `.d.ts` line that declares it. **A name with no line stops the plan until the task using it is corrected.**

- [ ] **Step 1: Run the maintenance gate on the lockfile**

Run: `cd digest && npm run sbom && still_active --sbom=sbom.cdx.json --fail-if-critical --markdown > ../docs/proposed/2026-09-21-ts-candidates/lockfile-gate.md; echo exit=$?`
Expected: exit 0. If a transitive dependency is flagged critical, stop and report which direct dependency pulls it in; substituting a library is a decision for the plan's owner, not this step.

- [ ] **Step 2: Write the API-name check**

```bash
#!/bin/bash
# digest/scripts/check-api-names.sh: every library identifier the plan's tasks 7-12 use must be
# declared in the installed .d.ts files. Prints "name<TAB>file:line" or "name<TAB>MISSING".
set -uo pipefail
cd "$(dirname "$0")/.."
declare -A NAMES=(
  [query]="node_modules/@anthropic-ai/claude-agent-sdk/sdk.d.ts"
  [systemPrompt]="node_modules/@anthropic-ai/claude-agent-sdk/sdk.d.ts"
  [allowedTools]="node_modules/@anthropic-ai/claude-agent-sdk/sdk.d.ts"
  [disallowedTools]="node_modules/@anthropic-ai/claude-agent-sdk/sdk.d.ts"
  [permissionMode]="node_modules/@anthropic-ai/claude-agent-sdk/sdk.d.ts"
  [outputFormat]="node_modules/@anthropic-ai/claude-agent-sdk/sdk.d.ts"
  [structured_output]="node_modules/@anthropic-ai/claude-agent-sdk/sdk.d.ts"
  [total_cost_usd]="node_modules/@anthropic-ai/claude-agent-sdk/sdk.d.ts"
  [includePartialMessages]="node_modules/@anthropic-ai/claude-agent-sdk/sdk.d.ts"
  [thinking]="node_modules/@anthropic-ai/claude-agent-sdk/sdk.d.ts"
  [cwd]="node_modules/@anthropic-ai/claude-agent-sdk/sdk.d.ts"
  [WorkflowIdReusePolicy]="node_modules/@temporalio/common/lib/index.d.ts"
  [WorkflowIdConflictPolicy]="node_modules/@temporalio/common/lib/index.d.ts"
  [ScheduleOverlapPolicy]="node_modules/@temporalio/client/lib/index.d.ts"
  [createTimeSkipping]="node_modules/@temporalio/testing/lib/index.d.ts"
  [nativeConnection]="node_modules/@temporalio/testing/lib/index.d.ts"
  [runUntil]="node_modules/@temporalio/worker/lib/index.d.ts"
  [workflowsPath]="node_modules/@temporalio/worker/lib/index.d.ts"
  [proxyActivities]="node_modules/@temporalio/workflow/lib/index.d.ts"
  [defineSignal]="node_modules/@temporalio/workflow/lib/index.d.ts"
  [setHandler]="node_modules/@temporalio/workflow/lib/index.d.ts"
  [condition]="node_modules/@temporalio/workflow/lib/index.d.ts"
  [heartbeat]="node_modules/@temporalio/activity/lib/index.d.ts"
  [toJSONSchema]="node_modules/zod/index.d.ts"
)
status=0
for name in "${!NAMES[@]}"; do
  f="${NAMES[$name]}"
  hit=$(grep -rn --include='*.d.ts' -m1 -w "$name" "$(dirname "$f")" 2>/dev/null | head -1)
  if [ -n "$hit" ]; then printf '%s\t%s\n' "$name" "$hit"; else printf '%s\tMISSING\n' "$name"; status=1; fi
done | sort
exit $status
```

Some packages put declarations under `lib/*.d.ts` rather than `lib/index.d.ts`; the script greps the package's whole `lib` directory, so the path only needs to name the package.

- [ ] **Step 3: Run it and record the result**

Run: `cd digest && bash scripts/check-api-names.sh | tee ../docs/proposed/2026-09-21-ts-candidates/api-names.md; echo exit=$?`
Expected: exit 0 and no `MISSING`. For each `MISSING` name: open the package's `.d.ts`, find the identifier that plays that role, and edit **this plan** (the task that uses the name) before proceeding. Two known risks to check by hand while there: whether the Agent SDK's `thinking` option exists in 0.3.278 or only `effort` does (task 7 uses `thinking`), and where `structured_output` sits on the TypeScript result message (task 7 reads it on the `result` message, as the SDK's structured-outputs doc shows).

- [ ] **Step 4: Commit**

```bash
git add digest/scripts/check-api-names.sh docs/proposed/2026-09-21-ts-candidates/lockfile-gate.md docs/proposed/2026-09-21-ts-candidates/api-names.md
git commit -m "build(digest): the library gate on the real lockfile; every library name the plan uses is declared in the installed types"
```

---

### Task 6: Frozen contracts as zod schemas and tests (spec §1)

**Files:**
- Create: `digest/src/contracts/ids.ts`, `digest/src/contracts/selections.ts`, `digest/src/contracts/coherence.ts`, `digest/src/contracts/index.ts`
- Test: `digest/src/contracts/ids.test.ts`, `digest/src/contracts/selections.test.ts`, `digest/src/contracts/coherence.test.ts`

**Interfaces:**
- Produces:
  - `ArticleId` (branded string, `/^A\d+$/`), `parseArticleId(s: string): ArticleId`, `assertNoUrls(text: string): void`.
  - `SelectionsSchema` (zod) mirroring `newsroom/src/schema.py` **exactly**: a source is `{ article_id }` and nothing else (`additionalProperties: False` there); a story has `headline, summary, why_it_matters, sources (min 1), reporting_varies?, cluster_id?` and nothing else; a brief is the same shape with `why_it_matters` **optional** (tolerated, as the Python says); `cluster_index` is rejected. `PREHEADER_MAX_CHARS = 157`, `NOT_COVERED_BLURB_MAX_LEN = 500`.
  - `CoherenceReportSchema` (zod) mirroring `COHERENCE_REPORT_SCHEMA`; `COHERENCE_FIELDS`, `FAILURE_KINDS`; `coherenceReportJsonSchema()` targeting draft-07.
- Note for the spec: §1's "sources[{article_id, angle, bias}]" is wrong against its own backing; the schema is `{article_id}` and `url`, `source`, `bias` are resolved by code after validation. Fix that row in the spec in this task's commit.

- [ ] **Step 1: Write the failing tests**

```ts
// digest/src/contracts/ids.test.ts
import { describe, expect, it } from "vitest";
import { assertNoUrls, parseArticleId } from "./ids.js";

describe("article ids", () => {
  it("accepts A1 and A42", () => {
    expect(parseArticleId("A1")).toBe("A1");
    expect(parseArticleId("A42")).toBe("A42");
  });
  it("rejects anything else", () => {
    for (const bad of ["a1", "A", "A-1", "1", "A1 ", "https://x"]) expect(() => parseArticleId(bad)).toThrow();
  });
  it("assertNoUrls throws on a URL and passes on a source id", () => {
    expect(() => assertNoUrls("see https://example.com/x")).toThrow(/URL/);
    expect(() => assertNoUrls("reuters,Reuters,centre")).not.toThrow();
  });
});
```

```ts
// digest/src/contracts/selections.test.ts
import { describe, expect, it } from "vitest";
import { PREHEADER_MAX_CHARS, SelectionsSchema } from "./selections.js";

const story = { headline: "H", summary: "S", why_it_matters: "W", sources: [{ article_id: "A1" }] };

describe("selections contract, mirrored from newsroom/src/schema.py", () => {
  it("accepts the shipped shape", () => {
    expect(SelectionsSchema.safeParse({ must_know: [story], should_know: [], preheader: "p" }).success).toBe(true);
  });
  it("a source is {article_id} and nothing else; angle and bias are resolved by code after validation", () => {
    const bad = { ...story, sources: [{ article_id: "A1", angle: "a", bias: "centre" }] };
    expect(SelectionsSchema.safeParse({ must_know: [bad], should_know: [], preheader: "p" }).success).toBe(false);
  });
  it("rejects a preheader over the cap", () => {
    expect(SelectionsSchema.safeParse({ must_know: [], should_know: [], preheader: "x".repeat(PREHEADER_MAX_CHARS + 1) }).success).toBe(false);
  });
  it("rejects cluster_index: it left the contract", () => {
    expect(SelectionsSchema.safeParse({ must_know: [{ ...story, cluster_index: 3 }], should_know: [], preheader: "p" }).success).toBe(false);
  });
  it("a brief may omit why_it_matters and may carry one (tolerated, per the Python)", () => {
    const { why_it_matters: _w, ...brief } = story;
    expect(SelectionsSchema.safeParse({ must_know: [], should_know: [brief], preheader: "p" }).success).toBe(true);
    expect(SelectionsSchema.safeParse({ must_know: [], should_know: [story], preheader: "p" }).success).toBe(true);
  });
  it("a story needs at least one source", () => {
    expect(SelectionsSchema.safeParse({ must_know: [{ ...story, sources: [] }], should_know: [], preheader: "p" }).success).toBe(false);
  });
});
```

```ts
// digest/src/contracts/coherence.test.ts
import { describe, expect, it } from "vitest";
import { CoherenceReportSchema, coherenceReportJsonSchema } from "./coherence.js";

describe("coherence contract", () => {
  it("accepts a pass and a fail entry", () => {
    const r = CoherenceReportSchema.safeParse({
      results: [
        { headline: "H1", article_ids: ["A1"], pass: true, reason: "ok" },
        { headline: "H2", article_ids: ["A5"], pass: false, reason: "summary: x", failed_fields: ["summary"], failure_kinds: { summary: "contradicted" } },
      ],
    });
    expect(r.success).toBe(true);
  });
  it("rejects an unknown field name and an unknown kind", () => {
    expect(CoherenceReportSchema.safeParse({ results: [{ headline: "H", article_ids: [], pass: false, reason: "r", failed_fields: ["title"] }] }).success).toBe(false);
    expect(CoherenceReportSchema.safeParse({ results: [{ headline: "H", article_ids: [], pass: false, reason: "r", failure_kinds: { summary: "fabricated" } }] }).success).toBe(false);
  });
  it("the JSON schema constrains shape, never count, and targets draft-07", () => {
    const schema = coherenceReportJsonSchema();
    const json = JSON.stringify(schema);
    for (const key of ["minItems", "maxItems", "minLength", "maxLength"]) expect(json).not.toContain(key);
    expect(String(schema["$schema"] ?? "")).toContain("draft-07");
  });
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd digest && npm test`
Expected: FAIL, modules not found.

- [ ] **Step 3: Write the contracts**

```ts
// digest/src/contracts/ids.ts
export type ArticleId = string & { readonly __brand: "ArticleId" };
const ID = /^A\d+$/;
export function parseArticleId(s: string): ArticleId {
  if (!ID.test(s)) throw new Error(`not an article id: ${JSON.stringify(s)}`);
  return s as ArticleId;
}
const URL_RE = /https?:\/\//i;
export function assertNoUrls(text: string): void {
  if (URL_RE.test(text)) throw new Error("a URL reached a model stage; the no-URL invariant is broken");
}
```

```ts
// digest/src/contracts/selections.ts
// Mirrors newsroom/src/schema.py: SOURCE_SCHEMA, REPORTING_VARIES_SCHEMA, ARTICLE_SCHEMA,
// SHOULD_KNOW_ARTICLE_SCHEMA, SELECTIONS_SCHEMA. `.strict()` is additionalProperties: false.
import { z } from "zod";
export const PREHEADER_MAX_CHARS = 157;
export const NOT_COVERED_BLURB_MAX_LEN = 500;
const Source = z.object({ article_id: z.string().regex(/^A\d+$/) }).strict();
const ReportingVaries = z.object({ source: z.string(), angle: z.string(), bias: z.string() }).strict();
const Story = z
  .object({
    headline: z.string(),
    summary: z.string(),
    why_it_matters: z.string(),
    sources: z.array(Source).min(1),
    reporting_varies: z.array(ReportingVaries).optional(),
    cluster_id: z.string().optional(),
  })
  .strict();
// Briefs render headline + summary only; why_it_matters is tolerated, not required (archived
// selections before 2026-09-03 carry one, and --resume re-renders those).
const Brief = Story.extend({ why_it_matters: z.string().optional() }).strict();
export const SelectionsSchema = z
  .object({
    must_know: z.array(Story),
    should_know: z.array(Brief),
    preheader: z.string().max(PREHEADER_MAX_CHARS),
    not_covered_blurb: z.string().max(NOT_COVERED_BLURB_MAX_LEN).optional(),
  })
  .strict();
export type Selections = z.infer<typeof SelectionsSchema>;
```

Read `newsroom/src/schema.py:105-125` for `SELECTIONS_SCHEMA`'s full property list (there may be fields beyond the four above, and its `required` list); mirror every one. `min(1)` on sources is a contract on the selections *output* and matches the Python's `minItems: 1`; the never-count rule is for schemas a model is decoded against.

```ts
// digest/src/contracts/coherence.ts
import { z } from "zod";
export const COHERENCE_FIELDS = ["headline", "summary", "why_it_matters"] as const;
export const FAILURE_KINDS = ["contradicted", "unsupported"] as const;
const Result = z.object({
  headline: z.string(),
  article_ids: z.array(z.string()),
  pass: z.boolean(),
  reason: z.string(),
  failed_fields: z.array(z.enum(COHERENCE_FIELDS)).optional(),
  failure_kinds: z.record(z.string(), z.enum(FAILURE_KINDS)).optional(),
});
export const CoherenceReportSchema = z.object({ results: z.array(Result) });
export type CoherenceReport = z.infer<typeof CoherenceReportSchema>;
export function coherenceReportJsonSchema(): Record<string, unknown> {
  // The Agent SDK validates against JSON Schema draft-07; zod targets 2020-12 by default.
  return z.toJSONSchema(CoherenceReportSchema, { target: "draft-7" }) as Record<string, unknown>;
}
```

```ts
// digest/src/contracts/index.ts
export * from "./ids.js";
export * from "./selections.js";
export * from "./coherence.js";
```

- [ ] **Step 4: Fix the spec row**

In `docs/superpowers/specs/2026-09-21-four-systems-rewrite-design.md`, the Output schema row: replace `sources[{article_id, angle, bias}]` with `sources[{article_id}] (url, source and bias are resolved by code after validation)`.

- [ ] **Step 5: Run the tests**

Run: `cd digest && npm test && npm run typecheck && npm run lint`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add digest/src/contracts docs/superpowers/specs/2026-09-21-four-systems-rewrite-design.md
git commit -m "feat(digest): the frozen contracts as zod schemas, mirrored from schema.py; cluster_index leaves the contract"
```

---

### Task 7: Artifact store over SQLite with pointers, integrity, conflict and quarantine (spec §2.1)

**Files:**
- Create: `digest/src/store/db.ts`, `digest/src/store/artifacts.ts`
- Test: `digest/src/store/artifacts.test.ts`

**Interfaces:**
- Produces:
  ```ts
  export interface Pointer { runId: number; name: string; sha256: string }
  export class IntegrityError extends Error {}
  export class ConflictError extends Error {}
  export class ArtifactStore {
    constructor(dbPath: string);
    put(runId: number, name: string, content: string): Pointer;   // no row: insert; same content: return it; different content: throw ConflictError
    get(p: Pointer): string;                                       // IntegrityError if the row is missing or its hash differs
    find(runId: number, name: string): Pointer | undefined;
    quarantine(runId: number, name: string): string;               // renames to `${name}.corrupt.${n}`
    replace(runId: number, name: string, content: string): Pointer; // the explicit force path
    close(): void;
  }
  ```
- Consumes: the real `run_artifacts` table. The test applies the repo's own migrations for it so the schema under test is production's.

- [ ] **Step 1: Write the failing test**

```ts
// digest/src/store/artifacts.test.ts
import Database from "better-sqlite3";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { ArtifactStore, ConflictError, IntegrityError } from "./artifacts.js";

const MIGRATIONS = new URL("../../../migrations/", import.meta.url).pathname;

function freshDb(): string {
  const path = join(mkdtempSync(join(tmpdir(), "digest-")), "digest.db");
  const db = new Database(path);
  db.exec("CREATE TABLE digest_runs (id INTEGER PRIMARY KEY AUTOINCREMENT)");
  for (const f of ["20260615100000_add_run_artifacts.sql", "20260729120000_unique_run_artifact_per_run.sql"]) db.exec(readFileSync(join(MIGRATIONS, f), "utf8"));
  db.close();
  return path;
}

describe("ArtifactStore on the production run_artifacts schema", () => {
  it("put returns a pointer whose hash is the content's sha256, and get round-trips", () => {
    const s = new ArtifactStore(freshDb());
    const p = s.put(300, "recap.txt", "hello");
    expect(p.sha256).toBe("2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824");
    expect(s.get(p)).toBe("hello");
  });
  it("put of identical content is idempotent; put of different content is a conflict, never a replace", () => {
    const s = new ArtifactStore(freshDb());
    const first = s.put(300, "recap.txt", "one");
    expect(s.put(300, "recap.txt", "one")).toEqual(first);
    expect(() => s.put(300, "recap.txt", "two")).toThrow(ConflictError);
    expect(s.get(first)).toBe("one");
  });
  it("get throws IntegrityError when the row no longer matches the pointer", () => {
    const path = freshDb();
    const s = new ArtifactStore(path);
    const p = s.put(300, "recap.txt", "one");
    new Database(path).prepare("UPDATE run_artifacts SET content='tampered'").run();
    expect(() => s.get(p)).toThrow(IntegrityError);
  });
  it("quarantine renames and frees the name; replace is the only overwrite", () => {
    const s = new ArtifactStore(freshDb());
    s.put(300, "recap.txt", "bad");
    expect(s.quarantine(300, "recap.txt")).toBe("recap.txt.corrupt.1");
    expect(s.find(300, "recap.txt")).toBeUndefined();
    const p = s.replace(300, "recap.txt", "good");
    expect(s.get(p)).toBe("good");
  });
});
```

The migration files may reference `digest_runs` in a foreign key (hence the stub table) and may contain statements yoyo wraps; if `db.exec` rejects a file, read it and run only its `CREATE` statements in the test, and say so in a comment.

- [ ] **Step 2: Run it to verify it fails**

Run: `cd digest && npm test -- artifacts`
Expected: FAIL, module not found.

- [ ] **Step 3: Write the store**

```ts
// digest/src/store/db.ts
import Database from "better-sqlite3";
export function openDb(path: string): Database.Database {
  const db = new Database(path);
  db.pragma("busy_timeout = 5000"); // the pipeline sets the same; WAL stays off (spec §5)
  return db;
}
```

```ts
// digest/src/store/artifacts.ts
import { createHash } from "node:crypto";
import type Database from "better-sqlite3";
import { openDb } from "./db.js";

export interface Pointer { runId: number; name: string; sha256: string }
export class IntegrityError extends Error {}
export class ConflictError extends Error {}

const sha = (s: string) => createHash("sha256").update(s, "utf8").digest("hex");
type Row = { content: string } | undefined;

export class ArtifactStore {
  private readonly db: Database.Database;
  constructor(dbPath: string) {
    this.db = openDb(dbPath);
  }
  private row(runId: number, name: string): Row {
    return this.db.prepare("SELECT content FROM run_artifacts WHERE run_id=? AND artifact_name=?").get(runId, name) as Row;
  }
  find(runId: number, name: string): Pointer | undefined {
    const r = this.row(runId, name);
    return r ? { runId, name, sha256: sha(r.content) } : undefined;
  }
  put(runId: number, name: string, content: string): Pointer {
    const existing = this.row(runId, name);
    if (existing) {
      if (existing.content === content) return { runId, name, sha256: sha(content) };
      throw new ConflictError(`artifact ${name} for run ${runId} exists with different content; quarantine or replace explicitly`);
    }
    this.db.prepare("INSERT INTO run_artifacts (run_id, artifact_name, content) VALUES (?, ?, ?)").run(runId, name, content);
    return { runId, name, sha256: sha(content) };
  }
  get(p: Pointer): string {
    const r = this.row(p.runId, p.name);
    if (!r) throw new IntegrityError(`no artifact ${p.name} for run ${p.runId}`);
    if (sha(r.content) !== p.sha256) throw new IntegrityError(`artifact ${p.name} for run ${p.runId} does not match its pointer`);
    return r.content;
  }
  quarantine(runId: number, name: string): string {
    const n = (this.db.prepare("SELECT COUNT(*) AS c FROM run_artifacts WHERE run_id=? AND artifact_name LIKE ?").get(runId, `${name}.corrupt.%`) as { c: number }).c + 1;
    const renamed = `${name}.corrupt.${n}`;
    this.db.prepare("UPDATE run_artifacts SET artifact_name=? WHERE run_id=? AND artifact_name=?").run(renamed, runId, name);
    return renamed;
  }
  replace(runId: number, name: string, content: string): Pointer {
    this.db.prepare("INSERT OR REPLACE INTO run_artifacts (run_id, artifact_name, content) VALUES (?, ?, ?)").run(runId, name, content);
    return { runId, name, sha256: sha(content) };
  }
  close(): void {
    this.db.close();
  }
}
```

The stage validator hook (spec §2.1: "a row that fails the hash **or the validator** is quarantined") belongs to each activity, which owns its validator; plan A2's activities call `find`, validate, and on failure `quarantine` then produce a fresh sample. The store stays validator-agnostic.

- [ ] **Step 4: Run the tests**

Run: `cd digest && npm test && npm run typecheck && npm run lint`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add digest/src/store
git commit -m "feat(digest): artifact store over the production run_artifacts schema; a pointer is (run, name, sha256), put conflicts rather than replaces"
```

---

### Task 8: The stage runner over the TypeScript Agent SDK (spec §2.2)

**Files:**
- Create: `digest/src/runner/prompt.ts`, `digest/src/runner/run-stage.ts`
- Test: `digest/src/runner/prompt.test.ts`, `digest/src/runner/run-stage.test.ts`

**Interfaces:**
- Produces (all in `digest/src/runner/prompt.ts` unless noted):
  ```ts
  export type StageTool = "Read" | "Grep";
  export interface StageSpec { name: string; model: string; thinking: "adaptive" | "disabled"; tools: StageTool[]; body: string }
  export function parseAgentSpec(markdown: string): StageSpec;      // throws on a tool that is not Read or Grep (Write is gone by contract)
  export function renderBody(body: string, todayIso: string): string; // {{CURRENT_DATE}} → "Monday, September 21, 2026"; throws on any other {{TOKEN}}
  // run-stage.ts
  export interface StageInput { userMessage: string; inputDir: string }
  export interface StageResult { text: string; structured?: unknown; toolCalls: { name: string; target: string }[]; costUsd: number; usage: Record<string, number>; durationMs: number }
  export type SdkQuery = typeof query;                               // the SDK's own type: the adapter is compiled against it
  export function runStage(spec: StageSpec, input: StageInput, opts: { outputSchema?: Record<string, unknown>; today: string; query?: SdkQuery }): Promise<StageResult>;
  ```
- Consumes: `@anthropic-ai/claude-agent-sdk` `query` and its `Options` and `SDKMessage` types, **as task 5's `api-names.md` confirmed them**. Tool scoping uses `allowedTools` plus `disallowedTools` for every built-in tool not in the spec (`Write`, `Edit`, `Bash`, `WebFetch`, `WebSearch`, `Glob`, `Task`, `NotebookEdit`); the SDK's `tools` option is not used.

- [ ] **Step 1: Write the failing tests**

```ts
// digest/src/runner/prompt.test.ts
import { describe, expect, it } from "vitest";
import { parseAgentSpec, renderBody } from "./prompt.js";

const MD = `---
name: coherence
tools: Read, Grep
model: claude-sonnet-5
thinking: adaptive
---

Today is {{CURRENT_DATE}}. Reply with {"results": []}.`;

describe("prompt", () => {
  it("parses frontmatter and body", () => {
    const s = parseAgentSpec(MD);
    expect(s).toMatchObject({ name: "coherence", model: "claude-sonnet-5", tools: ["Read", "Grep"], thinking: "adaptive" });
    expect(s.body.startsWith("Today is")).toBe(true);
  });
  it("refuses a tool the contract removed", () => {
    expect(() => parseAgentSpec(MD.replace("Read, Grep", "Read, Write"))).toThrow(/Write/);
  });
  it("renders the date and leaves JSON braces alone", () => {
    const out = renderBody(parseAgentSpec(MD).body, "2026-09-21");
    expect(out).toContain("Today is Monday, September 21, 2026");
    expect(out).toContain('{"results": []}');
  });
  it("refuses an unrendered token", () => {
    expect(() => renderBody("x {{OTHER}} y", "2026-09-21")).toThrow(/OTHER/);
  });
});
```

```ts
// digest/src/runner/run-stage.test.ts
import type { Options, SDKMessage } from "@anthropic-ai/claude-agent-sdk";
import { describe, expect, it } from "vitest";
import type { StageSpec } from "./prompt.js";
import { runStage, type SdkQuery } from "./run-stage.js";

const spec: StageSpec = { name: "coherence", model: "claude-sonnet-5", thinking: "adaptive", tools: ["Read", "Grep"], body: "Reply with JSON." };

// A fake with the SDK's own signature, so a renamed option or message field fails to compile here.
function fakeQuery(messages: SDKMessage[], seen?: { options?: Options }): SdkQuery {
  return (({ options }: { prompt: string; options?: Options }) => {
    if (seen) seen.options = options;
    return (async function* () { for (const m of messages) yield m; })();
  }) as unknown as SdkQuery;
}
const result = (over: Record<string, unknown>): SDKMessage =>
  ({ type: "result", subtype: "success", result: "{}", total_cost_usd: 0, usage: {}, duration_ms: 1, is_error: false, num_turns: 1, session_id: "s", ...over }) as unknown as SDKMessage;
const assistant = (content: unknown[]): SDKMessage => ({ type: "assistant", message: { content }, session_id: "s" }) as unknown as SDKMessage;

describe("runStage", () => {
  it("collects tool calls in order, the final text, and the structured output", async () => {
    const q = fakeQuery([
      assistant([{ type: "tool_use", id: "1", name: "Read", input: { file_path: "/in/a.csv" } }, { type: "tool_use", id: "2", name: "Grep", input: { pattern: "58%", path: "/in" } }]),
      result({ result: '{"results":[]}', structured_output: { results: [] }, total_cost_usd: 0.5, usage: { output_tokens: 10 }, duration_ms: 1200 }),
    ]);
    const r = await runStage(spec, { userMessage: "Begin.", inputDir: "/in" }, { today: "2026-09-21", query: q, outputSchema: { type: "object" } });
    expect(r.toolCalls).toEqual([{ name: "Read", target: "/in/a.csv" }, { name: "Grep", target: "58%" }]);
    expect(r.text).toBe('{"results":[]}');
    expect(r.structured).toEqual({ results: [] });
    expect(r.costUsd).toBe(0.5);
  });
  it("passes model, cwd, allowed and disallowed tools, and the schema through to the SDK options", async () => {
    const seen: { options?: Options } = {};
    const q = fakeQuery([result({ structured_output: {} })], seen);
    await runStage(spec, { userMessage: "Begin.", inputDir: "/in" }, { today: "2026-09-21", query: q, outputSchema: { type: "object" } });
    expect(seen.options?.model).toBe("claude-sonnet-5");
    expect(seen.options?.cwd).toBe("/in");
    expect(seen.options?.allowedTools).toEqual(["Read", "Grep"]);
    expect(seen.options?.disallowedTools).toContain("Write");
    expect(seen.options?.outputFormat).toEqual({ type: "json_schema", schema: { type: "object" } });
  });
  it("throws when a schema was requested and the success carries no structured output", async () => {
    const q = fakeQuery([result({})]);
    await expect(runStage(spec, { userMessage: "Begin.", inputDir: "/in" }, { today: "2026-09-21", query: q, outputSchema: { type: "object" } })).rejects.toThrow(/structured/);
  });
  it("throws on a non-success result and never returns partial output", async () => {
    const q = fakeQuery([result({ subtype: "error_max_turns", is_error: true })]);
    await expect(runStage(spec, { userMessage: "Begin.", inputDir: "/in" }, { today: "2026-09-21", query: q })).rejects.toThrow(/error_max_turns/);
  });
});
```

The fakes cast to `SDKMessage` at one place each; the *adapter* in `run-stage.ts` is compiled against the real `Options` and `SDKMessage` types with no cast, which is what catches a renamed field.

- [ ] **Step 2: Run them to verify they fail**

Run: `cd digest && npm test -- runner`
Expected: FAIL, modules not found.

- [ ] **Step 3: Write `prompt.ts`**

```ts
// digest/src/runner/prompt.ts
export type StageTool = "Read" | "Grep";
export interface StageSpec { name: string; model: string; thinking: "adaptive" | "disabled"; tools: StageTool[]; body: string }

const ALLOWED: ReadonlySet<string> = new Set(["Read", "Grep"]);

export function parseAgentSpec(markdown: string): StageSpec {
  const parts = markdown.split("---");
  if (parts.length < 3) throw new Error("agent spec has no frontmatter");
  const front = parts[1] ?? "";
  const body = parts.slice(2).join("---").trim();
  const fields: Record<string, string> = {};
  for (const line of front.split("\n")) {
    const i = line.indexOf(":");
    if (i > 0) fields[line.slice(0, i).trim()] = line.slice(i + 1).trim().replace(/^["']|["']$/g, "");
  }
  const model = fields["model"];
  if (!model) throw new Error("agent spec has no model");
  const named = (fields["tools"] ?? "").split(/[,\s]+/).filter(Boolean);
  const bad = named.filter((t) => !ALLOWED.has(t));
  if (bad.length) throw new Error(`agent spec names tools the contract removed: ${bad.join(", ")}`);
  return { name: fields["name"] ?? "", model, thinking: fields["thinking"] === "adaptive" ? "adaptive" : "disabled", tools: named as StageTool[], body };
}

const TOKEN = /\{\{([^{}]*)\}\}/g;

export function renderBody(body: string, todayIso: string): string {
  const d = new Date(`${todayIso}T00:00:00Z`);
  const pretty = d.toLocaleDateString("en-US", { weekday: "long", year: "numeric", month: "long", day: "numeric", timeZone: "UTC" });
  const out = body.replaceAll("{{CURRENT_DATE}}", pretty);
  const left = [...out.matchAll(TOKEN)].map((m) => m[1]);
  if (left.length) throw new Error(`unrendered token(s): ${[...new Set(left)].join(", ")}`);
  return out;
}
```

- [ ] **Step 4: Write `run-stage.ts`**

```ts
// digest/src/runner/run-stage.ts
import { query, type Options, type SDKMessage } from "@anthropic-ai/claude-agent-sdk";
import { renderBody, type StageSpec, type StageTool } from "./prompt.js";

export interface StageInput { userMessage: string; inputDir: string }
export interface StageResult { text: string; structured?: unknown; toolCalls: { name: string; target: string }[]; costUsd: number; usage: Record<string, number>; durationMs: number }
export type SdkQuery = typeof query;

const BUILTIN = ["Read", "Grep", "Glob", "Write", "Edit", "Bash", "WebFetch", "WebSearch", "Task", "NotebookEdit"] as const;

function targetOf(name: string, input: unknown): string {
  const inp = (input && typeof input === "object" ? input : {}) as Record<string, unknown>;
  const v = name === "Grep" ? inp["pattern"] : inp["file_path"];
  return typeof v === "string" ? v : "";
}

export async function runStage(
  spec: StageSpec,
  input: StageInput,
  opts: { outputSchema?: Record<string, unknown>; today: string; query?: SdkQuery },
): Promise<StageResult> {
  const q = opts.query ?? query;
  const allowed: StageTool[] = spec.tools;
  const options: Options = {
    model: spec.model,
    systemPrompt: renderBody(spec.body, opts.today),
    cwd: input.inputDir,
    allowedTools: allowed,
    disallowedTools: BUILTIN.filter((t) => !(allowed as readonly string[]).includes(t)),
    permissionMode: "acceptEdits",
    includePartialMessages: true,
    ...(opts.outputSchema ? { outputFormat: { type: "json_schema", schema: opts.outputSchema } } : {}),
  };
  // `thinking` is set here only if task 5's api-names.md found it on Options; otherwise map
  // spec.thinking to `effort` ("adaptive" -> omit, "disabled" -> "low") and note it in the commit.
  const toolCalls: { name: string; target: string }[] = [];
  const texts: string[] = [];
  let result: Extract<SDKMessage, { type: "result" }> | undefined;
  for await (const m of q({ prompt: input.userMessage, options })) {
    if (m.type === "assistant") {
      for (const block of m.message.content) {
        if (block.type === "tool_use") toolCalls.push({ name: block.name, target: targetOf(block.name, block.input) });
        else if (block.type === "text") texts.push(block.text);
      }
    } else if (m.type === "result") {
      result = m;
    }
  }
  if (!result) throw new Error(`stage ${spec.name}: no result message`);
  if (result.subtype !== "success" || result.is_error) throw new Error(`stage ${spec.name}: ${result.subtype}`);
  const structured = "structured_output" in result ? result.structured_output : undefined;
  // The SDK re-prompts on schema mismatch and ends with error_max_structured_output_retries; a
  // success WITHOUT structured_output when a schema was requested is also a failure (SDK docs).
  if (opts.outputSchema && structured === undefined) throw new Error(`stage ${spec.name}: success without structured output`);
  return {
    text: (result.result ?? texts.join("\n")).trim(),
    ...(structured !== undefined ? { structured } : {}),
    toolCalls,
    costUsd: result.total_cost_usd ?? 0,
    usage: (result.usage ?? {}) as Record<string, number>,
    durationMs: result.duration_ms ?? 0,
  };
}
```

If `tsc` reports a field that does not exist on `Options` or on the result message type, that is the API-name check doing its job: correct the field to what `sdk.d.ts` declares (task 5 recorded the line) and keep the test's expectation in step with it. `permissionMode: "acceptEdits"` is what production passes today; with Write disallowed it approves nothing.

- [ ] **Step 5: Run the tests**

Run: `cd digest && npm test && npm run typecheck && npm run lint`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add digest/src/runner
git commit -m "feat(digest): the stage runner over the Agent SDK; tools scoped per stage, result as the final message, tool calls on record"
```

- [ ] **Step 7: One live smoke, opt-in (~$0.01, model call)**

Create `digest/src/cli/smoke-stage.ts`:

```ts
import { runStage } from "../runner/run-stage.js";
const r = await runStage(
  { name: "smoke", model: "claude-haiku-4-5", thinking: "disabled", tools: [], body: "Reply with exactly the JSON {\"ok\": true} and nothing else." },
  { userMessage: "Begin.", inputDir: process.cwd() },
  { today: new Date().toISOString().slice(0, 10), outputSchema: { type: "object", properties: { ok: { type: "boolean" } }, required: ["ok"] } },
);
console.log(JSON.stringify(r));
```

The subscription login lives in the `claude-sessions` volume the newsroom service mounts (`docker-compose.yml:129`); the newsroom image has no Node. Run: `docker run --rm -v news-digest_claude-sessions:/home/node/.claude -v "$(pwd)/digest:/app/digest" -w /app/digest node:22-slim sh -c "npm ci && npx tsx src/cli/smoke-stage.ts"`. Expected: `"structured":{"ok":true}`. Record the command in `digest/README.md` and commit as `docs(digest): the live smoke command`.

---

### Task 9a: Signals, the activity interface, and stub activities (spec §2.1, §2.3)

**Files:**
- Create: `digest/src/workflow/signals.ts`, `digest/src/activities/index.ts`, `digest/src/activities/stub.ts`
- Test: `digest/src/activities/stub.test.ts`

**Interfaces:**
- Produces (definitions live here; every later task imports them from these paths):
  ```ts
  // signals.ts
  export const approveSignal = defineSignal<[{ decision: "approve" | "reject" }]>("approve");
  export const retrySignal = defineSignal<[{ decision: "retry" | "abort" }]>("retry");
  export const operatorNoteSignal = defineSignal<[{ stage: string; note: string }]>("operatorNote");
  // activities/index.ts
  export interface DigestInput { runDate: string; resumeRun?: number; force?: boolean; failStage?: "select" }  // failStage: test hook, stub only
  export interface DigestOutput { runId: number; stories: number; broadcast: "sent" | "rejected" | "skipped" }
  export interface Activities { ... }   // exactly the list below
  export const SOURCE_IDS_STUB: readonly string[];
  ```

- [ ] **Step 1: Write the failing test**

```ts
// digest/src/activities/stub.test.ts
import { describe, expect, it } from "vitest";
import { stubActivities } from "./stub.js";

describe("stub activities", () => {
  it("startRun honours resumeRun and defaults to 1", async () => {
    const a = stubActivities();
    expect(await a.startRun({ runDate: "2026-09-21" })).toEqual({ runId: 1 });
    expect(await a.startRun({ runDate: "2026-09-21", resumeRun: 303, force: true })).toEqual({ runId: 303 });
  });
  it("select throws non-retryably only when the input asks it to", async () => {
    const a = stubActivities();
    const p = { runId: 1, name: "x", sha256: "0".repeat(64) };
    await expect(a.select(1, p, p, undefined, { runDate: "d", failStage: "select" })).rejects.toThrow(/StubFailure|select failed/);
    await expect(a.select(1, p, p)).resolves.toMatchObject({ name: "selected.json" });
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd digest && npm test -- stub`
Expected: FAIL.

- [ ] **Step 3: Write the three files**

```ts
// digest/src/workflow/signals.ts
import { defineSignal } from "@temporalio/workflow";
export const approveSignal = defineSignal<[{ decision: "approve" | "reject" }]>("approve");
export const retrySignal = defineSignal<[{ decision: "retry" | "abort" }]>("retry");
export const operatorNoteSignal = defineSignal<[{ stage: string; note: string }]>("operatorNote");
```

```ts
// digest/src/activities/index.ts
import type { Pointer } from "../store/artifacts.js";
export interface DigestInput { runDate: string; resumeRun?: number; force?: boolean; failStage?: "select" }
export interface DigestOutput { runId: number; stories: number; broadcast: "sent" | "rejected" | "skipped" }
export interface Activities {
  startRun(input: DigestInput): Promise<{ runId: number }>;
  fetchFeed(runId: number, sourceId: string): Promise<Pointer>;
  prepare(runId: number, fetched: Pointer[]): Promise<{ articles: Pointer[]; index: Pointer }>;
  cluster(runId: number, articles: Pointer[]): Promise<Pointer>;
  recap(runId: number): Promise<Pointer>;
  select(runId: number, clusters: Pointer, recap: Pointer, note?: string, input?: DigestInput): Promise<Pointer>;
  fulltext(runId: number, selected: Pointer): Promise<Pointer>;
  writeStory(runId: number, storyIndex: number, selected: Pointer, fulltext: Pointer, note?: string): Promise<Pointer>;
  preheader(runId: number, drafts: Pointer[]): Promise<Pointer>;
  coherence(runId: number, drafts: Pointer[], fulltext: Pointer, note?: string): Promise<Pointer>;
  repair(runId: number, drafts: Pointer[], report: Pointer): Promise<Pointer>;
  assemble(runId: number, drafts: Pointer[], report: Pointer, repair: Pointer, preheader: Pointer): Promise<Pointer>;
  gnews(runId: number, selections: Pointer): Promise<Pointer>;
  threads(runId: number, selections: Pointer): Promise<Pointer>;
  render(runId: number, selections: Pointer, threads: Pointer, gnews: Pointer): Promise<{ html: Pointer; email: Pointer }>;
  broadcast(runId: number, email: Pointer): Promise<{ broadcastId: string }>;
  finishRun(runId: number, output: Omit<DigestOutput, "runId">): Promise<void>;
}
export const SOURCE_IDS_STUB: readonly string[] = ["reuters", "bbc_world", "al_jazeera"];
```

```ts
// digest/src/activities/stub.ts
import { ApplicationFailure } from "@temporalio/common";
import type { Pointer } from "../store/artifacts.js";
import type { Activities, DigestInput } from "./index.js";
const ptr = (runId: number, name: string): Pointer => ({ runId, name, sha256: "0".repeat(64) });
export function stubActivities(): Activities {
  return {
    startRun: async (input: DigestInput) => ({ runId: input.resumeRun ?? 1 }),
    fetchFeed: async (runId, sourceId) => ptr(runId, `feed_${sourceId}.json`),
    prepare: async (runId) => ({ articles: [ptr(runId, "articles_1.csv")], index: ptr(runId, "article_index.json") }),
    cluster: async (runId) => ptr(runId, "clusters.json"),
    recap: async (runId) => ptr(runId, "recap.txt"),
    select: async (runId, _clusters, _recap, _note, input) => {
      if (input?.failStage === "select") throw ApplicationFailure.nonRetryable("select failed for the test", "StubFailure");
      return ptr(runId, "selected.json");
    },
    fulltext: async (runId) => ptr(runId, "article_fulltext.json"),
    writeStory: async (runId, i) => ptr(runId, `draft_${i}.json`),
    preheader: async (runId) => ptr(runId, "preheader.txt"),
    coherence: async (runId) => ptr(runId, "coherence_report.json"),
    repair: async (runId) => ptr(runId, "repair_resolution.json"),
    assemble: async (runId) => ptr(runId, "selections.json"),
    gnews: async (runId) => ptr(runId, "gnews.json"),
    threads: async (runId) => ptr(runId, "thread_links.json"),
    render: async (runId) => ({ html: ptr(runId, "digest.html"), email: ptr(runId, "digest.mjml.html") }),
    broadcast: async () => ({ broadcastId: "stub" }),
    finishRun: async () => undefined,
  };
}
```

- [ ] **Step 4: Run the tests**

Run: `cd digest && npm test && npm run typecheck && npm run lint`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add digest/src/workflow/signals.ts digest/src/activities
git commit -m "feat(digest): the three signals and the activity interface plan A2 fills, with stubs"
```

---

### Task 9b: The DigestWorkflow with identity, one budget, the hold, and the retry park (spec §2.1, §2.3)

**Files:**
- Create: `digest/src/workflow/digest.workflow.ts`
- Test: `digest/src/workflow/digest.workflow.test.ts`

**Interfaces:**
- Consumes: task 9a's signals, `Activities`, `DigestInput`, `DigestOutput`, `stubActivities`.
- Produces: `DigestWorkflow(input: DigestInput): Promise<DigestOutput>`, `WORKFLOW_RUN_TIMEOUT = "4 hours"`, `HOLD_TIMEOUT = "2 hours"`, `workflowIdFor(runDate)`.

- [ ] **Step 1: Write the failing test**

```ts
// digest/src/workflow/digest.workflow.test.ts
import { WorkflowIdConflictPolicy } from "@temporalio/common";
import { TestWorkflowEnvironment } from "@temporalio/testing";
import { Worker } from "@temporalio/worker";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { stubActivities } from "../activities/stub.js";
import { DigestWorkflow, workflowIdFor } from "./digest.workflow.js";
import { approveSignal, retrySignal } from "./signals.js";

let env: TestWorkflowEnvironment;
beforeAll(async () => { env = await TestWorkflowEnvironment.createTimeSkipping(); }, 120_000);
afterAll(async () => { await env?.teardown(); });

const taskQueue = "digest-test";
async function withWorker<T>(fn: () => Promise<T>): Promise<T> {
  const worker = await Worker.create({ connection: env.nativeConnection, taskQueue, workflowsPath: new URL("./digest.workflow.ts", import.meta.url).pathname, activities: stubActivities() });
  return worker.runUntil(fn());
}

describe("DigestWorkflow", () => {
  it("runs every stage over stub activities and sends when approved", async () => {
    const out = await withWorker(async () => {
      const h = await env.client.workflow.start(DigestWorkflow, { taskQueue, workflowId: workflowIdFor("2026-09-21"), args: [{ runDate: "2026-09-21" }] });
      await h.signal(approveSignal, { decision: "approve" });
      return h.result();
    });
    expect(out).toMatchObject({ runId: 1, broadcast: "sent" });
    expect(out.stories).toBeGreaterThan(0);
  });
  it("proceeds after the hold times out with no signal", async () => {
    const out = await withWorker(async () => {
      const h = await env.client.workflow.start(DigestWorkflow, { taskQueue, workflowId: workflowIdFor("2026-09-22"), args: [{ runDate: "2026-09-22" }] });
      return h.result(); // time-skipping: the 2 h hold elapses without a signal
    });
    expect(out.broadcast).toBe("sent");
  });
  it("rejects a second start for the same day while one is running", async () => {
    await withWorker(async () => {
      const opts = { taskQueue, workflowId: workflowIdFor("2026-09-23"), args: [{ runDate: "2026-09-23" }], workflowIdConflictPolicy: WorkflowIdConflictPolicy.FAIL };
      const h = await env.client.workflow.start(DigestWorkflow, opts);
      await expect(env.client.workflow.start(DigestWorkflow, opts)).rejects.toThrow();
      await h.signal(approveSignal, { decision: "approve" });
      await h.result();
    });
  });
  it("parks on the retry signal when select fails non-retryably, and aborts on 'abort'", async () => {
    const out = await withWorker(async () => {
      const h = await env.client.workflow.start(DigestWorkflow, { taskQueue, workflowId: workflowIdFor("2026-09-24"), args: [{ runDate: "2026-09-24", failStage: "select" }] });
      await h.signal(retrySignal, { decision: "abort" });
      return h.result();
    });
    expect(out.broadcast).toBe("skipped");
  });
  it("a retry decision sent before the failure is not lost", async () => {
    const out = await withWorker(async () => {
      const h = await env.client.workflow.start(DigestWorkflow, { taskQueue, workflowId: workflowIdFor("2026-09-25"), args: [{ runDate: "2026-09-25", failStage: "select" }] });
      await h.signal(retrySignal, { decision: "abort" }); // may arrive before select runs
      return h.result();
    });
    expect(out.broadcast).toBe("skipped");
  });
});
```

The duplicate-start test asserts only that the second start rejects; the error class and message are the SDK's (`WorkflowExecutionAlreadyStartedError` in `@temporalio/common` if task 5 found it; do not assert on message text).

- [ ] **Step 2: Run it to verify it fails**

Run: `cd digest && npm test -- workflow`
Expected: FAIL, module not found. (The time-skipping environment downloads a test server binary on first run; allow it once and commit nothing from it.)

- [ ] **Step 3: Write the workflow**

```ts
// digest/src/workflow/digest.workflow.ts
import { ApplicationFailure, condition, proxyActivities, setHandler } from "@temporalio/workflow";
import type { Activities, DigestInput, DigestOutput } from "../activities/index.js";
import { SOURCE_IDS_STUB } from "../activities/index.js";
import { approveSignal, operatorNoteSignal, retrySignal } from "./signals.js";

export const WORKFLOW_RUN_TIMEOUT = "4 hours";
export const HOLD_TIMEOUT = "2 hours";
export const workflowIdFor = (runDate: string): string => `digest-${runDate}`;

// Model calls: bounded retries inside the outage-sized run budget. Network: quick retries.
// Verdicts and the send: one attempt (a verdict is a result; a send is at-most-once).
const model = proxyActivities<Activities>({ startToCloseTimeout: "45 minutes", heartbeatTimeout: "2 minutes", retry: { maximumAttempts: 3, initialInterval: "5 minutes", backoffCoefficient: 2 } });
const network = proxyActivities<Activities>({ startToCloseTimeout: "2 minutes", retry: { maximumAttempts: 3, initialInterval: "10 seconds" } });
const once = proxyActivities<Activities>({ startToCloseTimeout: "10 minutes", retry: { maximumAttempts: 1 } });

export async function DigestWorkflow(input: DigestInput): Promise<DigestOutput> {
  if (input.resumeRun !== undefined && !input.force) throw ApplicationFailure.nonRetryable("resumeRun requires force", "BadInput");
  let approval: "approve" | "reject" | undefined;
  const retryDecisions: ("retry" | "abort")[] = []; // a queue: a decision sent before the failure is kept
  const notes: Record<string, string> = {};
  setHandler(approveSignal, ({ decision }) => { approval = decision; });
  setHandler(retrySignal, ({ decision }) => { retryDecisions.push(decision); });
  setHandler(operatorNoteSignal, ({ stage, note }) => { notes[stage] = note; });

  const { runId } = await once.startRun(input);

  // Retries exhausted: park on the retry signal (spec §2.3 signal 2). Returns undefined on abort.
  async function guarded<T>(fn: () => Promise<T>): Promise<T | undefined> {
    for (;;) {
      try {
        return await fn();
      } catch {
        await condition(() => retryDecisions.length > 0);
        if (retryDecisions.shift() === "abort") return undefined;
      }
    }
  }

  const fetched = await Promise.all(SOURCE_IDS_STUB.map((s) => network.fetchFeed(runId, s)));
  const { articles } = await once.prepare(runId, fetched);
  const [clusters, recap] = await Promise.all([model.cluster(runId, articles), model.recap(runId)]);
  const selected = await guarded(() => model.select(runId, clusters, recap, notes["select"], input));
  if (!selected) return finish(runId, { stories: 0, broadcast: "skipped" });
  const fulltext = await network.fulltext(runId, selected);
  const storyCount = 3; // plan A2: read from the selected artifact
  const drafts = await Promise.all(Array.from({ length: storyCount }, (_, i) => model.writeStory(runId, i, selected, fulltext, notes["write"])));
  const [preheader, report] = await Promise.all([model.preheader(runId, drafts), model.coherence(runId, drafts, fulltext, notes["coherence"])]);
  const repair = await model.repair(runId, drafts, report);
  const selections = await once.assemble(runId, drafts, report, repair, preheader);
  const [gnews, threads] = await Promise.all([network.gnews(runId, selections), model.threads(runId, selections)]);
  const { email } = await once.render(runId, selections, threads, gnews);

  // Pre-broadcast hold (spec §2.3 signal 1): 2 h, then proceed.
  await condition(() => approval !== undefined, HOLD_TIMEOUT);
  if (approval === "reject") return finish(runId, { stories: storyCount, broadcast: "rejected" });
  await once.broadcast(runId, email);
  return finish(runId, { stories: storyCount, broadcast: "sent" });

  async function finish(id: number, out: Omit<DigestOutput, "runId">): Promise<DigestOutput> {
    await once.finishRun(id, out);
    return { runId: id, ...out };
  }
}
```

`guarded` wraps only `select` in this plan; plan A2 wraps every model stage. The `input` argument on `select` exists so the stub can be told to fail; plan A2's real `select` ignores it.

- [ ] **Step 4: Run the tests**

Run: `cd digest && npm test -- workflow && npm run typecheck && npm run lint`
Expected: PASS, five tests.

- [ ] **Step 5: Commit**

```bash
git add digest/src/workflow/digest.workflow.ts digest/src/workflow/digest.workflow.test.ts
git commit -m "feat(digest): the DigestWorkflow: identity per day, one 4 h budget, the hold, and a retry park that keeps an early decision"
```

---

### Task 9c: Worker, client, and the schedule (spec §2.1)

**Files:**
- Create: `digest/src/worker.ts`, `digest/src/client.ts`
- Test: `digest/src/client.test.ts`

**Interfaces:**
- Produces: `TASK_QUEUE = "digest"`; `runWorker(address?)`; `startOptions(runDate, opts) -> WorkflowOptions` (pure, tested) and `startDigest(runDate, opts?, address?)`; `ensureSchedule(address?)`.

- [ ] **Step 1: Write the failing test**

```ts
// digest/src/client.test.ts
import { WorkflowIdConflictPolicy, WorkflowIdReusePolicy } from "@temporalio/common";
import { describe, expect, it } from "vitest";
import { startOptions } from "./client.js";

describe("startOptions", () => {
  it("a normal day rejects duplicates while running and after completion", () => {
    const o = startOptions("2026-09-21", {});
    expect(o.workflowId).toBe("digest-2026-09-21");
    expect(o.workflowIdConflictPolicy).toBe(WorkflowIdConflictPolicy.FAIL);
    expect(o.workflowIdReusePolicy).toBe(WorkflowIdReusePolicy.REJECT_DUPLICATE);
    expect(o.workflowRunTimeout).toBe("4 hours");
  });
  it("a forced re-run may reuse the id of a completed run, never a running one", () => {
    const o = startOptions("2026-09-21", { force: true, resumeRun: 303 });
    expect(o.workflowIdReusePolicy).toBe(WorkflowIdReusePolicy.ALLOW_DUPLICATE);
    expect(o.workflowIdConflictPolicy).toBe(WorkflowIdConflictPolicy.FAIL);
    expect(o.args).toEqual([{ runDate: "2026-09-21", force: true, resumeRun: 303 }]);
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd digest && npm test -- client`
Expected: FAIL.

- [ ] **Step 3: Write the worker and client**

```ts
// digest/src/worker.ts
import { NativeConnection, Worker } from "@temporalio/worker";
import { stubActivities } from "./activities/stub.js";
export const TASK_QUEUE = "digest";
export async function runWorker(address = process.env["TEMPORAL_ADDRESS"] ?? "localhost:7233"): Promise<void> {
  const connection = await NativeConnection.connect({ address });
  const worker = await Worker.create({ connection, taskQueue: TASK_QUEUE, workflowsPath: new URL("./workflow/digest.workflow.js", import.meta.url).pathname, activities: stubActivities() });
  await worker.run();
}
if (process.argv[1]?.endsWith("worker.js")) await runWorker();
```

```ts
// digest/src/client.ts
import { Client, Connection, ScheduleOverlapPolicy, type WorkflowStartOptions } from "@temporalio/client";
import { WorkflowIdConflictPolicy, WorkflowIdReusePolicy } from "@temporalio/common";
import type { DigestInput } from "./activities/index.js";
import { TASK_QUEUE } from "./worker.js";
import { DigestWorkflow, WORKFLOW_RUN_TIMEOUT, workflowIdFor } from "./workflow/digest.workflow.js";

export type StartOpts = { resumeRun?: number; force?: boolean };

export function startOptions(runDate: string, opts: StartOpts): WorkflowStartOptions<typeof DigestWorkflow> {
  const input: DigestInput = { runDate, ...(opts.force ? { force: true } : {}), ...(opts.resumeRun !== undefined ? { resumeRun: opts.resumeRun } : {}) };
  return {
    taskQueue: TASK_QUEUE,
    workflowId: workflowIdFor(runDate),
    args: [input],
    workflowRunTimeout: WORKFLOW_RUN_TIMEOUT,
    workflowIdConflictPolicy: WorkflowIdConflictPolicy.FAIL,
    workflowIdReusePolicy: opts.force ? WorkflowIdReusePolicy.ALLOW_DUPLICATE : WorkflowIdReusePolicy.REJECT_DUPLICATE,
  };
}

async function client(address: string): Promise<Client> {
  return new Client({ connection: await Connection.connect({ address }) });
}

export async function startDigest(runDate: string, opts: StartOpts = {}, address = process.env["TEMPORAL_ADDRESS"] ?? "localhost:7233") {
  return (await client(address)).workflow.start(DigestWorkflow, startOptions(runDate, opts));
}

export async function ensureSchedule(address = process.env["TEMPORAL_ADDRESS"] ?? "localhost:7233") {
  await (await client(address)).schedule.create({
    scheduleId: "digest-daily",
    spec: { calendars: [{ hour: 10, minute: 25 }] }, // UTC
    policies: { overlap: ScheduleOverlapPolicy.SKIP, catchupWindow: "1 day" },
    action: { type: "startWorkflow", workflowType: DigestWorkflow, taskQueue: TASK_QUEUE, workflowId: "digest-scheduled", args: [{ runDate: "" }] },
  });
}
```

If `WorkflowStartOptions` is not the exported name (task 5's `api-names.md`), use the generic the `.d.ts` declares for `client.workflow.start`'s second parameter. The scheduled action's `workflowId` is fixed; plan A2's `startRun` derives the run date from the workflow's start time when `runDate` is empty, and the schedule's overlap policy covers scheduled starts.

- [ ] **Step 4: Run the tests**

Run: `cd digest && npm test && npm run typecheck && npm run lint`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add digest/src/worker.ts digest/src/client.ts digest/src/client.test.ts
git commit -m "feat(digest): worker, client and the daily schedule; start options encode the id policy the spec states"
```

---

### Task 10: Local Temporal by Docker Compose, pinned, with the worker as a service

**Files:**
- Create: `digest/compose.temporal.yml`, `digest/Dockerfile`, `digest/src/cli/start.ts`
- Modify: `Makefile` (targets `temporal-up`, `temporal-down`, `digest-start`)

**Interfaces:**
- Produces: `make temporal-up` (Temporal 1.32.0 on Postgres 16, the UI on 127.0.0.1:8233, the worker); `make digest-start DATE=2026-09-21` starts one workflow and prints its result.

- [ ] **Step 1: Write the compose file**

```yaml
# digest/compose.temporal.yml
services:
  postgres:
    image: postgres:16-alpine
    environment: { POSTGRES_USER: temporal, POSTGRES_PASSWORD: temporal }
    volumes: [temporal-pg:/var/lib/postgresql/data]
    mem_limit: 512m
  temporal:
    image: temporalio/auto-setup:1.32.0
    depends_on: [postgres]
    environment: { DB: postgres12, DB_PORT: "5432", POSTGRES_USER: temporal, POSTGRES_PWD: temporal, POSTGRES_SEEDS: postgres }
    ports: ["127.0.0.1:7233:7233"]
    mem_limit: 1g
  temporal-ui:
    image: temporalio/ui:2.42.1
    depends_on: [temporal]
    environment: { TEMPORAL_ADDRESS: temporal:7233 }
    ports: ["127.0.0.1:8233:8080"]
  digest-worker:
    build: { context: .., dockerfile: digest/Dockerfile }
    depends_on: [temporal]
    environment: { TEMPORAL_ADDRESS: temporal:7233 }
    volumes:
      - ../data:/app/data
      - claude-sessions:/home/appuser/.claude
    mem_limit: 512m
volumes:
  temporal-pg: {}
  claude-sessions:
    external: true
    name: news-digest_claude-sessions
```

`news-digest_claude-sessions` is the compose project's name for the `claude-sessions` volume at `docker-compose.yml:129`; confirm with `docker volume ls | grep claude-sessions`.

- [ ] **Step 2: Write the worker image and the start CLI**

```dockerfile
# digest/Dockerfile
FROM node:22-slim
RUN useradd -m appuser
WORKDIR /app/digest
COPY digest/package.json digest/package-lock.json ./
RUN npm ci --omit=dev
COPY digest/ ./
RUN npm run build
USER appuser
CMD ["node", "dist/worker.js"]
```

```ts
// digest/src/cli/start.ts
import { startDigest } from "../client.js";
const date = process.argv[2];
if (!date) throw new Error("usage: start <YYYY-MM-DD> [--force] [--resume N]");
const force = process.argv.includes("--force");
const r = process.argv.indexOf("--resume");
const resumeRun = r > 0 ? Number(process.argv[r + 1]) : undefined;
const handle = await startDigest(date, { force, ...(resumeRun !== undefined ? { resumeRun } : {}) });
console.log(`started ${handle.workflowId}`);
console.log(JSON.stringify(await handle.result()));
```

Makefile targets (append, following the file's `## ` help convention):

```make
temporal-up: ## Local Temporal 1.32.0 + Postgres + UI (:8233) + the digest worker
	docker compose -f digest/compose.temporal.yml up -d --build
temporal-down: ## Stop local Temporal; keeps the Postgres volume
	docker compose -f digest/compose.temporal.yml down
digest-start: ## Start one DigestWorkflow on local Temporal (usage: make digest-start DATE=2026-09-21)
	docker compose -f digest/compose.temporal.yml run --rm digest-worker node dist/cli/start.js $(DATE)
```

- [ ] **Step 3: Bring it up and run one workflow end to end on stubs**

Run: `make temporal-up && sleep 20 && make digest-start DATE=2026-09-21`
Expected: `started digest-2026-09-21`, then the process blocks on the hold. In the UI at http://127.0.0.1:8233 open the workflow and send signal `approve` with `{"decision":"approve"}`; the first shell prints `{"runId":1,"stories":3,"broadcast":"sent"}`. Then `make temporal-down`.

- [ ] **Step 4: Record memory while it is up**

Run: `docker stats --no-stream --format "{{.Name}}\t{{.MemUsage}}"` and paste the four lines into `digest/README.md` under "Footprint, local, stubs".

- [ ] **Step 5: Commit**

```bash
git add digest/compose.temporal.yml digest/Dockerfile digest/src/cli/start.ts Makefile digest/README.md
git commit -m "build(digest): local Temporal pinned at 1.32.0 with the worker as a service; one workflow runs end to end on stubs"
```

---

### Task 11: The activity-runner CLI the evals call (spec §4)

**Files:**
- Create: `digest/src/cli/run-stage.ts`
- Test: `digest/src/cli/run-stage.test.ts`

**Interfaces:**
- Produces: `node dist/cli/run-stage.js --agent <path.md> --input-dir <dir> --today YYYY-MM-DD [--schema coherence] [--inline-corpus]`, printing `StageResult` as JSON; `buildInvocation(argv)` and `inlineCorpus(dir)` exported for tests. Never touches Temporal.

- [ ] **Step 1: Write the failing test**

```ts
// digest/src/cli/run-stage.test.ts
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { buildInvocation, inlineCorpus } from "./run-stage.js";

describe("run-stage CLI", () => {
  it("builds the invocation from flags", () => {
    const inv = buildInvocation(["--agent", "/a/coherence.md", "--input-dir", "/in", "--today", "2026-09-21", "--schema", "coherence"]);
    expect(inv).toEqual({ agentPath: "/a/coherence.md", inputDir: "/in", today: "2026-09-21", schema: "coherence", inlineCorpus: false });
  });
  it("refuses an unknown schema name and a missing flag", () => {
    expect(() => buildInvocation(["--agent", "/a", "--input-dir", "/in", "--today", "2026-09-21", "--schema", "nope"])).toThrow(/schema/);
    expect(() => buildInvocation(["--agent", "/a"])).toThrow(/input-dir/);
  });
  it("inlines the draft, the article CSVs and the fulltext, in name order, and nothing else", () => {
    const d = mkdtempSync(join(tmpdir(), "corpus-"));
    writeFileSync(join(d, "articles_2.csv"), "two");
    writeFileSync(join(d, "articles_1.csv"), "one");
    writeFileSync(join(d, "draft_selections.json"), "{}");
    writeFileSync(join(d, "coherence_report.json"), "{}");
    const c = inlineCorpus(d);
    expect(c.indexOf("## articles_1.csv")).toBeLessThan(c.indexOf("## articles_2.csv"));
    expect(c).toContain("## draft_selections.json");
    expect(c).not.toContain("coherence_report");
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd digest && npm test -- cli/run-stage`
Expected: FAIL.

- [ ] **Step 3: Write the CLI**

```ts
// digest/src/cli/run-stage.ts
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { coherenceReportJsonSchema } from "../contracts/coherence.js";
import { parseAgentSpec } from "../runner/prompt.js";
import { runStage } from "../runner/run-stage.js";

export interface Invocation { agentPath: string; inputDir: string; today: string; schema?: "coherence"; inlineCorpus: boolean }

export function buildInvocation(argv: string[]): Invocation {
  const get = (flag: string): string | undefined => { const i = argv.indexOf(flag); return i >= 0 ? argv[i + 1] : undefined; };
  const agentPath = get("--agent"); const inputDir = get("--input-dir"); const today = get("--today");
  if (!agentPath) throw new Error("--agent is required");
  if (!inputDir) throw new Error("--input-dir is required");
  if (!today) throw new Error("--today is required");
  const schema = get("--schema");
  if (schema !== undefined && schema !== "coherence") throw new Error(`unknown schema ${schema}`);
  return { agentPath, inputDir, today, ...(schema ? { schema } : {}), inlineCorpus: argv.includes("--inline-corpus") };
}

export function inlineCorpus(inputDir: string): string {
  const names = readdirSync(inputDir).filter((n) => n === "draft_selections.json" || /^articles_\d+\.csv$/.test(n) || n === "article_fulltext.json").sort();
  return names.map((n) => `## ${n}\n\n${readFileSync(join(inputDir, n), "utf8")}`).join("\n\n");
}

if (process.argv[1]?.endsWith("run-stage.js")) {
  const inv = buildInvocation(process.argv.slice(2));
  const spec = parseAgentSpec(readFileSync(inv.agentPath, "utf8"));
  const result = await runStage(spec, { userMessage: inv.inlineCorpus ? inlineCorpus(inv.inputDir) : "Begin.", inputDir: inv.inputDir }, {
    today: inv.today,
    ...(inv.schema === "coherence" ? { outputSchema: coherenceReportJsonSchema() } : {}),
  });
  process.stdout.write(JSON.stringify(result));
}
```

- [ ] **Step 4: Run the tests**

Run: `cd digest && npm test && npm run typecheck && npm run lint`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add digest/src/cli/run-stage.ts digest/src/cli/run-stage.test.ts
git commit -m "feat(digest): the activity-runner CLI; evals call the stage primitive, never the workflow"
```

---

### Task 12a: Gate scoring, pure: planted inputs, recall, bands, disagreements (spec §7)

**Files:**
- Create: `digest/src/gate/plant.ts`, `digest/src/gate/band.ts`, `digest/src/gate/score.ts`, `digest/src/gate/verdict.ts`
- Test: `digest/src/gate/plant.test.ts`, `digest/src/gate/band.test.ts`, `digest/src/gate/score.test.ts`

**Interfaces:**
- Produces:
  ```ts
  // verdict.ts
  export type Criterion = 1 | 2 | 3 | 4 | 5 | 6 | 7;
  export interface JudgeVerdict { story: number; criterion: Criterion; pass: boolean; reason: string }
  // plant.ts
  export interface Plant { storyIndex: number; field: "headline" | "summary" | "why_it_matters"; kind: "wrong-number" | "absent-specific"; original: string; planted: string }
  export function plantDefects(draft: Selections, seed: number, n: number): { draft: Selections; plants: Plant[] };
  // band.ts
  export function selfAgreement(runs: JudgeVerdict[][]): { perCriterion: Record<number, number>; overall: number };
  // score.ts
  export function recallOnPlants(plants: Plant[], report: CoherenceReport): { caught: number; total: number };
  export function disagreements(a: JudgeVerdict[], b: JudgeVerdict[]): { story: number; criterion: Criterion }[];
  ```

- [ ] **Step 1: Write the failing tests**

```ts
// digest/src/gate/plant.test.ts
import { describe, expect, it } from "vitest";
import { plantDefects } from "./plant.js";

// Every field carries a number, so every plant is a wrong-number plant and the shape check always runs.
const draft = {
  must_know: [
    { headline: "Talks resume after 12 days", summary: "Officials said 3,000 attended.", why_it_matters: "The 2 sides differ.", sources: [{ article_id: "A1" }] },
    { headline: "Vote passes 58 to 40", summary: "Turnout was 58%.", why_it_matters: "A 9 point swing.", sources: [{ article_id: "A2" }] },
  ],
  should_know: [],
  preheader: "p",
};

describe("plantDefects", () => {
  it("is deterministic for a seed and changes exactly n distinct fields", () => {
    const a = plantDefects(draft, 7, 2);
    expect(plantDefects(draft, 7, 2).plants).toEqual(a.plants);
    expect(a.plants).toHaveLength(2);
    expect(new Set(a.plants.map((p) => `${p.storyIndex}:${p.field}`)).size).toBe(2);
    for (const p of a.plants) expect(p.original).not.toBe(p.planted);
  });
  it("a wrong-number plant changes a number and nothing else in the field", () => {
    const { plants } = plantDefects(draft, 1, 3);
    for (const p of plants) {
      expect(p.kind).toBe("wrong-number");
      expect(p.planted.replace(/\d[\d,]*/g, "#")).toBe(p.original.replace(/\d[\d,]*/g, "#"));
    }
  });
  it("a field with no number gets an absent specific appended", () => {
    const noNumbers = { ...draft, must_know: [{ ...draft.must_know[0]!, headline: "Talks resume", summary: "Officials attended.", why_it_matters: "Sides differ." }] };
    const { plants } = plantDefects(noNumbers, 3, 1);
    expect(plants[0]!.kind).toBe("absent-specific");
    expect(plants[0]!.planted.startsWith(plants[0]!.original)).toBe(true);
  });
});
```

```ts
// digest/src/gate/band.test.ts
import { describe, expect, it } from "vitest";
import { selfAgreement } from "./band.js";
import type { Criterion } from "./verdict.js";
const v = (story: number, criterion: Criterion, pass: boolean) => ({ story, criterion, pass, reason: "" });
describe("selfAgreement", () => {
  it("is 1 when every run agrees and 0.5 when half the cells flip", () => {
    expect(selfAgreement([[v(0, 1, true), v(0, 2, true)], [v(0, 1, true), v(0, 2, true)]]).overall).toBe(1);
    const half = selfAgreement([[v(0, 1, true), v(0, 2, true)], [v(0, 1, true), v(0, 2, false)]]);
    expect(half.overall).toBe(0.5);
    expect(half.perCriterion).toEqual({ 1: 1, 2: 0 });
  });
});
```

```ts
// digest/src/gate/score.test.ts
import { describe, expect, it } from "vitest";
import { disagreements, recallOnPlants } from "./score.js";
describe("score", () => {
  it("recall counts a plant caught when its story's field is flagged", () => {
    const plants = [{ storyIndex: 1, field: "summary" as const, kind: "wrong-number" as const, original: "58%", planted: "117%" }];
    const report = { results: [{ headline: "Talks", article_ids: ["A1"], pass: true, reason: "ok" }, { headline: "Vote", article_ids: ["A2"], pass: false, reason: "summary: x", failed_fields: ["summary" as const] }] };
    expect(recallOnPlants(plants, report)).toEqual({ caught: 1, total: 1 });
    expect(recallOnPlants([{ ...plants[0]!, field: "headline" }], report)).toEqual({ caught: 0, total: 1 });
  });
  it("disagreements are the cells where two judges differ", () => {
    const a = [{ story: 0, criterion: 1 as const, pass: true, reason: "" }, { story: 0, criterion: 2 as const, pass: true, reason: "" }];
    const b = [{ story: 0, criterion: 1 as const, pass: false, reason: "" }, { story: 0, criterion: 2 as const, pass: true, reason: "" }];
    expect(disagreements(a, b)).toEqual([{ story: 0, criterion: 1 }]);
  });
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd digest && npm test -- gate`
Expected: FAIL.

- [ ] **Step 3: Write the modules**

```ts
// digest/src/gate/verdict.ts
export type Criterion = 1 | 2 | 3 | 4 | 5 | 6 | 7;
export interface JudgeVerdict { story: number; criterion: Criterion; pass: boolean; reason: string }
```

```ts
// digest/src/gate/plant.ts
import type { Selections } from "../contracts/selections.js";
export interface Plant { storyIndex: number; field: "headline" | "summary" | "why_it_matters"; kind: "wrong-number" | "absent-specific"; original: string; planted: string }

function rng(seed: number): () => number {
  let s = seed >>> 0;
  return () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296; };
}
const NUMBER = /\d[\d,]*/;
const FIELDS = ["headline", "summary", "why_it_matters"] as const;

export function plantDefects(draft: Selections, seed: number, n: number): { draft: Selections; plants: Plant[] } {
  const next = rng(seed);
  const out: Selections = structuredClone(draft);
  const plants: Plant[] = [];
  let guard = 0;
  while (plants.length < n && guard++ < 1000) {
    const i = Math.floor(next() * out.must_know.length);
    const story = out.must_know[i];
    if (!story) continue;
    const field = FIELDS[Math.floor(next() * FIELDS.length)]!;
    const original = story[field];
    if (plants.some((p) => p.storyIndex === i && p.field === field)) continue;
    const m = NUMBER.exec(original);
    let planted: string;
    let kind: Plant["kind"];
    if (m) {
      const num = Number(m[0].replaceAll(",", ""));
      planted = original.replace(NUMBER, String(num * 2 + 1));
      kind = "wrong-number";
    } else {
      planted = `${original} The measure was announced in Geneva.`;
      kind = "absent-specific";
    }
    story[field] = planted;
    plants.push({ storyIndex: i, field, kind, original, planted });
  }
  return { draft: out, plants };
}
```

```ts
// digest/src/gate/band.ts
import type { JudgeVerdict } from "./verdict.js";
export function selfAgreement(runs: JudgeVerdict[][]): { perCriterion: Record<number, number>; overall: number } {
  const cells = new Map<string, boolean[]>();
  for (const run of runs) for (const v of run) { const k = `${v.story}:${v.criterion}`; cells.set(k, [...(cells.get(k) ?? []), v.pass]); }
  const byCriterion: Record<number, number[]> = {};
  let agree = 0;
  for (const [k, votes] of cells) {
    const same = votes.every((x) => x === votes[0]) ? 1 : 0;
    agree += same;
    const c = Number(k.split(":")[1]);
    (byCriterion[c] ??= []).push(same);
  }
  const avg = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0);
  return { perCriterion: Object.fromEntries(Object.entries(byCriterion).map(([c, xs]) => [Number(c), avg(xs)])), overall: cells.size ? agree / cells.size : 0 };
}
```

```ts
// digest/src/gate/score.ts
import type { CoherenceReport } from "../contracts/coherence.js";
import type { Plant } from "./plant.js";
import type { Criterion, JudgeVerdict } from "./verdict.js";
export function recallOnPlants(plants: Plant[], report: CoherenceReport): { caught: number; total: number } {
  let caught = 0;
  for (const p of plants) {
    const r = report.results[p.storyIndex];
    if (r && r.pass === false && (r.failed_fields ?? []).includes(p.field)) caught++;
  }
  return { caught, total: plants.length };
}
export function disagreements(a: JudgeVerdict[], b: JudgeVerdict[]): { story: number; criterion: Criterion }[] {
  const bs = new Map(b.map((v) => [`${v.story}:${v.criterion}`, v.pass]));
  return a.filter((v) => { const k = `${v.story}:${v.criterion}`; return bs.has(k) && bs.get(k) !== v.pass; }).map((v) => ({ story: v.story, criterion: v.criterion }));
}
```

`recallOnPlants` scores by story index in draft order; the runner's structured output keeps order, and plan A2 pins that with a test where the coherence activity lands.

- [ ] **Step 4: Run the tests**

Run: `cd digest && npm test && npm run typecheck && npm run lint`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add digest/src/gate
git commit -m "feat(digest): gate scoring, pure: planted defects, recall, self-agreement bands, disagreements"
```

---

### Task 12b: Judges as CLIs, the gate CLI, promptfoo, and one live band (spec §7.2)

**Files:**
- Create: `digest/src/gate/judge.ts`, `digest/src/cli/gate.ts`, `digest/gate/rubric.md`, `digest/gate/judges.json`, `digest/gate/promptfooconfig.yaml`
- Test: `digest/src/gate/judge.test.ts`

**Interfaces:**
- Produces:
  ```ts
  export interface Judge { name: string; family: "anthropic" | "openai" | "google"; run(digestHtml: string, inputsDir: string): Promise<JudgeVerdict[]> }
  export function cliJudge(name: string, family: Judge["family"], command: string[]): Judge;  // spawns `command`, writes {rubric, digest, inputsDir} JSON to stdin, parses the JSON array of verdicts from stdout
  export function parseVerdicts(stdout: string): JudgeVerdict[];                                // exported for the test; the outermost [...] in stdout
  ```

- [ ] **Step 1: Write the failing test**

```ts
// digest/src/gate/judge.test.ts
import { describe, expect, it } from "vitest";
import { cliJudge, parseVerdicts } from "./judge.js";

describe("judge", () => {
  it("parses the outermost JSON array out of chatty stdout", () => {
    const out = 'thinking...\n[{"story":0,"criterion":1,"pass":true,"reason":"ok"}]\ndone';
    expect(parseVerdicts(out)).toEqual([{ story: 0, criterion: 1, pass: true, reason: "ok" }]);
    expect(() => parseVerdicts("no json here")).toThrow(/array/);
  });
  it("runs a command, feeds it the rubric and digest on stdin, and returns its verdicts", async () => {
    // `cat` echoes stdin; a judge that returns its own input as a verdict list proves the plumbing.
    const j = cliJudge("echo", "openai", ["node", "-e", "let s='';process.stdin.on('data',d=>s+=d).on('end',()=>{const i=JSON.parse(s);console.log(JSON.stringify([{story:0,criterion:1,pass:i.digest==='<p>x</p>',reason:i.rubric.slice(0,3)}]))})"]);
    const v = await j.run("<p>x</p>", "/inputs");
    expect(v).toEqual([{ story: 0, criterion: 1, pass: true, reason: expect.any(String) }]);
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd digest && npm test -- judge`
Expected: FAIL.

- [ ] **Step 3: Write the judge module, the rubric, and the CLI**

```ts
// digest/src/gate/judge.ts
import { spawn } from "node:child_process";
import { readFileSync } from "node:fs";
import type { JudgeVerdict } from "./verdict.js";
export interface Judge { name: string; family: "anthropic" | "openai" | "google"; run(digestHtml: string, inputsDir: string): Promise<JudgeVerdict[]> }
export const RUBRIC = readFileSync(new URL("../../gate/rubric.md", import.meta.url), "utf8");

export function parseVerdicts(stdout: string): JudgeVerdict[] {
  const start = stdout.indexOf("["); const end = stdout.lastIndexOf("]");
  if (start < 0 || end < start) throw new Error("judge returned no JSON array");
  return JSON.parse(stdout.slice(start, end + 1)) as JudgeVerdict[];
}

export function cliJudge(name: string, family: Judge["family"], command: string[]): Judge {
  return {
    name, family,
    run: (digestHtml, inputsDir) => new Promise((resolve, reject) => {
      const [cmd, ...args] = command;
      if (!cmd) return reject(new Error("empty judge command"));
      const p = spawn(cmd, args, { stdio: ["pipe", "pipe", "inherit"] });
      let out = "";
      p.stdout.on("data", (d: Buffer) => { out += d.toString(); });
      p.on("error", reject);
      p.on("close", (code) => {
        if (code !== 0) return reject(new Error(`${name} exited ${code}`));
        try { resolve(parseVerdicts(out)); } catch (e) { reject(e instanceof Error ? e : new Error(String(e))); }
      });
      p.stdin.end(JSON.stringify({ rubric: RUBRIC, digest: digestHtml, inputsDir }));
    }),
  };
}
```

`digest/gate/rubric.md`: spec §7.1 verbatim, preceded by one instruction: "Return a JSON array of `{story, criterion, pass, reason}` objects, one per story per criterion, and nothing else. `story` is the 0-based index in the digest's must_know order."

`digest/gate/judges.json` starts as `[]`; fill it in step 5 with the two commands that work on the box, one Anthropic (`claude -p --model claude-opus-5 --output-format text` reading stdin) and one from another family (`codex exec` or `gemini -p`, read `--help` on the box), each wrapped so the rubric and digest reach the model from stdin.

```ts
// digest/src/cli/gate.ts
import { readFileSync, writeFileSync } from "node:fs";
import { selfAgreement } from "../gate/band.js";
import { cliJudge, type Judge } from "../gate/judge.js";
import { disagreements } from "../gate/score.js";
import type { JudgeVerdict } from "../gate/verdict.js";

// usage: gate --digest digest.html --inputs <dir> --judges gate/judges.json --reps 5 --out band.json
const get = (f: string): string => { const i = process.argv.indexOf(f); const v = i >= 0 ? process.argv[i + 1] : undefined; if (!v) throw new Error(`${f} is required`); return v; };
const digest = readFileSync(get("--digest"), "utf8");
const inputs = get("--inputs");
const reps = Number(process.argv.includes("--reps") ? get("--reps") : 5);
const judges: Judge[] = (JSON.parse(readFileSync(get("--judges"), "utf8")) as { name: string; family: Judge["family"]; command: string[] }[]).map((j) => cliJudge(j.name, j.family, j.command));
if (new Set(judges.map((j) => j.family)).size < 2) throw new Error("the gate needs judges from two families (spec §7.2)");
const out: Record<string, unknown> = {};
const first: Record<string, JudgeVerdict[]> = {};
for (const j of judges) {
  const runs: JudgeVerdict[][] = [];
  for (let i = 0; i < reps; i++) runs.push(await j.run(digest, inputs));
  first[j.name] = runs[0]!;
  out[j.name] = { band: selfAgreement(runs), runs };
}
const [a, b] = judges;
const dis = a && b ? disagreements(first[a.name]!, first[b.name]!) : [];
out["disagreements"] = dis;
writeFileSync(process.argv.includes("--out") ? get("--out") : "band.json", JSON.stringify(out, null, 2));
console.log(JSON.stringify({ judges: judges.map((j) => j.name), disagreements: dis.length }));
```

```yaml
# digest/gate/promptfooconfig.yaml
description: whole-digest judges, band-first (spec §7). promptfoo is the runner; the protocol is ours.
prompts:
  - file://rubric.md
providers:
  - id: exec:node dist/cli/gate.js --digest {{digest}} --inputs {{inputs}} --judges gate/judges.json --reps 5 --out /tmp/band.json
tests:
  - vars: { digest: ../docs/proposed/gate-fixtures/day-301/digest.html, inputs: ../docs/proposed/gate-fixtures/day-301/inputs }
    assert:
      - type: javascript
        value: JSON.parse(require('fs').readFileSync('/tmp/band.json','utf8')).disagreements.length <= 3
```

If the installed promptfoo names its shell provider differently from `exec:` (`npx promptfoo@0.123.1 providers --help`), use its name. The fixture directory is created in step 5; until then this config is the shape, and `npx promptfoo eval` is not run.

- [ ] **Step 4: Run the tests**

Run: `cd digest && npm test && npm run typecheck && npm run lint`
Expected: PASS.

- [ ] **Step 5: One live band, opt-in (~$3 to $6, model calls)**

Create the fixture: `bin/replay RUN=301` renders a digest from run 301's archived artifacts with no model calls; copy its rendered HTML to `docs/proposed/gate-fixtures/day-301/digest.html` and the run's archived inputs (`bin/ops artifact 301 <name>` for `draft_selections.json`, `articles_*.csv`, `article_fulltext.json`, or a `db.get_run_artifacts(301)` dump) to `docs/proposed/gate-fixtures/day-301/inputs/`. Fill `digest/gate/judges.json` with two working commands. Run: `cd digest && npm run build && node dist/cli/gate.js --digest ../docs/proposed/gate-fixtures/day-301/digest.html --inputs ../docs/proposed/gate-fixtures/day-301/inputs --judges gate/judges.json --reps 5 --out ../docs/proposed/gate-fixtures/day-301/band.json`. Record both bands in `docs/proposed/gate-fixtures/day-301/README.md`; a criterion with agreement under 0.8 is written up as "not usable as a gate criterion until the rubric is tightened".

- [ ] **Step 6: Commit**

```bash
git add digest/src/gate/judge.ts digest/src/gate/judge.test.ts digest/src/cli/gate.ts digest/gate docs/proposed/gate-fixtures/day-301
git commit -m "feat(digest): judges as CLIs from two families, the gate CLI, and the first live band"
```

---

## Self-review against the spec

- **§1 contracts** → task 6 (ids, selections mirrored exactly, coherence, draft-07), task 3 (all ten prompts as files), task 1 (archive closure). DB tables and fail-closed are consumed as-is by tasks 7 and 9; the parsing boundary and run date are enforced by tasks 8 and 9b's inputs.
- **§2.1** → task 7 (blobs, pointers, integrity, conflict, quarantine, replace only on force), task 9a/9b (identity per day, one 4 h run timeout, three retry classes, prepare as its own activity, gnews after assemble in parallel with threads), task 9c (id policies, schedule), task 10 (pinned server). Heartbeats: 9b sets `heartbeatTimeout` on model activities; plan A2's activities must call `heartbeat()`.
- **§2.2** → task 8 (Write gone by parser contract, tools scoped by allow and disallow lists, schema on the final message, structured output required when asked, tool calls on record), task 11 (the inline-corpus option). The inline-vs-Read fork is decided in plan A2 with the task 11 CLI and the existing Python band harness.
- **§2.3** → task 9b (three signals, 2 h hold, park on exhaustion with an early decision kept; operator note threaded into select, write, coherence).
- **§4** → task 11 (the primitive), task 12b (promptfoo as runner). Reproducing each Python harness is plan A2/C.
- **§7** → task 2 (curation band; the whole-run comparison adds the journal's non-model constants), task 12a/12b (plants, two families, bands, disagreements). The seven-day fail-closed rule and "three passed days" are plan A2's gate runs.
- **§8.2** → task 1 including its step 7 (the recheck band). **§8.3** → task 2. **§8.4 spine** → tasks 4 to 12b; "Temporal in terraform" is deliberately plan C (spec §7.2 lists it under C), and this plan's task 10 is local compose only.
- Not in this plan by design: every real activity (plan A2), terraform (plan C), the web tier (plan B), the history rewrite (plan D).

Placeholder scan: no TBD; every "read X" step names the file and the test that pins the outcome. Type consistency: `Pointer` (task 7) is imported by 9a, 9b, 12a; `StageSpec`/`StageTool` live in `runner/prompt.ts` (task 8) and `StageResult`/`SdkQuery` in `runner/run-stage.ts`; `DigestInput`/`DigestOutput`/`Activities` live in `activities/index.ts` (task 9a) and are imported by 9b, 9c; `JudgeVerdict`/`Criterion` live in `gate/verdict.ts` (task 12a) and are imported by 12b.

## Dispositions of the revision-1 review

1, 2 (task 2 called a non-existent signature and read a file orchestrate never writes): rewritten against the real keyword-only signature with `on_usage`, followed by `merge.assemble_selections`; a test drives the sequence. 3 (test commands ran the whole CI): every Python test command now overrides the entrypoint; verified. 4 (retry test could not reach the failing path; an early signal was dropped): stub takes an explicit `failStage`; the workflow queues decisions; a test sends the decision before the failure. 5 (contracts contradicted schema.py): sources are `{article_id}`, briefs tolerate `why_it_matters`; the spec row is fixed in the same task. 6 (import cycle): a leaf `prompts.py` with an agreement test against `orchestrate.parse_agent_spec`; prompts read at call time. 7 (production-behaviour constraint false): rewritten to state exactly what changes. 8 (task 1 test tested a tuple): it now calls `archive_run_artifacts` on a real DB. 9 (`put` silently returned another content's pointer): `ConflictError`; tests apply the repo's migration files. 10 (library names inferred): task 5 verifies every name against the installed `.d.ts` before use; `tools` is no longer passed, `disallowedTools` is. 11 (fakes tested the fake): the adapter compiles against the SDK's `Options` and `SDKMessage`; casts are confined to the test's fakes. 12 (interface drift): blocks corrected; `parseAgentSpec` throws on a removed tool. 13 (coverage claimed, not delivered): the recheck band is task 1 step 7; task 2 states the span it measures. 14 (right-sizing): task 8 → 9a/9b/9c, task 11 → 12a/12b, the lockfile gate → task 5. 15 (lint and a vacuous test): `catch {}`, no unused params, the plant test's draft carries a number in every field.
