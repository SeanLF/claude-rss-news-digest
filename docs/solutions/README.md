# Solutions

Reusable lessons from running news-digest in production. One lesson per file.

This directory replaces the flat `.claude/learnings.md`, which was untracked
(gitignored by `.claude/*`) and therefore existed only on one machine. Lessons
here are committed, greppable, and readable by agents starting a fresh session.

## Conventions

**The filename is the lesson, not the incident.** `rename-is-silent-until-every-reference-is-updated.md`,
not `2026-02-06-docker-rename-bug.md`. A future reader is searching for the
principle, not the date it was learned.

**One lesson per file.** If a write-up contains two independent lessons, it is
two files. Long incident narratives belong in `docs/postmortems/`; this
directory holds what was *learned*, linked back to the postmortem for detail.

**Frontmatter is the index.** Agents filter on it, so fill it in:

```yaml
---
title: <the lesson as a full sentence>
date: <YYYY-MM-DD, when it was learned>
category: <directory name>
module: <affected area, e.g. orchestrate, db, deploy, coherence>
problem_type: <best_practice | logic_error | schema_change | integration | performance | test_failure>
severity: <high | medium | low>
applies_when:
  - <a situation where a reader should stop and read this>
tags: [<searchable keywords>]
---
```

**Categories** (add directories as needed):

| Directory | For |
|---|---|
| `best-practices/` | Generalisable rules, not tied to one bug |
| `logic-errors/` | Ordering, state, and control-flow mistakes |
| `database-issues/` | Schema, migration, and query lessons |
| `integration-issues/` | External systems: systemd, Docker, SDKs, providers |
| `performance-issues/` | Cost and latency |
| `test-failures/` | Harness and eval lessons |

## When to write one

Capture the learning as **its own commit** when closing an incident or landing a
non-obvious fix, in the same session, while the reasoning is still loaded. A
lesson written a week later is a summary; one written at the time is evidence.

Prefer writing nothing to writing a restatement of the diff. The test: would
this have saved you time if you had read it before you started?

## Structure borrowed from

The frontmatter-and-category shape is adapted from
[koala73/worldmonitor](https://github.com/koala73/worldmonitor)'s `docs/solutions/`,
which is the best example of a compounding lessons corpus I have seen in a
production repo.
