# Rewrite plan A1: prerequisites and the TypeScript-on-Temporal spine

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. One task per commit; the repo's review gate requires a reviewer run after every commit (`feature-dev:code-reviewer` for mechanical tasks, `adversarial-reviewer` for tasks 1, 2, 7, 8, 9, 11).

**Goal:** Land everything plan A's fan-out depends on: the two Python-side prerequisites the spec orders first, then a TypeScript package that runs a `DigestWorkflow` end to end on a local Temporal with stub activities, a real artifact store, a real activity runner, and the gate harness.

**Architecture:** The pipeline becomes one Temporal workflow whose activities persist blobs to SQLite and return pointers; the model-calling activity wraps the TypeScript Agent SDK with tools scoped per stage and the result returned as the final message. This plan builds the spine (contracts, store, runner, workflow skeleton, local Temporal, gate harness); plan A2 ports the stages onto it one activity at a time.

**Tech Stack:** Node 22, TypeScript 5 strict, `@temporalio/{workflow,worker,client,testing}` 1.24, `@anthropic-ai/claude-agent-sdk` 0.3.278 (bundles the CLI; subscription login inherited), `better-sqlite3` 13, `zod` 4, `vitest` 5, `promptfoo` 0.123, Docker Compose for Temporal 1.32.0 + Postgres 16. Python side: the existing `newsroom/` package, pytest in Docker (`make test`).

**Spec:** `docs/superpowers/specs/2026-09-21-four-systems-rewrite-design.md` (read §1, §2.1, §2.2, §7, §8 before starting).

## Global Constraints

- Python 3.14, tests run in Docker: `docker compose run --rm ci pytest -q <path>`; the pre-commit hook runs `bin/ci` (~90 s) and formats with ruff.
- Frozen contracts (spec §1): article ids `A{n}`; no URL reaches a model stage; `schema.SELECTIONS_SCHEMA` and `schema.COHERENCE_REPORT_SCHEMA` shapes; fail-closed; parsing boundary (four reasons); one UTC run date read once.
- New TypeScript code lives under `digest/` at the repo root: `digest/src/{contracts,store,runner,workflow,activities,gate,cli}`, tests beside sources as `*.test.ts`. No `digest/` file imports from `newsroom/`.
- Library gate: `still_active --sbom=<cyclonedx of digest/package-lock.json> --fail-if-critical` must pass before the scaffold commit (spec §8.4); the candidate pass is in `docs/proposed/2026-09-21-ts-candidates/`.
- Every agent definition pins a model. Sonnet for mechanical tasks, Opus for reviews.
- No deletion of Python in this plan. Nothing in this plan changes what production runs.
- Model calls only in tasks 2 and 11, opt-in, never in CI.
- Commit messages: rationale (why), not narrative (what); attribution lines as the session requires.

---

### Task 1: Archive the repair path's outputs (spec §8.2, known gap A2)

**Files:**
- Modify: `newsroom/src/db.py:768-800` (`_TRACE_ARTIFACTS`)
- Modify: `newsroom/tests/test_archive_closure.py:36-45` (`UNARCHIVED_REPAIR_OUTPUTS`)
- Test: `newsroom/tests/test_archive_closure.py`, `newsroom/tests/test_db.py`

**Interfaces:**
- Consumes: `db.archive_run_artifacts(run_id, claude_input_dir)` (existing; archives every name in `_TRACE_ARTIFACTS` plus `articles_*.csv`).
- Produces: four more rows per run in `run_artifacts`: `repaired_fields.json`, `recheck_draft.json`, `recheck_report.json`, `repair_resolution.json` (only when the repair path ran).

- [ ] **Step 1: Write the failing test**

Append to `newsroom/tests/test_db.py`:

```python
def test_repair_outputs_are_archived_when_present(tmp_path):
    """Probe 0 gap A2: the recheck verdict was the one output nobody could read back (run 303)."""
    for name in ("repaired_fields.json", "recheck_draft.json", "recheck_report.json", "repair_resolution.json"):
        (tmp_path / name).write_text('{"x": 1}', encoding="utf-8")
    assert {"repaired_fields.json", "recheck_draft.json", "recheck_report.json", "repair_resolution.json"} <= set(
        db._TRACE_ARTIFACTS
    )
```

- [ ] **Step 2: Run it to verify it fails**

Run: `docker compose run --rm ci pytest -q newsroom/tests/test_db.py -k repair_outputs_are_archived`
Expected: FAIL, the four names are not in `_TRACE_ARTIFACTS`.

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
# Closed 2026-09-2x: the repair path's four model outputs are archived (db._TRACE_ARTIFACTS).
UNARCHIVED_REPAIR_OUTPUTS: dict[str, str] = {}
```

and keep `DERIVED_IN_PROCESS` as it is (the union still works with an empty dict).

- [ ] **Step 5: Run the two test files**

Run: `docker compose run --rm ci pytest -q newsroom/tests/test_db.py newsroom/tests/test_archive_closure.py`
Expected: PASS. If `test_archive_closure` reports a prompt-named file that is neither archived nor derived, the list is wrong, not the test.

- [ ] **Step 6: Commit**

```bash
git add newsroom/src/db.py newsroom/tests/test_db.py newsroom/tests/test_archive_closure.py
git commit -m "feat(archive): the repair path's four outputs are archived; run 303's drops become readable"
```

---

### Task 2: A whole-run re-run on archived inputs, for the same-day band (spec §7.4, §8.3)

**Files:**
- Create: `newsroom/src/rerun_run.py`
- Create: `bin/rerun-run`
- Test: `newsroom/tests/test_rerun_run.py`

**Interfaces:**
- Consumes: `db.get_run_artifacts(run) -> dict[str, str]`; `orchestrate.orchestrate_selections(claude_input_dir, *, today, ...)` (read its signature at `newsroom/src/orchestrate.py`, the function that runs all stages over a directory); `rerun_stage._run_date(run)`.
- Produces: `rerun_run.rerun(run: int, *, reps: int, work: Path) -> dict` writing `work/rep<i>/` (the full `claude_input` tree after each rep) and `work/summary.json` with per-rep `cost_usd`, `wall_s`, `stories`, `false_drops_vs_archived`. The band is the spread of `cost_usd` and `wall_s` over reps.

- [ ] **Step 1: Write the failing test**

```python
# newsroom/tests/test_rerun_run.py
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
import rerun_run


def test_restore_writes_every_archived_input_and_never_the_outputs(tmp_path):
    archive = {
        "articles_1.csv": "article_id,title\nA1,x\n",
        "sources.csv": "id,name\nreuters,Reuters\n",
        "weekly_recap.txt": "recap",
        "recent_rss_titles.csv": "t\n",
        "yesterday_headlines.txt": "",
        "recent_digest_headlines.txt": "",
        "clusters.json": '{"stale": true}',
        "selections.json": '{"stale": true}',
    }
    written = rerun_run.restore_inputs(archive, tmp_path)
    assert (tmp_path / "articles_1.csv").exists() and (tmp_path / "sources.csv").exists()
    assert not (tmp_path / "clusters.json").exists() and not (tmp_path / "selections.json").exists()
    assert sorted(written) == sorted(n for n in archive if n not in rerun_run.STAGE_OUTPUTS)


def test_summary_carries_the_band_fields(tmp_path):
    reps = [{"cost_usd": 5.0, "wall_s": 1200.0, "stories": 16}, {"cost_usd": 5.5, "wall_s": 1300.0, "stories": 15}]
    s = rerun_run.summarise(300, reps)
    assert s["run"] == 300 and s["reps"] == 2
    assert s["cost_usd"] == [5.0, 5.5] and s["wall_s"] == [1200.0, 1300.0]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `docker compose run --rm ci pytest -q newsroom/tests/test_rerun_run.py`
Expected: FAIL, `ModuleNotFoundError: rerun_run`.

- [ ] **Step 3: Write `newsroom/src/rerun_run.py`**

