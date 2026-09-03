---
title: A test that feeds the code a synthetic prompt has not tested the prompt; the first run of a new prompt path against a model must be one you can watch
date: 2026-09-03
category: test-failures
module: write_fanout, .claude/agents/write.md
problem_type: test_failure
severity: medium
applies_when:
  - a test builds its own prompt body ("read X then Y") instead of loading the agent's real .md
  - a stage is re-pointed at a new input layout (a branch dir, a subset, a filtered copy) and the prompt that reads that layout is unchanged
  - the first execution of a new pipeline path against a real model is scheduled to be the production run
  - a prompt names files with a glob and the agent has no tool that expands globs
tags: [testing, prompts, fan-out, write, fixtures, thinking-artifacts, glob, agent-tools]
---

# A test that feeds the code a synthetic prompt has not tested the prompt

## The lesson

The prompt is an input to a model, not to the code around it. A test that hands the code
a stand-in prompt proves the redirect, the file layout and the fan-in. It proves nothing
about what the real prompt tells the model to do with that layout, and the only test of
that is a model run. So when a prompt path changes shape, the first model run against it
must be one you can read the transcript of. If that first run is prod, the transcript is
the incident.

## What happened

The per-story WRITE fan-out (1c6ff2b, 8ffdb88) rebuilt WRITE's input: one branch directory
per story, each holding `selected.json` with one story and a single `articles_1.csv`
filtered to that story's cluster. `write.md` was left as it was, apart from the path
redirect, and it still said:

```
- ALL `/app/data/claude_input/articles_*.csv` files
```

The WRITE agent has `Read` and `Write` and no `Glob`. Told to read "ALL articles_*.csv",
it read `articles_1.csv`, then went looking for the rest. Run 285's thinking artifact for
branch s10, a three-article story:

> Since articles_33.csv didn't work, I'm wondering if the naming convention is different
> ... Let me try different filename patterns like "articles_033.csv" with padding, or
> alternative naming conventions like "articles_must_know.csv" ...

That branch billed 90 s and $0.20 with 160k cache-read tokens for three articles. Every
branch paid some of the same tax; s14's whole reasoning trace is three lines, two of them
about the articles files.

The fan-out's tests were thorough about everything except this. `test_write_fanout.py`
checks the redirect with a body it writes itself (`"read .../selected.json then
.../articles_1.csv"`), and a second class loads the real `write.md` to prove that
`branch_body` changes nothing but the path. Both pass. Both were designed to. Neither
reads the prompt as the model would: as instructions about a directory that no longer
looks the way the instructions assume. The fan-out was deployed behind a flag on
2026-09-01, the flag stayed off for run 284, its removal (8ffdb88) deployed on 2026-09-02, and run
285 was the first time a model ever saw the branch layout.

## The shape

Look for the same gap whenever the change is "same prompt, new input":

- A test that constructs the prompt it tests. It is testing the plumbing and will say so
  if you read the fixture, but the test name says "prompt".
- A prompt that describes the input directory (file names, counts, "all of", globs). The
  description is a claim about the layout and goes stale the moment the layout moves.
- An agent tool list that cannot do what the prompt asks. A glob in a prompt for an
  agent with no `Glob` was a latent bug even before the fan-out; the fan-out made it
  expensive.

What to do instead:

- Load the real agent file in the test and assert on what it tells the model about the
  layout the code builds. `test_read_step_names_the_branch_articles_file_not_a_glob` is
  the version of this that exists now: it fails if `articles_*.csv` comes back.
- Before the first prod run of a re-shaped prompt path, run it once where the thinking
  artifacts are yours to read. `thinking_write_*.txt` is archived per branch precisely so
  that "what did the model do with the prompt" is a question with an answer. The answer
  was there on 2026-09-01; nobody had a run to ask it of.
- Treat a flagged rollout as unfinished until the flag has been on for a run you looked
  at. A flag that stays off and is then deleted is an untested deploy with extra steps.

## Related

- [[unit-tests-at-both-ends-of-a-seam-pass-while-the-seam-is-broken]] -- the same
  failure one level down: each side of a contract tested with inputs it built itself.
- [[a-bound-that-guarded-one-call-does-not-guard-n]] -- the other thing the fan-out
  inherited unchanged and should not have.
- [[a-canary-nothing-runs-is-not-a-canary]] -- a check that never sees the real artefact.
