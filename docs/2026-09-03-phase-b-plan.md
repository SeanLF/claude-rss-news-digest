# Phase B: render the WRITE prompt from the branch's data

> **For agentic workers:** REQUIRED SUB-SKILL: use `superpowers:executing-plans` or
> `superpowers:subagent-driven-development`. Steps use checkbox (`- [ ]`) syntax.

**Goal:** `write.md` stops carrying rules *about* the data a branch holds and `branch_body`
starts rendering what that branch actually has. Structural facts — tier, which context files
are present, whether the story continues a thread — move out of prose and into what Python
hands the call.

**Written 2026-09-03 evening**, after Phase C's cheap directions closed
(`docs/2026-09-03-cohesion-stray-postcheck-probe.md`,
`docs/2026-09-03-cohesion-thin-pass-measurement.md`). Parent:
`docs/2026-09-03-stage-invocation-rewrite-plan.md`, "Phase B outline".

**Stack:** Python 3.14, `newsroom/src/write_fanout.py`, pytest in Docker
(`docker compose run --rm ci`), `bin/eval-regression` as the floor.

## Constraints inherited from the parent plan

- Derived, never hand-written: the rendered prompt must be produced from `write.md`'s own
  text, and a test asserts the surviving rules match byte for byte
  (`test_every_probe_survives_the_rewrite_byte_for_byte` is the pattern).
- No rule of the form "do not X" is added. Removals ship one at a time.
- TDD per task; one reviewer pass per commit; `bin/eval-regression` before any prompt lands.
- Per-stage thinking config is not touched.

## Scope split, and why

Phase B bundles two things with different risk and different owners:

| | what | status |
|---|---|---|
| **B1** | Render the tier, the present context files, and thread state from the branch | **unblocked** — Python-side, testable without a model call |
| **B2** | Remove the 2026-09-03 negative rules from `write.md`, starting with the "Separately / Meanwhile / On the sidelines / In other news" list | **awaiting Sean** — the handoff of 2026-09-03 says the word list stays for run 286 unless he says otherwise |

B1 lands first and on its own. B2 does not start until run 286's `summary_bolt_on` count is
read against the 0.86/run archive baseline, because that list was added to move exactly that
number and removing it before the first measurement discards the only evidence about it.

---

## B1 — the renderer

**Files:**
- Modify: `newsroom/src/write_fanout.py` (`branch_body`, ~line 99; the `_BRANCH_CORPUS_FILES`
  tuple already names the files a branch may hold)
- Modify: `.claude/agents/write.md` (mark the renderable sections with delimiters; no rule text changes)
- Test: `newsroom/tests/test_write_fanout.py` (next to `TestBranchBody`)

**Interfaces:**
- Consumes: `write_fanout.build_branches(claude_input_dir) -> FanOut` with `Branch.dir` /
  `.tier` / `.name`; `branch_story(branch_dir)`.
- Produces: `branch_body(body: str, branch_dir: Path, *, tier: str) -> str` — the current
  path redirect, plus: the why_it_matters section and its schema field render only for
  `must_know`; a paragraph about a context file renders only when that file is in the branch
  dir; the recent-headlines paragraph renders as the thread's own last-seen line when the
  branch carries thread state, else as today.

### Task 1: tier decides whether why_it_matters renders

- [ ] **Step 1: failing test**

```python
def test_should_know_branch_renders_no_why_it_matters_section_or_field(self, tmp_path):
    body = WRITE_SPEC.read_text(encoding="utf-8").split("---", 2)[2].strip()
    out = write_fanout.branch_body(body, tmp_path, tier="should_know")
    assert "**Why it matters" not in out
    assert "why_it_matters" not in out
    # the rules that are not tier-specific survive byte for byte
    start, end = body.index("**Writing style"), body.index("**Output schema:**")
    assert body[start:end].replace(_WHY_BLOCK, "") in out

def test_must_know_branch_still_renders_the_whole_why_it_matters_section(self, tmp_path):
    body = WRITE_SPEC.read_text(encoding="utf-8").split("---", 2)[2].strip()
    out = write_fanout.branch_body(body, tmp_path, tier="must_know")
    assert "**Why it matters (must_know only)" in out
    assert "**Filler self-check" in out
```

- [ ] **Step 2: run, confirm they fail** —
  `docker compose run --rm --entrypoint pytest ci -q newsroom/tests/test_write_fanout.py -k why_it_matters`
- [ ] **Step 3: implement.** Delimit the why_it_matters block in `write.md` with HTML comment
  markers (`<!-- tier:must_know -->` … `<!-- /tier:must_know -->`) so the renderer excises by
  marker, not by fragile index arithmetic, and the block's text is unchanged. Strip the
  `"why_it_matters"` line from the rendered schema for should_know.
- [ ] **Step 4: run to green, then the whole file.**
- [ ] **Step 5: commit.** Body states that the sentence "should_know stories get no
  why_it_matters … omit the key entirely" is now structural and comes out of the prose.

### Task 2: a context paragraph renders only when its file is present

- [ ] **Step 1: failing test** — a branch seeded without `article_fulltext.json` renders no
  paragraph naming it; seeded with it, the paragraph is byte-identical to `write.md`'s.
  Same for `weekly_recap.txt` and `recent_digest_headlines.txt`.
- [ ] **Step 2-4:** same marker approach, one marker per file, keyed off the same tuple
  `branch_corpus` already uses so the two cannot drift.
- [ ] **Step 5: commit.**

### Task 3: thread state replaces the recent-headlines file for a continuing story

- [ ] Read `threads.py` for what the linker already knows about a continuing story; render
  "readers last saw: …" from it instead of the whole recent-headlines file. If the linker
  exposes no per-story last-seen line, this task stops and says so rather than inventing one
  — the value is removing a whole file from the branch, not paraphrasing it.

### Gate for B1

Run in order, and keep the output in `docs/2026-09-04-phase-b-measurement.md`:

```bash
bin/eval-regression
bin/eval-write-turns --run 286 --reps 3      # tool-loop arm only is enough: this is a prompt change
```

**Pass:** the eval floor is unchanged; L1 failures no worse; the `summary_bolt_on` count
within the arm's own spread. **Any regression rolls back the single task that caused it** —
tasks ship one at a time for exactly this reason. Report the within-arm spread before the
between-arm difference; n >= 3 per arm, and a difference smaller than the spread is noise.

---

## B2 — the removals (blocked)

Do not start before run 286 is read. Then, one at a time, each behind the same gate:

1. The "Separately / Meanwhile / On the sidelines / In other news" list (`write.md:49`) — it
   Goodharts the `summary_bolt_on` grader, which matches those literal words.
2. The COHERENCE sentence about missing fields — the per-story corpus already lists the
   fields present.
3. Every remaining NO rule (`write.md:52-56`) triaged into: a shaping decision in Python, a
   COHERENCE probe, or a worked example. `docs/2026-08-30` prompt audit's finding stands —
   naming an error class in a prompt does not fix it — so a removal that costs nothing on the
   floor is a removal that should have happened.

## Self-review

- The parent plan said Phase B waits on Phase A. It does not any more: `branch_body` already
  renders the per-branch prompt inside the tool loop, so the renderer has a home whether or
  not the call is tool-free. Phase A is closed and this is written against the tool loop.
- Task 3 is allowed to end in "not possible as specified". That is deliberate: the thread
  linker's shape is unread at planning time and a plan that forces a paraphrase would be
  worse than one that stops.
- B2 is listed but not scheduled, and the reason is a pending measurement plus Sean's
  explicit hold, not an oversight.