```python
"""Re-run a whole curation run from a run's archived inputs, with model calls, N times.

The spec's speed-and-cost gate (§7.4) needs the OLD system's same-day band, and replay.py makes
no model calls. This restores every archived input of run N into a scratch input dir and drives
orchestrate.orchestrate_selections over it with the run's date pinned. Usage (in the container,
see bin/rerun-run): rerun_run.py --run 300 --reps 3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import time
from pathlib import Path

import config
import db
import orchestrate
import rerun_stage
import usage

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


async def _one_rep(archive: dict[str, str], work: Path, today) -> dict:
    restore_inputs(archive, work)
    t0 = time.monotonic()
    usage_rows: list[dict] = []
    # orchestrate_selections records usage through a callback in production; here it is captured.
    await orchestrate.orchestrate_selections(work, today=today, record_usage=usage_rows.append)
    wall = time.monotonic() - t0
    selections = json.loads((work / "selections.json").read_text(encoding="utf-8"))
    stories = len(selections.get("must_know", [])) + len(selections.get("should_know", []))
    return {"cost_usd": round(sum(r.get("api_cost_usd", 0.0) for r in usage_rows), 4), "wall_s": round(wall, 1), "stories": stories}


async def rerun(run: int, *, reps: int, work: Path) -> dict:
    archive = db.get_run_artifacts(run)
    if "sources.csv" not in archive or "weekly_recap.txt" not in archive:
        raise SystemExit(f"run {run} predates the closed archive (2026-09-18); pick run >= 300")
    today = rerun_stage._run_date(run)
    results = []
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

`orchestrate.orchestrate_selections` today does not take `record_usage`; read its signature and how it records `run_usage` rows (search `usage_row_from_sdk` in `orchestrate.py`). Add an optional keyword `record_usage: Callable[[dict], None] | None = None` that, when given, receives each usage row instead of writing it to the DB. That change is part of this task: write it, and add one test in `newsroom/tests/test_orchestrate.py` that a fake stage's usage row reaches the callback and no DB write happens (mirror the existing usage tests in that file).

- [ ] **Step 4: Write `bin/rerun-run`**

```bash
#!/bin/bash
# Re-run a whole curation run from a run's archived inputs, with model calls, N times: the old
# system's same-day cost/time band (spec §7.4). Opt-in, never in CI. ~$5 and ~20 min per rep.
# Usage: bin/rerun-run --run 300 [--reps 3]
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

Run: `docker compose run --rm ci pytest -q newsroom/tests/test_rerun_run.py newsroom/tests/test_orchestrate.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add newsroom/src/rerun_run.py newsroom/src/orchestrate.py newsroom/tests/test_rerun_run.py newsroom/tests/test_orchestrate.py bin/rerun-run
git commit -m "feat(eval): re-run a whole run on archived inputs with model calls; the same-day band the gate needs"
```

- [ ] **Step 7: Measure the band (model calls, ~$15, ~1 h; opt-in)**

Run: `bin/rerun-run --run 301 --reps 3`
Copy `data/rerun-run/run301/summary.json` to `docs/proposed/2026-09-2x-same-day-band/summary.json` with a one-paragraph README stating the three costs, the three wall clocks, and the run's original `run_usage` total for comparison. Commit as `docs(eval): the old system's same-day band on run 301`.

---

### Task 3: Extract the three Python-embedded prompts into files (spec §1, prompts row)

**Files:**
- Create: `.claude/agents/thread-synthesis.md`, `.claude/agents/thread-audit.md`, `.claude/agents/cohesion.md`, `.claude/agents/cluster-extract.md`
- Modify: `newsroom/src/thread_synthesis.py` (the `AUDIT_SYSTEM` constant at line 80 and the synthesis system prompt built near line 295), `newsroom/src/cohesion.py:43` (`JUDGE_SYSTEM`), `newsroom/src/cluster_extractjoin.py` (`EXTRACT_SYSTEM`)
- Test: `newsroom/tests/test_prompt_files.py`

**Interfaces:**
- Produces: `orchestrate.load_prompt_text(name: str) -> str` returning the body of `.claude/agents/<name>.md` (frontmatter stripped), used by the three modules instead of their string constants.

- [ ] **Step 1: Write the failing test**

```python
# newsroom/tests/test_prompt_files.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
import cluster_extractjoin, cohesion, orchestrate, thread_synthesis

AGENTS = Path(__file__).resolve().parents[2] / ".claude" / "agents"


def test_every_model_stage_prompt_is_a_file():
    for name in ("thread-synthesis", "thread-audit", "cohesion", "cluster-extract"):
        assert (AGENTS / f"{name}.md").exists(), name


def test_modules_read_their_prompt_from_the_file():
    assert cohesion.JUDGE_SYSTEM == orchestrate.load_prompt_text("cohesion")
    assert thread_synthesis.AUDIT_SYSTEM == orchestrate.load_prompt_text("thread-audit")
    assert cluster_extractjoin.EXTRACT_SYSTEM == orchestrate.load_prompt_text("cluster-extract")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `docker compose run --rm ci pytest -q newsroom/tests/test_prompt_files.py`
Expected: FAIL, the files do not exist and `load_prompt_text` is undefined.

- [ ] **Step 3: Create the four files**

For each constant, create the `.md` with frontmatter and the constant's text verbatim as the body:

```markdown
---
name: cohesion
description: Judges whether a cluster is one event or several (the cohesion gate). Runs inside the WRITE phase.
model: claude-sonnet-4-6
---

<the exact text of cohesion.JUDGE_SYSTEM>
```

Same shape for `thread-audit` (text of `thread_synthesis.AUDIT_SYSTEM`), `thread-synthesis` (the system prompt the synthesis call passes at `thread_synthesis.py:295`; read the variable it passes and lift its text), `cluster-extract` (`cluster_extractjoin.EXTRACT_SYSTEM`). The `model:` line is the model each module passes today (read the call sites).

- [ ] **Step 4: Add `load_prompt_text` to `orchestrate.py` and point the constants at it**

In `orchestrate.py`, next to `parse_agent_spec`:

```python
def load_prompt_text(name: str) -> str:
    """The body of .claude/agents/<name>.md, frontmatter stripped: the one place a stage's
    prompt lives, so a prompt embedded in Python cannot drift from the file the rewrite carries."""
    return parse_agent_spec(_AGENTS_DIR / f"{name}.md").body
```

(`_AGENTS_DIR` is whatever name `orchestrate.py` already uses for the agents directory; read the top of the file.) Then in each module replace the string constant with `JUDGE_SYSTEM = orchestrate.load_prompt_text("cohesion")` and so on. Check the module layering in `CLAUDE.md`: `cohesion`, `thread_synthesis` and `cluster_extractjoin` may not import `orchestrate` if `orchestrate` imports them. If they do, put `load_prompt_text` in `config.py` (a leaf module) instead and import it from there; the test then reads `config.load_prompt_text`.

- [ ] **Step 5: Run the suite**

Run: `docker compose run --rm ci pytest -q`
Expected: PASS. `test_archive_closure` scans `.claude/agents/*.md` for filenames; the new files name no input files, so nothing changes there.

- [ ] **Step 6: Commit**

```bash
git add .claude/agents/thread-synthesis.md .claude/agents/thread-audit.md .claude/agents/cohesion.md .claude/agents/cluster-extract.md newsroom/src newsroom/tests/test_prompt_files.py
git commit -m "refactor(prompts): the three prompts embedded in Python are files; every model stage's prompt is now text the rewrite carries"
```

---

### Task 4: TypeScript scaffold, CI job, and the library gate

**Files:**
- Create: `digest/package.json`, `digest/tsconfig.json`, `digest/vitest.config.ts`, `digest/eslint.config.js`, `digest/src/index.ts`, `digest/src/smoke.test.ts`, `digest/Dockerfile.ci`
- Modify: `bin/ci` (add a TypeScript step), `docker-compose.yml` (add `ci-ts` service), `.gitignore` (`digest/node_modules`, `digest/dist`)

**Interfaces:**
- Produces: `npm test`, `npm run lint`, `npm run typecheck` under `digest/`; `bin/ci` runs them in the `ci-ts` container.

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

`digest/package.json`:

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

Pin every version exactly (no `^`); `npm install` writes the lockfile. If a pinned devDependency version does not exist, use `npm view <pkg> version` and pin what it prints.

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

- [ ] **Step 3: Run the test locally to verify it passes**

Run: `cd digest && npm install && npm test && npm run typecheck && npm run lint`
Expected: 1 test passes; typecheck and lint clean.

- [ ] **Step 4: Run the library gate on the real lockfile**

Run: `cd digest && npm run sbom && still_active --sbom=sbom.cdx.json --fail-if-critical --markdown > ../docs/proposed/2026-09-21-ts-candidates/lockfile-gate.md; echo exit=$?`
Expected: exit 0. If a transitive dependency is flagged critical, replace the package that pulls it in before continuing; record the substitution in the README beside the table.

- [ ] **Step 5: Add the CI container and step**

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
    volumes:
      - ./digest/src:/app/digest/src
