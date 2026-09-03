# Stage Invocation Rewrite: Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the pipeline's shape and change how its stages are invoked and prompted: tool-free single-turn calls for the pure transforms, prompts built from the data instead of rules about the data, one reasoning step in the clusterer, and one verifier over what renders.

**Architecture:** Python already owns identity, assembly, validation and the per-branch prompt. This plan moves the remaining structural facts (tier, file layout, cluster membership, thread state) out of prose instructions and into what Python hands each call. Every phase is gated on a measurement with a stated noise floor, because this project has twice rejected a change on quality that looked obvious on cost, and once shipped one that looked tested and was not.

**Tech Stack:** Python 3.14, Claude Agent SDK via `newsroom/src/claude_cli.py` (subscription; no Messages API), pytest in Docker (`docker compose run --rm ci`), the opt-in eval harnesses under `bin/eval-*`.

**Spec:** the design discussion of 2026-09-03 (this file's "Design" section is the written form; there is no separate spec). Context: `docs/lessons/best-practices/inlining-the-corpus-costs-absence-detection.md`, `docs/lessons/test-failures/a-test-that-feeds-the-code-a-synthetic-prompt-has-not-tested-the-prompt.md`, `docs/2026-08-31-*` plumbing audit, memory `project_cluster_junk_drawers`.

## Global Constraints

- Stay on the Agent SDK. A Messages-API framework means leaving the subscription; rejected on economics 2026-08-31.
- Per-stage thinking config is a measured setting (`coherence`, `write`, `repair_recheck` adaptive; the rest disabled). A phase may not change it as a side effect.
- A single-turn prompt is **derived** from the shipped prompt by swapping only the I/O section, never hand-written, and a test asserts the rules survive byte for byte (`test_every_probe_survives_the_rewrite_byte_for_byte` is the pattern).
- Every comparison reports the within-arm spread before the between-arm difference, with n >= 5 per arm. A claimed effect smaller than either arm's spread is noise.
- No prompt rule of the form "do not X". Structural facts go into the data or the schema; behavioural guidance is positive and carries its reason.
- TDD per task; one reviewer pass per commit (the gate enforces it); the eval floor (`bin/eval-regression`) runs before any prompt lands.
- Real model calls are opt-in harness runs on the subscription, never in CI.

---

## Design

### What stays

Python assigns opaque ids and resolves them after the models are done; CLUSTER is a cheap per-article extract plus a deterministic join; SELECT judges; WRITE writes one story per call; COHERENCE is an adversarial checker separate from the writer; repair beats drop; every run archives its artifacts and the invariants read from the archive; threads are an entity; circulation is Rust. None of that changes.

### What changes, and the order

| Phase | Change | Gate that opens it | Readout |
|---|---|---|---|
| 1 | Per-story single-turn COHERENCE (cited sources only, no tools) | none: this is the first measurement | recall on the 6 hard positives, idx 4 (absence) rate, false drops, tokens per story |
| 2 | Per-story single-turn WRITE (branch files inlined, no tools) | none: second measurement, independent of 1 | COHERENCE flag rate on the outputs, L1 graders, tokens per story, with the within-arm spread first |
| A | Tool-free invocation for WRITE, REPAIR, PREHEADER, RECAP (COHERENCE and the re-check stay on the tool loop: Phase 1 failed its precision gate on 2026-09-03) | Phase 2 passes its gate | per-run tokens and cost, coherence flag rate, blank rate, unchanged or better |
| B | Prompts rendered from data: tier sections, present files, thread state; the negative rules added on 2026-09-03 come out | Phase A shipped (single-turn makes the prompt a string Python owns end to end) | eval floor unchanged; the L1 `summary_bolt_on` count |
| C | One cohesion judgement per multi-event cluster, before SELECT | independent of A and B; can start any time | strays per run against hand labels; `summary_bolt_on`; two-event headlines |
| D | One verifier over what renders: the thread delta goes through COHERENCE with the story | Phase A (per-story COHERENCE exists) | flags on delta text; the thread audit retires if redundant |

Phases A to D each get their own plan once their gate reports. This file specifies Phases 1 and 2 to the step, because they decide the shape of everything after them.

### Why Phase 1 might pass where the 2026-08-31 attempt failed

That attempt inlined the whole 82k-token corpus and lost absence detection: idx 4 ("no cited source states this tenure") fell from 4/5 to 1/8, and 2 of 8 runs made a false drop. The mechanism in the lesson is that a negative over 82k tokens invites satisficing, while the tool loop turns it into a search that terminates. A per-story call sees one story and its 3 to 36 cited articles, a few thousand to ~35k tokens. Exhausting that is a finite act again. If idx 4 holds at per-story size, the mechanism is confirmed and the conversion is safe for checkers as well as generators. If it does not, the lesson stands and Phase A is limited to generation stages.

### Why Phase C is the clusterer's job

The join fuses articles on entity and keyword overlap and labels the cluster by majority vote over per-article `primary_event` tags. Nothing reasons "one event or two". The helipad cluster fused three different `primary_event` tags on "Trump" and "White House". WRITE, handed one cluster and no others, cannot re-cluster; SELECT sees only labels. The tags that would decide it are already in `cluster_tags.json`. A judgement over the multi-tag clusters, using the rubric decided on 2026-09-01 (event identity, not genre), is the cheapest place to put reasoning that is currently nowhere.

---

### Task 1: Per-story single-turn COHERENCE arm in the coherence eval

*Landed 2026-09-03 as 499dfd8 + a777a29. Step 10 run the same day: **gate failed on precision** (recall 5.0 vs 4.8, idx 4 5/5, but false drops in 4 of 5 runs). COHERENCE stays on the tool loop; see `docs/2026-09-03-per-story-coherence-measurement.md`.*

**Files:**
- Modify: `newsroom/src/orchestrate.py` (add `build_story_corpus`, `build_per_story_body` next to `build_coherence_corpus` and `build_single_turn_body`, around lines 472-510)
- Modify: `newsroom/src/eval_coherence.py` (add `run_per_story_to_file`; add `--per-story` to `main`)
- Test: `newsroom/tests/test_orchestrate.py` (next to `test_every_probe_survives_the_rewrite_byte_for_byte`, ~line 1031)

**Interfaces:**
- Consumes: `orchestrate.build_single_turn_body(body) -> str`, `orchestrate.parse_coherence_report(text) -> dict | None`, `claude_cli.run_agent(prompt, *, model, system_prompt, allowed_tools, tools, cwd, idle_timeout, thinking, max_turns)`, `eval_coherence.score(report_path, labels)`.
- Produces: `orchestrate.build_story_corpus(claude_input_dir: Path, story: dict) -> str` (the story as a one-entry draft, the CSV header plus only the rows whose `article_id` is in the story's `sources`, and the `article_fulltext.json` entries for those ids), `orchestrate.build_per_story_body(body: str) -> str` (the single-turn body with "For each story in draft_selections.json" scoped to "the one story"), `eval_coherence.run_per_story_to_file(out_path, model, body, thinking, fixtures) -> None` (writes one merged `coherence_report.json` with one result per story, in draft order).

- [x] **Step 1: Write the failing test for the corpus builder**

```python
# newsroom/tests/test_orchestrate.py, in the class holding test_every_probe_survives_the_rewrite_byte_for_byte
    def test_story_corpus_holds_only_the_cited_rows_and_their_fulltext(self, tmp_path):
        """Per-story COHERENCE sees one story and its cited sources, nothing else: the
        absence probe has a bounded corpus to exhaust, which the 82k-token inline lost."""
        (tmp_path / "articles_1.csv").write_text(
            "article_id,title,summary\nA1,First,about one\nA2,Second,about two\n"
        )
        (tmp_path / "articles_2.csv").write_text("article_id,title,summary\nA3,Third,about three\n")
        (tmp_path / "article_fulltext.json").write_text(json.dumps({"A1": "full one", "A3": "full three"}))
        story = {"headline": "H", "summary": "S", "why_it_matters": "W",
                 "sources": [{"article_id": "A3"}, {"article_id": "A1"}]}
        corpus = orchestrate.build_story_corpus(tmp_path, story)
        assert "A1,First" in corpus and "A3,Third" in corpus
        assert "A2" not in corpus
        assert "full one" in corpus and "full three" in corpus
        assert '"headline": "H"' in corpus
        assert "article_id,title,summary" in corpus  # header survives so columns stay named

    def test_per_story_body_scopes_the_instruction_to_one_story_and_keeps_the_probes(self):
        multi = orchestrate.parse_agent_spec(COHERENCE_SPEC).body
        single = orchestrate.build_per_story_body(multi)
        start = multi.index("**For each field, run all three probes")
        end = multi.index("**Output schema")
        assert multi[start:end] in single
        assert "the one story" in single
        assert "every story" not in single.lower()
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `docker compose run --rm --entrypoint pytest ci -q newsroom/tests/test_orchestrate.py -k "story_corpus or per_story_body"`
Expected: FAIL with `AttributeError: module 'orchestrate' has no attribute 'build_story_corpus'`

- [x] **Step 3: Implement the two builders**

```python
# newsroom/src/orchestrate.py, after build_single_turn_body

def build_story_corpus(claude_input_dir: Path, story: dict) -> str:
    """One story and only its cited sources, inlined.

    The 2026-08-31 single-turn arm inlined the whole corpus and lost absence detection
    (idx 4: 4/5 -> 1/8). A checker that has to exhaust a negative needs a corpus it can
    exhaust; a story's own cited articles are that corpus. Header kept so columns stay named.
    """
    cited = {s.get("article_id") for s in story.get("sources", []) if isinstance(s, dict)}
    header: list[str] | None = None
    rows: list[list[str]] = []
    for csv_path in sorted(claude_input_dir.glob("articles_*.csv")):
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            head = next(reader, None)
            if head is None:
                continue
            header = header or head
            id_column = head.index("article_id")
            rows.extend(r for r in reader if len(r) > id_column and r[id_column] in cited)
    buf = io.StringIO()
    writer = csv.writer(buf)
    if header:
        writer.writerow(header)
    writer.writerows(rows)
    parts = [
        "## draft_selections.json\n\n" + json.dumps({"must_know": [story], "should_know": []}, indent=2),
        "## articles_1.csv\n\n" + buf.getvalue(),
    ]
    fulltext_path = claude_input_dir / "article_fulltext.json"
    if fulltext_path.exists():
        fulltext = json.loads(fulltext_path.read_text(encoding="utf-8"))
        if isinstance(fulltext, dict):
            scoped = {k: v for k, v in fulltext.items() if k in cited}
            parts.append("## article_fulltext.json\n\n" + json.dumps(scoped, indent=2))
    return "\n\n".join(parts)


def build_per_story_body(body: str) -> str:
    """The single-turn body, scoped to one story.

    build_single_turn_body already replaces the whole numbered instructions block (including
    "for each story in draft_selections.json") with the inline-input block, so the only
    per-story wording left to change is the rules line. Derived, not rewritten: the probe
    block is asserted byte for byte by the same test that guards build_single_turn_body.
    """
    out = build_single_turn_body(body)
    return out.replace("- Check EVERY story (must_know and should_know).", "- Check the one story.")
```

Add `import csv` and `import io` at the top of `orchestrate.py` if absent (check with `rg -n '^import (csv|io)$' newsroom/src/orchestrate.py`).

- [x] **Step 4: Run the tests to verify they pass**

Run: `docker compose run --rm --entrypoint pytest ci -q newsroom/tests/test_orchestrate.py -k "story_corpus or per_story_body or probe_survives"`
Expected: PASS (3 tests)

If `test_per_story_body_scopes...` fails on the "every story" assertion, read the current `coherence.md` rules block: the exact sentence to replace is the one beginning `- Check EVERY story`. Adjust the `replace` target to the current text; do not weaken the assertion.

- [x] **Step 5: Write the failing test for the merged report**

```python
# newsroom/tests/test_eval_coherence.py (create if absent; mirror test_eval_repair.py's imports)
def test_per_story_reports_merge_in_draft_order(tmp_path, monkeypatch):
    """17 calls produce 17 results, one per story, in the draft's order, so score() maps
    headlines exactly as it does for the multi-turn report."""
    import eval_coherence, orchestrate
    (tmp_path / "draft_selections.json").write_text(json.dumps({
        "must_know": [{"headline": "One", "summary": "s", "why_it_matters": "w", "sources": [{"article_id": "A1"}]}],
        "should_know": [{"headline": "Two", "summary": "s", "sources": [{"article_id": "A2"}]}],
        "preheader": "p",
    }))
    (tmp_path / "articles_1.csv").write_text("article_id,title,summary\nA1,a,b\nA2,c,d\n")

    async def fake_run_agent(prompt, **kw):
        head = json.loads(prompt.split("## draft_selections.json\n\n", 1)[1].split("\n\n## ", 1)[0])
        h = head["must_know"][0]["headline"]
        class R:
            ok = True
            text = json.dumps({"results": [{"headline": h, "article_ids": ["A1"], "pass": h == "One",
                                             "reason": "" if h == "One" else "summary: x",
                                             "failed_fields": [] if h == "One" else ["summary"]}]})
            usage = {}
            total_cost_usd = 0.0
        return R()

    monkeypatch.setattr(eval_coherence.claude_cli, "run_agent", fake_run_agent)
    out = tmp_path / "coherence_report.json"
    asyncio.run(eval_coherence.run_per_story_to_file(out, "m", "**Instructions:**\n1. x\n**For each field, run all three probes\n- Check EVERY story (must_know and should_know). x\n3. Use the Write tool to write the result to `/app/data/claude_input/coherence_report.json`\n- DO NOT use Bash. Use Read and Write tools only.\n", {"type": "disabled"}, tmp_path))
    report = json.loads(out.read_text())
    assert [r["headline"] for r in report["results"]] == ["One", "Two"]
    assert [r["pass"] for r in report["results"]] == [True, False]
```

- [x] **Step 6: Run it to verify it fails**

Run: `docker compose run --rm --entrypoint pytest ci -q newsroom/tests/test_eval_coherence.py`
Expected: FAIL with `AttributeError: module 'eval_coherence' has no attribute 'run_per_story_to_file'`

- [x] **Step 7: Implement the per-story runner and the flag**

```python
# newsroom/src/eval_coherence.py, after run_single_turn_to_file

async def run_per_story_to_file(out_path: Path, model: str, body: str, thinking: dict, fixtures: Path) -> None:
    """Per-story single-turn arm: one call per story, each seeing only its cited sources.
    Results are merged in draft order so score() maps them like the multi-turn report.
    Runs at most 4 stories concurrently, the same width as the WRITE fan-out."""
    if out_path.exists():
        out_path.unlink()
    draft = json.loads((fixtures / "draft_selections.json").read_text(encoding="utf-8"))
    stories = [s for tier in ("must_know", "should_know") for s in draft.get(tier, [])]
    system_prompt = orchestrate.build_per_story_body(body)
    sem = asyncio.Semaphore(4)
    usage_total = {"input": 0, "cache_write": 0, "cache_read": 0, "output": 0, "cost": 0.0}

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
        usage_total["input"] += u.get("input_tokens", 0)
        usage_total["cache_write"] += u.get("cache_creation_input_tokens", 0)
        usage_total["cache_read"] += u.get("cache_read_input_tokens", 0)
        usage_total["output"] += u.get("output_tokens", 0)
        usage_total["cost"] += res.total_cost_usd or 0.0
        return results[0]

    merged = await asyncio.gather(*(one(s) for s in stories))
    out_path.write_text(json.dumps({"results": list(merged)}), encoding="utf-8")
    print(
        f"  [per-story x{len(stories)}] input={usage_total['input']} cache_write={usage_total['cache_write']} "
        f"cache_read={usage_total['cache_read']} output={usage_total['output']} cost=${usage_total['cost']:.4f}"
    )
```

In `main()`:

```python
    ap.add_argument("--per-story", action="store_true", help="one single-turn call per story, cited sources only")
    ...
        if args.per_story:
            asyncio.run(run_per_story_to_file(fixtures / REPORT_NAME, model, body, thinking, fixtures))
        elif args.single_turn:
```

- [x] **Step 8: Run the test to verify it passes, then the whole suite**

Run: `docker compose run --rm --entrypoint pytest ci -q newsroom/tests/test_eval_coherence.py newsroom/tests/test_orchestrate.py`
Expected: PASS
Run: `bin/ci`
Expected: `CI passed`

- [x] **Step 9: Commit**

```bash
git add newsroom/src/orchestrate.py newsroom/src/eval_coherence.py newsroom/tests/test_orchestrate.py newsroom/tests/test_eval_coherence.py
git commit -m "feat(eval): a per-story single-turn arm for the coherence eval"
```

The commit body states the hypothesis (bounded corpus restores absence detection) and cites the 2026-08-31 numbers it is measured against.

- [x] **Step 10: Run the measurement** (done 2026-09-03; verdict above)

Run, in order, and keep the full output in `docs/2026-09-03-per-story-coherence-measurement.md`:

```bash
bin/eval-coherence --runs 5                 # multi-turn baseline, today's prompt (expect ~4.6/6, 0 false drops)
bin/eval-coherence --runs 5 --per-story     # the arm under test
```

Read the scorecard as: mean recall over 6; how many of 5 runs caught idx 4 (`(4, 'summary')`, the absence case); false drops per run; per-story tokens from the usage line.

**Gate for Phase A (checkers):** per-story mean recall >= the multi-turn mean on the same day, idx 4 caught in >= 4 of 5 runs, 0 false drops in 5 runs. Any one failing keeps COHERENCE on the tool loop and limits Phase A to generation stages. Record the verdict in the measurement doc and in the memory index.

---

### Task 2: Per-story single-turn WRITE arm

*Landed 2026-09-03 as 23df49c + 44fe45c; Step 6 (the measurement) not yet run.*

**Files:**
- Create: `newsroom/src/eval_write_turns.py`
- Create: `bin/eval-write-turns` (copy `bin/eval-write-arms`, swap the module name)
- Modify: `newsroom/src/write_fanout.py` (add `branch_corpus(branch_dir: Path) -> str` and `single_turn_branch_body(body: str) -> str` next to `branch_body`)
- Test: `newsroom/tests/test_write_fanout.py` (next to `TestBranchBody`)

**Interfaces:**
- Consumes: `write_fanout.build_branches(claude_input_dir) -> FanOut` (`.branches: list[Branch]`, each with `.dir`, `.tier`, `.name`), `write_fanout.branch_body`, `write_fanout.branch_story(branch_dir)`, `claude_cli.run_agent`, `eval_graders.grade_selections`, and the multi-turn coherence path in `eval_coherence.run_agent_to_file` for the endpoint.
- Produces: `write_fanout.branch_corpus(branch_dir) -> str` (every file in the branch dir the prompt lists, inlined under `## <name>` headings, in the prompt's order), `write_fanout.single_turn_branch_body(body) -> str` (the branch prompt with the Read/Write I/O replaced by the inline block and "Reply with the JSON object and nothing else").

- [x] **Step 1: Write the failing tests**

```python
# newsroom/tests/test_write_fanout.py
class TestSingleTurnBranch:
    def test_corpus_inlines_exactly_the_files_the_prompt_lists(self, tmp_path):
        branches = write_fanout.build_branches(_seed(tmp_path, extras=("recap.txt", "weekly_recap.txt"))).branches
        corpus = write_fanout.branch_corpus(branches[0].dir)
        for name in ("selected.json", "articles_1.csv", "weekly_recap.txt"):
            assert f"## {name}" in corpus
        assert "## recap.txt" not in corpus  # copied into the dir, never read by WRITE

    def test_single_turn_body_keeps_every_rule_and_drops_the_tools(self):
        body = WRITE_SPEC.read_text(encoding="utf-8").split("---", 2)[2].strip()
        out = write_fanout.single_turn_branch_body(write_fanout.branch_body(body, Path("/tmp/b/s00")))
        rules_start = body.index("**Writing style")
        rules_end = body.index("**Output schema:**")
        assert body[rules_start:rules_end] in out
        assert "Use the Read tool" not in out and "Use the Write tool" not in out
        assert "Reply with the JSON object and nothing else" in out
```

- [x] **Step 2: Run them to verify they fail**

Run: `docker compose run --rm --entrypoint pytest ci -q newsroom/tests/test_write_fanout.py -k SingleTurnBranch`
Expected: FAIL with `AttributeError: module 'write_fanout' has no attribute 'branch_corpus'`

- [x] **Step 3: Implement**

```python
# newsroom/src/write_fanout.py, after branch_body

# The files write.md's read step lists, in its order. recap.txt is copied for parity with the
# run dir but the prompt never reads it, so it stays out of the corpus.
_BRANCH_CORPUS_FILES = (
    BRANCH_SELECTED_NAME,
    BRANCH_ARTICLES_NAME,
    "weekly_recap.txt",
    "article_fulltext.json",
    "recent_digest_headlines.txt",
)

_SINGLE_TURN_IO = (
    "**Your input arrives in the next message, inline, one section per file. "
    "There are no files to open and no tools available.**\n\n"
    "**Reply with the JSON object and nothing else** -- no preamble, no code fence, no commentary.\n\n"
)


def branch_corpus(branch_dir: Path) -> str:
    """Everything the tool-loop WRITE opens with Read, inlined in the prompt's order."""
    parts: list[str] = []
    for name in _BRANCH_CORPUS_FILES:
        path = branch_dir / name
        if path.exists():
            parts.append(f"## {name}\n\n{path.read_text(encoding='utf-8')}")
    return "\n\n".join(parts)


def single_turn_branch_body(body: str) -> str:
    """The branch prompt with its I/O section swapped for the inline block. Derived, so the
    rules cannot drift between the two deliveries; a test holds them byte for byte."""
    start = body.index("**Instructions:**")
    end = body.index("**Writing style")
    out = body[:start] + _SINGLE_TURN_IO + body[end:]
    return out.replace("- DO NOT use Bash. Use Read and Write tools only.\n", "")
```

- [x] **Step 4: Run the tests to verify they pass**

Run: `docker compose run --rm --entrypoint pytest ci -q newsroom/tests/test_write_fanout.py`
Expected: PASS

- [x] **Step 5: Write the harness**

`newsroom/src/eval_write_turns.py` replays an archived run (default: the newest with a `write_branches.json` artifact) through both deliveries, `--reps 5` each, and reports the within-arm spread first. Pattern: `eval_write_arms.py` for the shell wrapper and the archive-restore shape; its `build_arm` and `deltas` helpers are specific to the thread-delta A/B and do not apply here. The harness:

1. Restores the run's `claude_input/` from `run_artifacts` into `/app/data/eval-write-turns/<run>/`, exactly as `eval_write_arms.py` does.
2. Calls `write_fanout.build_branches` on it.
3. Arm **T** (tool loop): for each branch, `claude_cli.run_agent("Begin.", system_prompt=write_fanout.branch_body(body, branch.dir), tools=["Read", "Write"], allowed_tools="Read Write", cwd=str(branch.dir), thinking=<write.md's>, model=<write.md's>)`, then `write_fanout.branch_story(branch.dir)`.
4. Arm **S** (single turn): for each branch, `claude_cli.run_agent(write_fanout.branch_corpus(branch.dir), system_prompt=write_fanout.single_turn_branch_body(write_fanout.branch_body(body, branch.dir)), tools=[], allowed_tools="", max_turns=1, ...)`, parse the reply with `orchestrate.parse_json_object` (the shape-agnostic extractor; `parse_coherence_report` insists on a `results` list and returns None for a WRITE draft), write it to `branch.dir / "draft_selections.json"`, then `write_fanout.branch_story(branch.dir)`.
5. Fans each rep in with `write_fanout.assemble_draft`, writes `draft_<arm><rep>.json`, then runs the shipped multi-turn COHERENCE over each draft with `eval_coherence.run_agent_to_file` against the restored corpus, and counts `pass: false` results.
6. Prints per rep: stories written, coherence flags, L1 failures (`eval_graders.grade_selections`), tokens (input, cache write, cache read, output) and cost. Then per arm: min, mean, max of flags and of tokens.

Concurrency 4 per arm; arms run sequentially so the container cache does not favour the second.

- [ ] **Step 6: Run it once with `--reps 1` to prove both arms complete, then `--reps 5`**

```bash
bin/eval-write-turns --run 285 --reps 1
bin/eval-write-turns --run 285 --reps 5 | tee docs/2026-09-03-per-story-write-measurement.md
```

**Gate for Phase A (generators):** arm S coherence flags, mean over 5 reps, within arm T's spread or lower; L1 failures no worse; the tokens line is the saving. If S flags more than T's maximum, single-turn WRITE is rejected on quality and Phase A applies only to COHERENCE (if Task 1 passed), PREHEADER and RECAP.

- [x] **Step 7: Commit the harness, then the measurement doc, separately** (harness landed as 23df49c + 44fe45c; the measurement doc is pending the run)

```bash
git add newsroom/src/write_fanout.py newsroom/tests/test_write_fanout.py newsroom/src/eval_write_turns.py bin/eval-write-turns
git commit -m "feat(eval): replay a run's WRITE branches through the tool loop and single-turn, N reps each"
git add docs/2026-09-03-per-story-write-measurement.md
git commit -m "docs(eval): single-turn WRITE at per-story size, measured against the tool loop"
```

---

### Phase A outline (own plan after Tasks 1 and 2 report)

- `orchestrate.run_stage` gains a `delivery` field on the stage spec: `"files"` (today) or `"inline"`. Inline builds the user turn from the stage's listed inputs with the same builders the evals used, calls with `tools=[]`, `max_turns=1`, and parses the reply into the stage's output file so everything downstream is unchanged.
- Stages convert one at a time, each behind the eval floor and a `--resume`-able rollback (the file path stays; only who writes the file changes).
- Order: PREHEADER and RECAP first (Haiku, lowest risk), then WRITE branches, then COHERENCE per story (only if Task 1 passed), then repair and recheck (recheck is a checker; it inherits Task 1's verdict).
- `run_usage` keeps one row per stage; the per-branch breakdown stays in the run artifact.

### Phase B outline (own plan after Phase A)

- `write_fanout.branch_body` becomes a renderer: tier chooses whether the why_it_matters section and schema field appear; the presence of `article_fulltext.json`, `weekly_recap.txt` and `recent_digest_headlines.txt` in the branch decides whether their paragraphs render; a continuing thread renders "readers last saw: ..." from the linker instead of the whole recent-headlines file.
- The 2026-09-03 additions come out: "should_know stories get no why_it_matters ... omit the key" (structure does it), the "Separately, Meanwhile, On the sidelines" list (Goodharts the grader), and the COHERENCE sentence about missing fields (the per-story corpus lists the fields present).
- Every remaining NO rule in write.md is triaged into: a shaping decision in Python, a COHERENCE probe, or a worked example. The eval floor and the coherence eval say whether each removal cost anything. Removals ship one at a time.

### Phase C outline (own plan; can start now)

- Input: `cluster_tags.json` (per-article `primary_event`) and `clusters.json`. A cluster whose members carry more than one distinct `primary_event` phrase is a candidate. Expect ~20 to 30 per run.
- One Sonnet call per candidate (or one call over all candidates with a strict per-cluster output shape): "Do these articles report one event? If not, split them by event." Output is a partition; Python applies it before SELECT. Rubric: event identity, not genre (2026-09-01 decision).
- Measurement before shipping: hand-label 40 candidate clusters from three archived runs (one event / two or more), score the judgement's agreement, and replay `summary_bolt_on` and two-event headlines on a run with and without the split. The 2026-09-01 embedding gate's failure mode (a metric maximised by fragmentation) is avoided by scoring against the hand labels, not against the count of clusters.
- Cost bound: `_STAGE_BUDGET_USD` applies; expected ~$0.20 per run at Sonnet 4.6 with thinking disabled.

### Phase D outline (own plan after Phase A)

- For an ongoing story, the rendered lede is the thread delta, checked today by `thread_audit` and never by COHERENCE. With per-story COHERENCE in place, the delta text joins the story's fields in the per-story corpus and gets the same three probes.
- If a month of runs shows the audit and COHERENCE agree, the audit retires; if they disagree, the disagreement set is the next fixture.

---

## Self-review

- Spec coverage: the four rewrite items map to Phases A (tool-free), B (dynamic prompts), C (clusterer reasoning), D (one verifier). Tasks 1 and 2 are the gates for A. Phase B's dependency on A is real (the prompt becomes a Python-owned string only once the tool I/O section is gone), so B is not started early.
- Placeholders: Phases A to D are outlines by design and say so; Tasks 1 and 2 carry code. Task 2 Step 5 describes a harness rather than reproducing `eval_write_arms.py`'s 200 lines; the reusable helpers are named.
- Names used across tasks: `build_story_corpus`, `build_per_story_body`, `run_per_story_to_file`, `branch_corpus`, `single_turn_branch_body`, `_BRANCH_CORPUS_FILES`, `parse_json_object` (split out of `parse_coherence_report` in 44fe45c after review found the report parser cannot parse a WRITE draft).