```

In `bin/ci`, inside `run_checks`, add a step to the `steps` list following the pattern the Rust steps use (read `bin/ci:124-198`):

```python
        ("Tests: TypeScript", ["docker", "compose", "run", "--rm", "--build", "ci-ts", "npm", "test"], REPO),
        ("Types: TypeScript", ["docker", "compose", "run", "--rm", "ci-ts", "npm", "run", "typecheck"], REPO),
        ("Style: TypeScript", ["docker", "compose", "run", "--rm", "ci-ts", "npm", "run", "lint"], REPO),
```

`bin/ci` runs *inside* the `ci` container for the Python steps (`run_in_docker`); read how it decides that and add the three TypeScript steps on the host side of that split, next to the Rust ones, which also shell out to `docker compose`.

- [ ] **Step 6: Run `bin/ci` end to end**

Run: `bin/ci`
Expected: every step passes including the three new ones.

- [ ] **Step 7: Commit**

```bash
git add digest/package.json digest/package-lock.json digest/tsconfig.json digest/vitest.config.ts digest/eslint.config.js digest/src/index.ts digest/src/smoke.test.ts digest/Dockerfile.ci bin/ci docker-compose.yml .gitignore docs/proposed/2026-09-21-ts-candidates/lockfile-gate.md
git commit -m "build(digest): TypeScript package, CI steps, and the library gate on the real lockfile"
```

---

### Task 5: Frozen contracts as zod schemas and tests (spec §1)

**Files:**
- Create: `digest/src/contracts/ids.ts`, `digest/src/contracts/selections.ts`, `digest/src/contracts/coherence.ts`, `digest/src/contracts/index.ts`
- Test: `digest/src/contracts/ids.test.ts`, `digest/src/contracts/selections.test.ts`, `digest/src/contracts/coherence.test.ts`

**Interfaces:**
- Produces:
  - `ArticleId` (branded string matching `/^A\d+$/`), `parseArticleId(s: string): ArticleId`, `assertNoUrls(text: string): void` (throws on `https?://`).
  - `SelectionsSchema` (zod) mirroring `newsroom/src/schema.py` `SELECTIONS_SCHEMA` minus `cluster_index`; `PREHEADER_MAX_CHARS = 157`; `NOT_COVERED_BLURB_MAX_LEN = 500`.
  - `CoherenceReportSchema` (zod) mirroring `COHERENCE_REPORT_SCHEMA` (shape only); `COHERENCE_FIELDS`, `FAILURE_KINDS` as `as const` tuples.
  - `coherenceReportJsonSchema(): object` producing the JSON Schema the runner passes as `outputFormat` (via `zod`'s `toJSONSchema`).

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

const story = {
  headline: "H",
  summary: "S",
  why_it_matters: "W",
  sources: [{ article_id: "A1", angle: "a", bias: "centre" }],
};

describe("selections contract", () => {
  it("accepts the shipped shape", () => {
    const ok = SelectionsSchema.safeParse({ must_know: [story], should_know: [], preheader: "p" });
    expect(ok.success).toBe(true);
  });
  it("rejects a preheader over the cap", () => {
    const bad = SelectionsSchema.safeParse({ must_know: [], should_know: [], preheader: "x".repeat(PREHEADER_MAX_CHARS + 1) });
    expect(bad.success).toBe(false);
  });
  it("rejects cluster_index: it left the contract", () => {
    const bad = SelectionsSchema.safeParse({ must_know: [{ ...story, cluster_index: 3 }], should_know: [], preheader: "p" });
    expect(bad.success).toBe(false);
  });
  it("a brief carries no why_it_matters", () => {
    const { why_it_matters: _w, ...brief } = story;
    expect(SelectionsSchema.safeParse({ must_know: [], should_know: [brief], preheader: "p" }).success).toBe(true);
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
import { z } from "zod";
export const PREHEADER_MAX_CHARS = 157;
export const NOT_COVERED_BLURB_MAX_LEN = 500;
const Source = z.object({ article_id: z.string().regex(/^A\d+$/), angle: z.string(), bias: z.string() }).strict();
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
const Brief = Story.omit({ why_it_matters: true }).strict();
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

The Python `SELECTIONS_SCHEMA` has fields this excerpt may not list (read `newsroom/src/schema.py:30-92` and mirror every property and every `required` list exactly; `.strict()` is what rejects `cluster_index`). Sources `min(1)` mirrors `minItems: 1` in the Python; that is a contract on the *selections* output, not on a model's structured output, so it is allowed here.

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

- [ ] **Step 4: Run the tests**

Run: `cd digest && npm test && npm run typecheck && npm run lint`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add digest/src/contracts
git commit -m "feat(digest): the frozen contracts as zod schemas; cluster_index leaves the contract"
```

---

### Task 6: Artifact store over SQLite with pointers, integrity and quarantine (spec §2.1)

**Files:**
- Create: `digest/src/store/artifacts.ts`, `digest/src/store/db.ts`
- Test: `digest/src/store/artifacts.test.ts`

**Interfaces:**
- Produces:
  ```ts
  export interface Pointer { runId: number; name: string; sha256: string }
  export class ArtifactStore {
    constructor(dbPath: string);
    put(runId: number, name: string, content: string): Pointer;          // INSERT OR IGNORE; returns the existing pointer if a row exists
    get(p: Pointer): string;                                              // throws IntegrityError if the hash does not match
    find(runId: number, name: string): Pointer | undefined;
    quarantine(runId: number, name: string): string;                      // renames to `${name}.corrupt.${n}`, returns the new name
    replace(runId: number, name: string, content: string): Pointer;       // explicit force path only
    close(): void;
  }
  export class IntegrityError extends Error {}
  ```
- Consumes: the existing `run_artifacts` table (`run_id`, `artifact_name`, `content`, `created_at`, `UNIQUE(run_id, artifact_name)`); no migration.

- [ ] **Step 1: Write the failing test**

```ts
// digest/src/store/artifacts.test.ts
import Database from "better-sqlite3";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { ArtifactStore, IntegrityError } from "./artifacts.js";

function freshDb(): string {
  const path = join(mkdtempSync(join(tmpdir(), "digest-")), "digest.db");
  const db = new Database(path);
  db.exec(`CREATE TABLE run_artifacts (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER, artifact_name TEXT NOT NULL,
           content TEXT NOT NULL, created_at DATETIME DEFAULT (datetime('now','utc')), UNIQUE(run_id, artifact_name));`);
  db.close();
  return path;
}

describe("ArtifactStore", () => {
  it("put returns a pointer whose hash is the content's sha256, and get round-trips", () => {
    const s = new ArtifactStore(freshDb());
    const p = s.put(300, "recap.txt", "hello");
    expect(p.sha256).toBe("2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824");
    expect(s.get(p)).toBe("hello");
  });
  it("put is idempotent: a second put returns the first row, never replaces it", () => {
    const s = new ArtifactStore(freshDb());
    const first = s.put(300, "recap.txt", "one");
    const again = s.put(300, "recap.txt", "two");
    expect(again.sha256).toBe(first.sha256);
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

const sha = (s: string) => createHash("sha256").update(s, "utf8").digest("hex");

export class ArtifactStore {
  private readonly db: Database.Database;
  constructor(dbPath: string) {
    this.db = openDb(dbPath);
  }
  find(runId: number, name: string): Pointer | undefined {
    const row = this.db.prepare("SELECT content FROM run_artifacts WHERE run_id=? AND artifact_name=?").get(runId, name) as { content: string } | undefined;
    return row ? { runId, name, sha256: sha(row.content) } : undefined;
  }
  put(runId: number, name: string, content: string): Pointer {
    this.db.prepare("INSERT OR IGNORE INTO run_artifacts (run_id, artifact_name, content) VALUES (?, ?, ?)").run(runId, name, content);
    const p = this.find(runId, name);
    if (!p) throw new Error(`put failed for ${name}`);
    return p;
  }
  get(p: Pointer): string {
    const row = this.db.prepare("SELECT content FROM run_artifacts WHERE run_id=? AND artifact_name=?").get(p.runId, p.name) as { content: string } | undefined;
    if (!row) throw new IntegrityError(`no artifact ${p.name} for run ${p.runId}`);
    if (sha(row.content) !== p.sha256) throw new IntegrityError(`artifact ${p.name} for run ${p.runId} does not match its pointer`);
    return row.content;
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

- [ ] **Step 4: Run the tests**

Run: `cd digest && npm test && npm run typecheck && npm run lint`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add digest/src/store
git commit -m "feat(digest): artifact store over run_artifacts; a pointer is (run, name, sha256), put never replaces, replace is the force path"
```

---

### Task 7: The stage runner over the TypeScript Agent SDK (spec §2.2)

**Files:**
- Create: `digest/src/runner/prompt.ts`, `digest/src/runner/run-stage.ts`
- Test: `digest/src/runner/prompt.test.ts`, `digest/src/runner/run-stage.test.ts`

**Interfaces:**
- Produces:
  ```ts
  export interface StageSpec { name: string; model: string; thinking: "adaptive" | "disabled"; tools: ("Read" | "Grep")[]; body: string }
  export function parseAgentSpec(markdown: string): StageSpec;                       // frontmatter + body, like orchestrate.parse_agent_spec
  export function renderBody(body: string, today: string): string;                   // {{CURRENT_DATE}} → "Monday, September 21, 2026"; throws on any other {{TOKEN}}
  export interface StageInput { userMessage: string; inputDir: string }             // corpus inline (§2.2) and/or files on disk
  export interface StageResult { text: string; structured?: unknown; toolCalls: { name: string; target: string }[]; costUsd: number; usage: Record<string, number>; durationMs: number }
  export interface SdkQuery { (args: { prompt: string; options: Record<string, unknown> }): AsyncIterable<unknown> }  // the SDK's query, injectable for tests
  export function runStage(spec: StageSpec, input: StageInput, opts: { outputSchema?: object; today: string; query?: SdkQuery }): Promise<StageResult>;
  ```
- Consumes: `@anthropic-ai/claude-agent-sdk` `query`. **Before writing `run-stage.ts`, read the installed type definitions for the exact option and message field names**: `rg -n "systemPrompt|allowedTools|tools\?|permissionMode|outputFormat|structured_output|total_cost_usd|subtype|tool_use" digest/node_modules/@anthropic-ai/claude-agent-sdk/sdk.d.ts`. The Python SDK's names are `system_prompt`, `allowed_tools`, `tools`, `permission_mode`, `output_format`, and the result message carries `structured_output`, `total_cost_usd`, `usage`, `subtype`; the TypeScript names are the camelCase options and the same snake_case message fields. Use what `sdk.d.ts` says, not this paragraph.

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
    expect(s.name).toBe("coherence");
    expect(s.model).toBe("claude-sonnet-5");
    expect(s.tools).toEqual(["Read", "Grep"]);
    expect(s.thinking).toBe("adaptive");
    expect(s.body.startsWith("Today is")).toBe(true);
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
import { describe, expect, it } from "vitest";
import { runStage, type SdkQuery, type StageSpec } from "./run-stage.js";

const spec: StageSpec = { name: "coherence", model: "claude-sonnet-5", thinking: "adaptive", tools: ["Read", "Grep"], body: "Reply with JSON." };

function fakeQuery(messages: unknown[]): SdkQuery {
  return async function* () {
    for (const m of messages) yield m;
  } as unknown as SdkQuery;
}

describe("runStage", () => {
  it("collects tool calls in order, the final text, and the structured output", async () => {
    const q = fakeQuery([
      { type: "assistant", message: { content: [{ type: "tool_use", id: "1", name: "Read", input: { file_path: "/in/a.csv" } }, { type: "tool_use", id: "2", name: "Grep", input: { pattern: "58%", path: "/in" } }] } },
      { type: "result", subtype: "success", result: '{"results":[]}', structured_output: { results: [] }, total_cost_usd: 0.5, usage: { output_tokens: 10 }, duration_ms: 1200, is_error: false },
    ]);
    const r = await runStage(spec, { userMessage: "Begin.", inputDir: "/in" }, { today: "2026-09-21", query: q });
    expect(r.toolCalls).toEqual([{ name: "Read", target: "/in/a.csv" }, { name: "Grep", target: "58%" }]);
    expect(r.text).toBe('{"results":[]}');
    expect(r.structured).toEqual({ results: [] });
    expect(r.costUsd).toBe(0.5);
  });
  it("passes tools, model, cwd and the schema through to the SDK options", async () => {
    let seen: Record<string, unknown> = {};
    const q: SdkQuery = async function* ({ options }) {
      seen = options;
      yield { type: "result", subtype: "success", result: "{}", structured_output: {}, total_cost_usd: 0, usage: {}, duration_ms: 1, is_error: false };
    };
    await runStage(spec, { userMessage: "Begin.", inputDir: "/in" }, { today: "2026-09-21", query: q, outputSchema: { type: "object" } });
    expect(seen["model"]).toBe("claude-sonnet-5");
    expect(seen["cwd"]).toBe("/in");
    expect(seen["allowedTools"]).toEqual(["Read", "Grep"]);
    expect(seen["outputFormat"]).toEqual({ type: "json_schema", schema: { type: "object" } });
  });
  it("throws when a schema was requested and the success carries no structured output", async () => {
    const q = fakeQuery([{ type: "result", subtype: "success", result: "{}", total_cost_usd: 0, usage: {}, duration_ms: 1, is_error: false }]);
    await expect(runStage(spec, { userMessage: "Begin.", inputDir: "/in" }, { today: "2026-09-21", query: q, outputSchema: { type: "object" } })).rejects.toThrow(/structured/);
  });
  it("throws on a non-success result and never returns partial output", async () => {
    const q = fakeQuery([{ type: "result", subtype: "error_max_turns", result: "", total_cost_usd: 0, usage: {}, duration_ms: 1, is_error: true }]);
    await expect(runStage(spec, { userMessage: "Begin.", inputDir: "/in" }, { today: "2026-09-21", query: q })).rejects.toThrow(/error_max_turns/);
  });
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd digest && npm test -- runner`
Expected: FAIL, modules not found.

- [ ] **Step 3: Write `prompt.ts`**

```ts
// digest/src/runner/prompt.ts
export interface StageSpec { name: string; model: string; thinking: "adaptive" | "disabled"; tools: ("Read" | "Grep")[]; body: string }

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
  const tools = (fields["tools"] ?? "").split(/[,\s]+/).filter((t): t is "Read" | "Grep" => t === "Read" || t === "Grep");
  const thinking = fields["thinking"] === "adaptive" ? "adaptive" : "disabled";
  return { name: fields["name"] ?? "", model, thinking, tools, body };
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
import { query as sdkQuery } from "@anthropic-ai/claude-agent-sdk";
import { renderBody, type StageSpec } from "./prompt.js";
export type { StageSpec } from "./prompt.js";

export interface StageInput { userMessage: string; inputDir: string }
export interface StageResult { text: string; structured?: unknown; toolCalls: { name: string; target: string }[]; costUsd: number; usage: Record<string, number>; durationMs: number }
export type SdkQuery = (args: { prompt: string; options: Record<string, unknown> }) => AsyncIterable<unknown>;

interface ToolUse { type: "tool_use"; name: string; input?: Record<string, unknown> }
interface AssistantMsg { type: "assistant"; message: { content: unknown[] } }
interface ResultMsg { type: "result"; subtype: string; result?: string; structured_output?: unknown; total_cost_usd?: number; usage?: Record<string, number>; duration_ms?: number; is_error?: boolean }

const target = (t: ToolUse): string => {
  const inp = t.input ?? {};
  const v = t.name === "Grep" ? inp["pattern"] : inp["file_path"];
  return typeof v === "string" ? v : "";
};

export async function runStage(
  spec: StageSpec,
  input: StageInput,
  opts: { outputSchema?: object; today: string; query?: SdkQuery },
): Promise<StageResult> {
  const q = opts.query ?? (sdkQuery as unknown as SdkQuery);
  const options: Record<string, unknown> = {
    model: spec.model,
    systemPrompt: renderBody(spec.body, opts.today),
    cwd: input.inputDir,
    allowedTools: spec.tools,
    tools: spec.tools,
    permissionMode: "acceptEdits",
    thinking: { type: spec.thinking },
    includePartialMessages: true,
  };
  if (opts.outputSchema) options["outputFormat"] = { type: "json_schema", schema: opts.outputSchema };
  const toolCalls: { name: string; target: string }[] = [];
  const texts: string[] = [];
  let result: ResultMsg | undefined;
  for await (const raw of q({ prompt: input.userMessage, options })) {
    const m = raw as { type?: string };
    if (m.type === "assistant") {
      for (const block of (raw as AssistantMsg).message.content) {
        const b = block as { type?: string; text?: string };
        if (b.type === "tool_use") toolCalls.push({ name: (b as unknown as ToolUse).name, target: target(b as unknown as ToolUse) });
        else if (b.type === "text" && typeof b.text === "string") texts.push(b.text);
      }
    } else if (m.type === "result") {
      result = raw as ResultMsg;
    }
  }
  if (!result) throw new Error(`stage ${spec.name}: no result message`);
  if (result.subtype !== "success" || result.is_error) throw new Error(`stage ${spec.name}: ${result.subtype}`);
  // The SDK re-prompts on schema mismatch and ends with error_max_structured_output_retries; a
  // success WITHOUT structured_output when a schema was requested is also a failure (SDK docs).
  if (opts.outputSchema && result.structured_output === undefined) throw new Error(`stage ${spec.name}: success without structured output`);
  return {
    text: (result.result ?? texts.join("\n")).trim(),
    ...(result.structured_output !== undefined ? { structured: result.structured_output } : {}),
    toolCalls,
    costUsd: result.total_cost_usd ?? 0,
    usage: result.usage ?? {},
    durationMs: result.duration_ms ?? 0,
  };
}
```

Adjust the option keys (`systemPrompt`, `tools`, `thinking`, `includePartialMessages`) and the result field names to what `sdk.d.ts` declares; the tests pin the behaviour, so a renamed key fails a test rather than silently doing nothing. `permissionMode: "acceptEdits"` is what production passes today; with no Write tool available it approves nothing, and it stays because the Read tool prompts under `default`.

- [ ] **Step 5: Run the tests**

Run: `cd digest && npm test && npm run typecheck && npm run lint`
Expected: PASS.

- [ ] **Step 6: One live smoke, opt-in (~$0.01)**

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

Run inside the newsroom container so the subscription login is present: `docker compose run --rm -v "$(pwd)/digest:/app/digest" --entrypoint bash digest-newsroom -c "cd /app/digest && npx tsx src/cli/smoke-stage.ts"`. If the newsroom image has no Node, add `node:22` to the smoke via a temporary `docker run` with the `~/.claude` credentials volume the compose file mounts for the newsroom service (read `docker-compose.yml:2-30` for the volume name). Expected: `"structured":{"ok":true}` in the output. Record the exact command that worked in `digest/README.md`.

- [ ] **Step 7: Commit**

```bash
git add digest/src/runner digest/src/cli/smoke-stage.ts digest/README.md
git commit -m "feat(digest): the stage runner over the Agent SDK; tools scoped per stage, result as the final message, tool calls on record"
```

---

### Task 8: The workflow skeleton with stub activities, identity, timeouts and the three signals (spec §2.1, §2.3)

**Files:**
- Create: `digest/src/workflow/digest.workflow.ts`, `digest/src/workflow/signals.ts`, `digest/src/activities/index.ts`, `digest/src/activities/stub.ts`, `digest/src/worker.ts`, `digest/src/client.ts`
- Test: `digest/src/workflow/digest.workflow.test.ts`

**Interfaces:**
- Produces:
  ```ts
  // signals.ts
  export const approveSignal = defineSignal<[{ decision: "approve" | "reject" }]>("approve");
  export const retrySignal = defineSignal<[{ decision: "retry" | "abort" }]>("retry");
  export const operatorNoteSignal = defineSignal<[{ stage: string; note: string }]>("operatorNote");
  // digest.workflow.ts
  export interface DigestInput { runDate: string; resumeRun?: number; force?: boolean }
  export interface DigestOutput { runId: number; stories: number; broadcast: "sent" | "rejected" | "skipped" }
  export async function DigestWorkflow(input: DigestInput): Promise<DigestOutput>;
  export const WORKFLOW_RUN_TIMEOUT = "4 hours";
  export const HOLD_TIMEOUT = "2 hours";
  export function workflowIdFor(runDate: string): string;   // `digest-${runDate}`
  // activities/index.ts (the activity interface plan A2 implements)
  export interface Activities {
    startRun(input: DigestInput): Promise<{ runId: number }>;
    fetchFeed(runId: number, sourceId: string): Promise<Pointer>;
    prepare(runId: number, fetched: Pointer[]): Promise<{ articles: Pointer[]; index: Pointer }>;
    cluster(runId: number, articles: Pointer[]): Promise<Pointer>;
    recap(runId: number): Promise<Pointer>;
    select(runId: number, clusters: Pointer, recap: Pointer, note?: string): Promise<Pointer>;
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
  ```
  In this task every activity is a stub that returns a fixed pointer; plan A2 replaces them one at a time behind the same names.

- [ ] **Step 1: Write the failing test**

```ts
// digest/src/workflow/digest.workflow.test.ts
import { TestWorkflowEnvironment } from "@temporalio/testing";
import { Worker } from "@temporalio/worker";
import { WorkflowIdConflictPolicy, WorkflowIdReusePolicy } from "@temporalio/common";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { stubActivities } from "../activities/stub.js";
import { approveSignal, retrySignal } from "./signals.js";
import { DigestWorkflow, workflowIdFor } from "./digest.workflow.js";

let env: TestWorkflowEnvironment;
beforeAll(async () => { env = await TestWorkflowEnvironment.createTimeSkipping(); }, 60_000);
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
    expect(out.broadcast).toBe("sent");
    expect(out.stories).toBeGreaterThan(0);
  });
  it("proceeds after the hold times out with no signal", async () => {
    const out = await withWorker(async () => {
      const h = await env.client.workflow.start(DigestWorkflow, { taskQueue, workflowId: workflowIdFor("2026-09-22"), args: [{ runDate: "2026-09-22" }] });
      await env.sleep("3 hours"); // time-skipping: the 2 h hold elapses
      return h.result();
    });
    expect(out.broadcast).toBe("sent");
  });
  it("rejects a duplicate start for the same day while one is running", async () => {
    await withWorker(async () => {
      const opts = { taskQueue, workflowId: workflowIdFor("2026-09-23"), args: [{ runDate: "2026-09-23" }], workflowIdConflictPolicy: WorkflowIdConflictPolicy.FAIL, workflowIdReusePolicy: WorkflowIdReusePolicy.REJECT_DUPLICATE };
      const h = await env.client.workflow.start(DigestWorkflow, opts);
      await expect(env.client.workflow.start(DigestWorkflow, opts)).rejects.toThrow(/already/i);
      await h.signal(approveSignal, { decision: "approve" });
      await h.result();
    });
  });
  it("parks on the retry signal when an activity exhausts its policy, and aborts on 'abort'", async () => {
    const out = await withWorker(async () => {
      const h = await env.client.workflow.start(DigestWorkflow, { taskQueue, workflowId: workflowIdFor("2026-09-24"), args: [{ runDate: "2026-09-24", force: true, resumeRun: -1 }] });
      // stub: resumeRun -1 makes `select` throw non-retryably (see stub.ts)
      await h.signal(retrySignal, { decision: "abort" });
      return h.result();
    });
    expect(out.broadcast).toBe("skipped");
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd digest && npm test -- workflow`
Expected: FAIL, modules not found.

- [ ] **Step 3: Write the signals, stubs, and workflow**

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
export interface DigestInput { runDate: string; resumeRun?: number; force?: boolean }
export interface DigestOutput { runId: number; stories: number; broadcast: "sent" | "rejected" | "skipped" }
export interface Activities {
  startRun(input: DigestInput): Promise<{ runId: number }>;
  fetchFeed(runId: number, sourceId: string): Promise<Pointer>;
  prepare(runId: number, fetched: Pointer[]): Promise<{ articles: Pointer[]; index: Pointer }>;
  cluster(runId: number, articles: Pointer[]): Promise<Pointer>;
  recap(runId: number): Promise<Pointer>;
  select(runId: number, clusters: Pointer, recap: Pointer, note?: string): Promise<Pointer>;
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
export const SOURCE_IDS_STUB = ["reuters", "bbc_world", "al_jazeera"];
```

```ts
// digest/src/activities/stub.ts
import { ApplicationFailure } from "@temporalio/common";
import type { Pointer } from "../store/artifacts.js";
import type { Activities, DigestInput } from "./index.js";
const ptr = (runId: number, name: string): Pointer => ({ runId, name, sha256: "0".repeat(64) });
export function stubActivities(): Activities {
  return {
    startRun: async (input: DigestInput) => ({ runId: input.resumeRun && input.resumeRun > 0 ? input.resumeRun : 1 }),
    fetchFeed: async (runId, sourceId) => ptr(runId, `feed_${sourceId}.json`),
    prepare: async (runId) => ({ articles: [ptr(runId, "articles_1.csv")], index: ptr(runId, "article_index.json") }),
    cluster: async (runId) => ptr(runId, "clusters.json"),
    recap: async (runId) => ptr(runId, "recap.txt"),
    select: async (runId) => {
      if (runId === -1) throw ApplicationFailure.nonRetryable("select failed for the test", "StubFailure");
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
    finishRun: async () => {},
  };
}
```

The stub's `startRun` returns `resumeRun` when given, so the retry test passes `resumeRun: -1` to reach the failing `select`; `force: true` is what allows a start with a `resumeRun` at all (see the workflow).

```ts
// digest/src/workflow/digest.workflow.ts
import { ApplicationFailure, condition, proxyActivities, setHandler, sleep } from "@temporalio/workflow";
import type { Activities, DigestInput, DigestOutput } from "../activities/index.js";
import { SOURCE_IDS_STUB } from "../activities/index.js";
import { approveSignal, operatorNoteSignal, retrySignal } from "./signals.js";

export type { DigestInput, DigestOutput } from "../activities/index.js";
export const WORKFLOW_RUN_TIMEOUT = "4 hours";
export const HOLD_TIMEOUT = "2 hours";
export const workflowIdFor = (runDate: string): string => `digest-${runDate}`;

// Model calls: retry inside the outage-sized run budget. Verdicts and the send: one attempt.
const model = proxyActivities<Activities>({ startToCloseTimeout: "45 minutes", heartbeatTimeout: "2 minutes", retry: { maximumAttempts: 3, initialInterval: "5 minutes", backoffCoefficient: 2 } });
const network = proxyActivities<Activities>({ startToCloseTimeout: "2 minutes", retry: { maximumAttempts: 3, initialInterval: "10 seconds" } });
const once = proxyActivities<Activities>({ startToCloseTimeout: "10 minutes", retry: { maximumAttempts: 1 } });

export async function DigestWorkflow(input: DigestInput): Promise<DigestOutput> {
  if (input.resumeRun !== undefined && !input.force) throw ApplicationFailure.nonRetryable("resumeRun requires force", "BadInput");
  let approval: "approve" | "reject" | undefined;
  let retryDecision: "retry" | "abort" | undefined;
  const notes: Record<string, string> = {};
  setHandler(approveSignal, ({ decision }) => { approval = decision; });
  setHandler(retrySignal, ({ decision }) => { retryDecision = decision; });
  setHandler(operatorNoteSignal, ({ stage, note }) => { notes[stage] = note; });

  const { runId } = await once.startRun(input);

  // Retries exhausted: park on the retry signal (spec §2.3 signal 2). Returns undefined on abort.
  async function guarded<T>(stage: string, fn: () => Promise<T>): Promise<T | undefined> {
    for (;;) {
      try {
        return await fn();
      } catch (e) {
        retryDecision = undefined;
        await condition(() => retryDecision !== undefined);
        if (retryDecision === "abort") return undefined;
      }
    }
  }

  const fetched = await Promise.all(SOURCE_IDS_STUB.map((s) => network.fetchFeed(runId, s)));
  const { articles } = await once.prepare(runId, fetched);
  const [clusters, recap] = await Promise.all([model.cluster(runId, articles), model.recap(runId)]);
  const selected = await guarded("select", () => model.select(runId, clusters, recap, notes["select"]));
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

`sleep` is imported for plan A2's use of timers; remove the import if lint flags it unused. `guarded` wraps only `select` here; plan A2 wraps every model stage.

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
import { Client, Connection, ScheduleOverlapPolicy } from "@temporalio/client";
import { WorkflowIdConflictPolicy, WorkflowIdReusePolicy } from "@temporalio/common";
import { TASK_QUEUE } from "./worker.js";
import { DigestWorkflow, WORKFLOW_RUN_TIMEOUT, workflowIdFor } from "./workflow/digest.workflow.js";

export async function startDigest(runDate: string, opts: { resumeRun?: number; force?: boolean } = {}, address = process.env["TEMPORAL_ADDRESS"] ?? "localhost:7233") {
  const client = new Client({ connection: await Connection.connect({ address }) });
  return client.workflow.start(DigestWorkflow, {
    taskQueue: TASK_QUEUE,
    workflowId: workflowIdFor(runDate),
    args: [{ runDate, ...opts }],
    workflowRunTimeout: WORKFLOW_RUN_TIMEOUT,
    workflowIdConflictPolicy: WorkflowIdConflictPolicy.FAIL,
    workflowIdReusePolicy: opts.force || opts.resumeRun !== undefined ? WorkflowIdReusePolicy.ALLOW_DUPLICATE : WorkflowIdReusePolicy.REJECT_DUPLICATE,
  });
}

export async function ensureSchedule(address = process.env["TEMPORAL_ADDRESS"] ?? "localhost:7233") {
  const client = new Client({ connection: await Connection.connect({ address }) });
  await client.schedule.create({
    scheduleId: "digest-daily",
    spec: { calendars: [{ hour: 10, minute: 25 }] }, // UTC
    policies: { overlap: ScheduleOverlapPolicy.SKIP, catchupWindow: "1 day" },
    action: { type: "startWorkflow", workflowType: DigestWorkflow, taskQueue: TASK_QUEUE, workflowId: "digest-scheduled", args: [{ runDate: "" }] },
  });
}
```

The scheduled action's `workflowId` is fixed here; plan A2's `startRun` activity derives the run date from the workflow start time when `runDate` is empty, and the schedule's own overlap policy covers scheduled starts (spec §2.1). Read `@temporalio/client`'s `ScheduleOptions` types for the exact calendar-spec field names (`hour`, `minute` are documented; if the type wants `hour: [{ start: 10, end: 10 }]` use that form).

- [ ] **Step 4: Run the tests**

Run: `cd digest && npm test -- workflow && npm run typecheck && npm run lint`
Expected: PASS. The time-skipping environment downloads a test server binary on first run; allow it once and commit nothing from it.

- [ ] **Step 5: Commit**

```bash
git add digest/src/workflow digest/src/activities digest/src/worker.ts digest/src/client.ts
git commit -m "feat(digest): the DigestWorkflow skeleton: identity per day, one 4 h budget, three signals, stub activities behind the interface plan A2 fills"
```

---

### Task 9: Local Temporal by Docker Compose, pinned, with the worker as a service

**Files:**
- Create: `digest/compose.temporal.yml`, `digest/Dockerfile` (the worker image), `digest/src/cli/start.ts`
- Modify: `Makefile` (targets `temporal-up`, `temporal-down`, `digest-start`)

**Interfaces:**
- Produces: `make temporal-up` brings up `temporal` (1.32.0, Postgres backend), `postgres` (16), `temporal-ui`, and `digest-worker`; `make digest-start DATE=2026-09-21` starts a workflow by id and prints its result.

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

The `claude-sessions` volume name is whatever `docker-compose.yml` names the newsroom's credentials volume (read `docker-compose.yml:120-135`); the worker needs the same login the pipeline uses.

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
Expected: the start prints `started digest-2026-09-21`, then blocks on the hold. In another shell, approve through the UI at http://127.0.0.1:8233 (Workflows → digest-2026-09-21 → Send signal `approve` with `{"decision":"approve"}`), and the first shell prints `{"runId":1,"stories":3,"broadcast":"sent"}`. Then `make temporal-down`.

- [ ] **Step 4: Record memory for the box (spec §5's "not measured")**

Run while it is up: `docker stats --no-stream --format "{{.Name}}\t{{.MemUsage}}"` and paste the four lines into `digest/README.md` under "Footprint, local, stubs".

- [ ] **Step 5: Commit**

```bash
git add digest/compose.temporal.yml digest/Dockerfile digest/src/cli/start.ts Makefile digest/README.md
git commit -m "build(digest): local Temporal pinned at 1.32.0 with the worker as a service; one workflow runs end to end on stubs"
```

---

### Task 10: The activity-runner CLI the evals call (spec §4)

**Files:**
- Create: `digest/src/cli/run-stage.ts`
- Test: `digest/src/cli/run-stage.test.ts`

**Interfaces:**
- Produces: `node dist/cli/run-stage.js --agent <path.md> --input-dir <dir> --today YYYY-MM-DD [--schema coherence] [--inline-corpus] > result.json`, printing `StageResult` as JSON. This is the one primitive the Python evals and promptfoo call; it never touches Temporal.

- [ ] **Step 1: Write the failing test**

```ts
// digest/src/cli/run-stage.test.ts
import { describe, expect, it } from "vitest";
import { buildInvocation } from "./run-stage.js";

describe("run-stage CLI", () => {
  it("builds the invocation from flags", () => {
    const inv = buildInvocation(["--agent", "/a/coherence.md", "--input-dir", "/in", "--today", "2026-09-21", "--schema", "coherence"]);
    expect(inv.agentPath).toBe("/a/coherence.md");
    expect(inv.inputDir).toBe("/in");
    expect(inv.today).toBe("2026-09-21");
    expect(inv.schema).toBe("coherence");
    expect(inv.inlineCorpus).toBe(false);
  });
  it("refuses an unknown schema name and a missing flag", () => {
    expect(() => buildInvocation(["--agent", "/a", "--input-dir", "/in", "--today", "2026-09-21", "--schema", "nope"])).toThrow(/schema/);
    expect(() => buildInvocation(["--agent", "/a"])).toThrow(/input-dir/);
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

### Task 11: The gate harness: planted inputs, two judges, bands (spec §7)

**Files:**
- Create: `digest/src/gate/plant.ts`, `digest/src/gate/judge.ts`, `digest/src/gate/band.ts`, `digest/src/gate/score.ts`, `digest/src/cli/gate.ts`, `digest/gate/rubric.md`, `digest/gate/promptfooconfig.yaml`
- Test: `digest/src/gate/plant.test.ts`, `digest/src/gate/band.test.ts`, `digest/src/gate/score.test.ts`

**Interfaces:**
- Produces:
  ```ts
  // plant.ts: plant defects into a draft (spec §7.1: defects in the inputs, before the stage under test)
  export interface Plant { storyIndex: number; field: "headline" | "summary" | "why_it_matters"; kind: "wrong-number" | "wrong-entity" | "absent-specific"; original: string; planted: string }
  export function plantDefects(draft: Selections, seed: number, n: number): { draft: Selections; plants: Plant[] };
  // judge.ts: one judge = a CLI command that reads the digest + inputs and returns the rubric verdicts
  export interface JudgeVerdict { story: number; criterion: 1 | 2 | 3 | 4 | 5 | 6 | 7; pass: boolean; reason: string }
  export interface Judge { name: string; family: "anthropic" | "openai" | "google"; run(digestHtml: string, inputsDir: string): Promise<JudgeVerdict[]> }
  export function cliJudge(name: string, family: Judge["family"], command: string[]): Judge;   // spawns `command`, feeds a JSON {rubric, digest, inputsDir} on stdin, parses JSON verdicts from stdout
  // band.ts
  export function selfAgreement(runs: JudgeVerdict[][]): { perCriterion: Record<number, number>; overall: number };  // fraction of (story, criterion) cells with the same verdict across runs
  // score.ts
  export function recallOnPlants(plants: Plant[], report: CoherenceReport): { caught: number; total: number };
  export function disagreements(a: JudgeVerdict[], b: JudgeVerdict[]): { story: number; criterion: number }[];
  ```
- Consumes: `SelectionsSchema`, `CoherenceReportSchema` (task 5); the run-stage CLI (task 10) for producing a digest from planted inputs on the old pipeline until plan A2 lands.

- [ ] **Step 1: Write the failing tests**

```ts
// digest/src/gate/plant.test.ts
import { describe, expect, it } from "vitest";
import { plantDefects } from "./plant.js";

const draft = {
  must_know: [
    { headline: "Talks resume after 12 days", summary: "Officials said 3,000 attended.", why_it_matters: "It matters.", sources: [{ article_id: "A1", angle: "a", bias: "c" }] },
    { headline: "Vote passes", summary: "Turnout was 58%.", why_it_matters: "Why.", sources: [{ article_id: "A2", angle: "a", bias: "c" }] },
  ],
  should_know: [],
  preheader: "p",
};

describe("plantDefects", () => {
  it("is deterministic for a seed and changes exactly n fields", () => {
    const a = plantDefects(draft, 7, 2);
    const b = plantDefects(draft, 7, 2);
    expect(a.plants).toEqual(b.plants);
    expect(a.plants).toHaveLength(2);
    for (const p of a.plants) expect(p.original).not.toBe(p.planted);
  });
  it("a wrong-number plant changes a number and nothing else in the field", () => {
    const { plants } = plantDefects(draft, 1, 1);
    const p = plants[0]!;
    if (p.kind === "wrong-number") expect(p.planted.replace(/\d[\d,]*/g, "#")).toBe(p.original.replace(/\d[\d,]*/g, "#"));
  });
});
```

```ts
// digest/src/gate/band.test.ts
import { describe, expect, it } from "vitest";
import { selfAgreement } from "./band.js";
const v = (story: number, criterion: 1 | 2, pass: boolean) => ({ story, criterion, pass, reason: "" });
describe("selfAgreement", () => {
  it("is 1 when every run agrees and 0.5 when half the cells flip", () => {
    expect(selfAgreement([[v(0, 1, true), v(0, 2, true)], [v(0, 1, true), v(0, 2, true)]]).overall).toBe(1);
    expect(selfAgreement([[v(0, 1, true), v(0, 2, true)], [v(0, 1, true), v(0, 2, false)]]).overall).toBe(0.5);
  });
});
```

```ts
// digest/src/gate/score.test.ts
import { describe, expect, it } from "vitest";
import { disagreements, recallOnPlants } from "./score.js";
describe("score", () => {
  it("recall counts a plant caught when its story's field is flagged", () => {
    const plants = [{ storyIndex: 1, field: "summary" as const, kind: "wrong-number" as const, original: "58%", planted: "85%" }];
    const report = { results: [{ headline: "Talks", article_ids: ["A1"], pass: true, reason: "ok" }, { headline: "Vote", article_ids: ["A2"], pass: false, reason: "summary: x", failed_fields: ["summary" as const] }] };
    expect(recallOnPlants(plants, report)).toEqual({ caught: 1, total: 1 });
  });
  it("disagreements are the cells where two judges differ", () => {
    const a = [{ story: 0, criterion: 1 as const, pass: true, reason: "" }];
    const b = [{ story: 0, criterion: 1 as const, pass: false, reason: "" }];
    expect(disagreements(a, b)).toEqual([{ story: 0, criterion: 1 }]);
  });
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd digest && npm test -- gate`
Expected: FAIL.

- [ ] **Step 3: Write the gate modules**

```ts
// digest/src/gate/plant.ts
import type { Selections } from "../contracts/selections.js";
export interface Plant { storyIndex: number; field: "headline" | "summary" | "why_it_matters"; kind: "wrong-number" | "wrong-entity" | "absent-specific"; original: string; planted: string }

function rng(seed: number): () => number {
  let s = seed >>> 0;
  return () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296; };
}

const NUMBER = /\d[\d,]*/;
export function plantDefects(draft: Selections, seed: number, n: number): { draft: Selections; plants: Plant[] } {
  const next = rng(seed);
  const out: Selections = structuredClone(draft);
  const plants: Plant[] = [];
  const fields = ["headline", "summary", "why_it_matters"] as const;
  let guard = 0;
  while (plants.length < n && guard++ < 1000) {
    const i = Math.floor(next() * out.must_know.length);
    const story = out.must_know[i];
    if (!story) continue;
    const field = fields[Math.floor(next() * fields.length)]!;
    const original = story[field];
    if (typeof original !== "string" || plants.some((p) => p.storyIndex === i && p.field === field)) continue;
    const m = NUMBER.exec(original);
    let planted: string; let kind: Plant["kind"];
    if (m) {
      const num = Number(m[0].replaceAll(",", ""));
      planted = original.replace(NUMBER, String(num * 2 + 1)); kind = "wrong-number";
    } else {
      planted = `${original} The measure was announced in Geneva.`; kind = "absent-specific";
    }
    (story as Record<string, string>)[field] = planted;
    plants.push({ storyIndex: i, field, kind, original, planted });
  }
  return { draft: out, plants };
}
```

```ts
// digest/src/gate/judge.ts
import { spawn } from "node:child_process";
import { readFileSync } from "node:fs";
export interface JudgeVerdict { story: number; criterion: 1 | 2 | 3 | 4 | 5 | 6 | 7; pass: boolean; reason: string }
export interface Judge { name: string; family: "anthropic" | "openai" | "google"; run(digestHtml: string, inputsDir: string): Promise<JudgeVerdict[]> }
export const RUBRIC = readFileSync(new URL("../../gate/rubric.md", import.meta.url), "utf8");

export function cliJudge(name: string, family: Judge["family"], command: string[]): Judge {
  return {
    name, family,
    run: (digestHtml, inputsDir) => new Promise((resolve, reject) => {
      const [cmd, ...args] = command;
      if (!cmd) return reject(new Error("empty judge command"));
      const p = spawn(cmd, args, { stdio: ["pipe", "pipe", "inherit"] });
      let out = "";
      p.stdout.on("data", (d: Buffer) => { out += d.toString(); });
      p.on("close", (code) => {
        if (code !== 0) return reject(new Error(`${name} exited ${code}`));
        const start = out.indexOf("["); const end = out.lastIndexOf("]");
        if (start < 0 || end < start) return reject(new Error(`${name} returned no JSON array`));
        resolve(JSON.parse(out.slice(start, end + 1)) as JudgeVerdict[]);
      });
      p.stdin.end(JSON.stringify({ rubric: RUBRIC, digest: digestHtml, inputsDir }));
    }),
  };
}
```

`digest/gate/rubric.md` is spec §7.1 verbatim, with one added instruction at the top: "Return a JSON array of `{story, criterion, pass, reason}` objects, one per story per criterion, and nothing else." The judge commands are configured, not coded: an Anthropic judge is `["claude", "-p", "--model", "claude-opus-5", "--output-format", "json"]`-style, an OpenAI one `["codex", "exec", ...]`, a Google one `["gemini", "-p", ...]`; read each CLI's `--help` on the box and record the working three commands in `digest/gate/judges.json` as `{ "name", "family", "command": [] }` entries.

```ts
// digest/src/gate/band.ts
import type { JudgeVerdict } from "./judge.js";
export function selfAgreement(runs: JudgeVerdict[][]): { perCriterion: Record<number, number>; overall: number } {
  const key = (v: JudgeVerdict) => `${v.story}:${v.criterion}`;
  const cells = new Map<string, boolean[]>();
  for (const run of runs) for (const v of run) cells.set(key(v), [...(cells.get(key(v)) ?? []), v.pass]);
  const perCriterion: Record<number, number[]> = {};
  let agree = 0;
  for (const [k, votes] of cells) {
    const same = votes.every((x) => x === votes[0]) ? 1 : 0;
    agree += same;
    const c = Number(k.split(":")[1]);
    (perCriterion[c] ??= []).push(same);
  }
  const avg = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0);
  return { perCriterion: Object.fromEntries(Object.entries(perCriterion).map(([c, xs]) => [Number(c), avg(xs)])), overall: cells.size ? agree / cells.size : 0 };
}
```

```ts
// digest/src/gate/score.ts
import type { CoherenceReport } from "../contracts/coherence.js";
import type { JudgeVerdict } from "./judge.js";
import type { Plant } from "./plant.js";
export function recallOnPlants(plants: Plant[], report: CoherenceReport): { caught: number; total: number } {
  let caught = 0;
  for (const p of plants) {
    const r = report.results[p.storyIndex];
    if (r && r.pass === false && (r.failed_fields ?? []).includes(p.field)) caught++;
  }
  return { caught, total: plants.length };
}
export function disagreements(a: JudgeVerdict[], b: JudgeVerdict[]): { story: number; criterion: number }[] {
  const bs = new Map(b.map((v) => [`${v.story}:${v.criterion}`, v.pass]));
  return a.filter((v) => bs.has(`${v.story}:${v.criterion}`) && bs.get(`${v.story}:${v.criterion}`) !== v.pass).map((v) => ({ story: v.story, criterion: v.criterion }));
}
```

`recallOnPlants` scores by story *index* in draft order; the coherence report is in draft order (the Python eval maps by headline because reports were once reordered; the runner's structured output keeps order, and a test in plan A2 pins that).

- [ ] **Step 4: Write the gate CLI and the promptfoo config**

```ts
// digest/src/cli/gate.ts
import { readFileSync, writeFileSync } from "node:fs";
import { selfAgreement } from "../gate/band.js";
import { cliJudge, type Judge } from "../gate/judge.js";
import { disagreements } from "../gate/score.js";

// usage: gate --digest digest.html --inputs <dir> --judges gate/judges.json --reps 5 --out band.json
const get = (f: string) => { const i = process.argv.indexOf(f); return i >= 0 ? process.argv[i + 1] : undefined; };
const digest = readFileSync(get("--digest")!, "utf8");
const inputs = get("--inputs")!;
const reps = Number(get("--reps") ?? 5);
const judges: Judge[] = (JSON.parse(readFileSync(get("--judges")!, "utf8")) as { name: string; family: Judge["family"]; command: string[] }[]).map((j) => cliJudge(j.name, j.family, j.command));
const families = new Set(judges.map((j) => j.family));
if (families.size < 2) throw new Error("the gate needs judges from two families (spec §7.2)");
const out: Record<string, unknown> = {};
const firstRuns: Record<string, Awaited<ReturnType<Judge["run"]>>> = {};
for (const j of judges) {
  const runs = [];
  for (let i = 0; i < reps; i++) runs.push(await j.run(digest, inputs));
  firstRuns[j.name] = runs[0]!;
  out[j.name] = { band: selfAgreement(runs), runs };
}
const [a, b] = judges;
if (a && b) out["disagreements"] = disagreements(firstRuns[a.name]!, firstRuns[b.name]!);
writeFileSync(get("--out") ?? "band.json", JSON.stringify(out, null, 2));
console.log(JSON.stringify({ judges: judges.map((j) => j.name), disagreements: (out["disagreements"] as unknown[]).length }));
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

The provider line uses promptfoo's `exec:` provider; if the installed promptfoo names it differently (`npx promptfoo@0.123.1 providers --help`), use its name. The fixture directory does not exist yet: this config is committed so the first gate run in plan A2 has its harness, and the `tests` entry is the shape, not a claim that the fixture exists.

- [ ] **Step 5: Run the unit tests**

Run: `cd digest && npm test && npm run typecheck && npm run lint`
Expected: PASS (the CLI and promptfoo config are not unit-tested; the judge spawn is exercised in step 6).

- [ ] **Step 6: One live band, opt-in (~$3 to $6, model calls)**

Produce a digest from archived inputs of run 301 through today's pipeline (`bin/replay RUN=301` renders one from artifacts with no model calls; copy the rendered HTML and `data/claude_input` of that replay into `docs/proposed/gate-fixtures/day-301/`). Fill `digest/gate/judges.json` with two working judge commands. Run: `cd digest && npm run build && node dist/cli/gate.js --digest ../docs/proposed/gate-fixtures/day-301/digest.html --inputs ../docs/proposed/gate-fixtures/day-301/inputs --judges gate/judges.json --reps 5 --out ../docs/proposed/gate-fixtures/day-301/band.json`. Expected: a `band.json` with two judges' self-agreement per criterion and a disagreement list. Record the two bands in `docs/proposed/gate-fixtures/day-301/README.md`; a criterion with agreement under 0.8 is written up as "not usable as a gate criterion until the rubric is tightened", not silently kept.

- [ ] **Step 7: Commit**

```bash
git add digest/src/gate digest/src/cli/gate.ts digest/gate docs/proposed/gate-fixtures/day-301
git commit -m "feat(digest): the gate harness; planted inputs, two judge families, band before metric"
```

---

## Self-review against the spec

- **§1 contracts** → task 5 (ids, selections, coherence), task 3 (prompts as files), task 1 (archive closure). DB tables and fail-closed are consumed as-is by tasks 6 and 8; parsing boundary and run date are enforced by tasks 7 and 8's inputs.
- **§2.1** → task 6 (blobs, pointers, integrity, quarantine, replace-only-on-force), task 8 (workflow identity, run timeout, per-activity retry classes, prepare as its own activity, gnews after assemble in parallel with threads), task 9 (pinned server). Heartbeats: task 8 sets `heartbeatTimeout` on model activities; plan A2 must call `heartbeat()` inside them.
- **§2.2** → task 7 (Write gone, tools scoped, schema on the final message, tool calls on record), task 10 (the inline-corpus option). The inline-vs-Read fork is decided in plan A2 with the task 10 CLI and the existing Python band harness.
- **§2.3** → task 8 (three signals, 2 h hold, park on exhaustion; operator note threaded into select, write, coherence).
- **§4** → task 10 (the primitive), task 11 (promptfoo as runner). Reproducing each Python harness is plan A2/C.
- **§7** → task 2 (same-day band), task 11 (plants, two families, bands, disagreements). The seven-day fail-closed rule and the "three passed days" cut-over are plan A2's gate runs, not this plan's.
- **§8.2, §8.3** → tasks 1, 2. **§8.4 spine** → tasks 4 to 11.
- Not in this plan by design: every real activity (plan A2), terraform for Temporal on the box (plan C; task 9 is local compose only), the web tier (plan B), the history rewrite (plan D).

Placeholder scan: every "read X and use its names" step names the file to read and the test that pins the outcome; no TBD. Type consistency: `Pointer` is defined once (task 6) and imported by tasks 8 and 11; `StageSpec`/`StageResult` once (task 7), used by task 10; `DigestInput`/`DigestOutput`/`Activities` once (task 8's `activities/index.ts`), used by the workflow, stub, client, and test.
