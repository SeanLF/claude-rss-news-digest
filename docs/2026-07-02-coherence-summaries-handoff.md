# Handoff — extend COHERENCE to summaries/why_it_matters + open product items (2026-07-01→02)

Written near a full context window to hand a FRESH session the next block cleanly (the project's
"plan in one session, implement fresh = ~3x more efficient" rule). Keep **Opus** for this
(implementation wants the review-gate rigor; Fable was great for divergent ideas, not this). It's a
focused single change — **subagent-driven is NOT needed**; a normal TDD + review-gate flow fits.

## Branch state (READ FIRST)
`feat/extractjoin-cluster-stage`, ~33 commits, all CI-green, **HELD from deploy (Sean's call — do
NOT merge to main or run bin/deploy).** This session shipped: the extract-join CLUSTER stage, the
Item-1 WRITE stale-fact fix, SDK pin + canaries, the pre-deploy audit fixes (incl. the coverage-
guard bug), `run_usage.duration_ms`, C1 (parallel extraction, 3.6x), image slim (911→664MB, 0
CRITICAL vulns), check-versions fixes. Everything additive/behaviour-preserving or independently
re-validated, so the deploy validation holds. Full context: memory `project_sonnet5_eval.md`,
`docs/2026-07-01-improvement-roadmap.md` (the backlog + what's been killed/measured).

## TASK 1 (do first) — extend COHERENCE to summaries + why_it_matters
**Why:** `.claude/agents/coherence.md` fact-checks **headlines only** (line ~2). The confirmed
shipped reader-facing bug this session — the "puts the Biden administration in a bind" stale-prior —
was in a **summary**, so the safety net structurally could not catch it. WRITE-side prevention
shipped (the `{{CURRENT_DATE}}` + no-stale-world-state rule), but the DETECTION hole is still open,
and it covers the failure class the project cares most about (fabrication/misattribution in the text
readers actually read — headlines are ~15% of the words).

**What to do:**
1. Read `.claude/agents/coherence.md` + `newsroom/src/merge.py` (`assemble_selections` drops entries
   whose coherence entry has `pass:false`) + `newsroom/src/orchestrate.py` `validate_coherence`.
2. Extend the COHERENCE prompt to also check the SUMMARY and WHY_IT_MATTERS against the cited source
   articles (same "supported by the cited sources alone" test WRITE's citation rule uses), not just
   the headline. Keep the output schema shape (`results:[{headline/pass/...}]`) so `merge` +
   `validate_coherence` still parse — OR extend the schema deliberately and update both.
3. **Fold in the model swap (roadmap C4): move COHERENCE from sonnet-4-6 → claude-haiku-4-5** in the
   same change (mechanical per-item fact-check = Haiku register; RECAP already moved; `claude-haiku-4-5`
   is already in `usage.py:_PINNED_MODEL_IDS`). The extra summary/why tokens are then ~free on Haiku.
4. **TDD + the DON'T-OVER-DROP caveat (there is history — the closed "COHERENCE over-drop" incident):**
   keep the fail condition to fabrication/contradiction ONLY (a specific number/name/date/quote in the
   summary that the cited sources don't support), NOT stylistic/editorial disagreement. Reproduce the
   Biden-summary case (or a crafted equivalent) as a failing check first.
5. **Validate on REAL output** (the bar): run COHERENCE (Haiku, extended prompt) on 2-3 archived
   snapshots via the eval harness / a dry run; confirm it (a) catches a planted summary fabrication,
   (b) does NOT over-drop clean digests (compare drop-rate vs the current headline-only version).
   Watch that Haiku isn't lenient/strict-shifted vs Sonnet (the COHERENCE golden is partly circular —
   trust paired direction, spot-check by eye).
6. Review gate (code-reviewer + silent-failure-hunter — this is error-handling/drop logic) + `make
   ci-fix` green + atomic WHY-commit. It's behaviour-changing (may drop more/differently), so it wants
   its own dry-run validation before the eventual deploy.

Files: `.claude/agents/coherence.md`, maybe `newsroom/src/merge.py` / `orchestrate.py` (if schema
changes), `newsroom/tests/`.

## Decisions carried from this session (b/c)
- **b) External heartbeat (dead-man's-switch): OPTIONAL, low urgency.** systemd `OnFailure=` already
  emails on "ran and failed" (Sean got one this morning). A heartbeat only adds the "timer/box/Docker
  dead → never ran" gap (the 2026-06-16 class). One ping after `db.complete_run()` (Python urllib, not
  curl — curl was removed from the image) or the systemd unit's `ExecStopPost=`, free-tier
  (healthchecks.io). Do it only if silent non-execution is the scenario worth insuring. NOT part of Task 1.
- **c) Full-text fetch for SELECTED stories: FEASIBLE, biggest editorial-ceiling lever, own task.**
  After SELECT, Python fetches the ~15-19 selected stories' article pages (`trafilatura`, pure-Python,
  4GB-fine), stores extracted text keyed by article_id, WRITE + COHERENCE read it instead of the
  300-char RSS blurb. "Claude never sees URLs" holds (Python fetches). Per-article fallback to the RSS
  summary on paywall/scrape-failure (strictly additive). Revisit write.md's "don't fabricate beyond the
  RSS summary" wording since the supported-fact set grows. This PAIRS with Task 1 (COHERENCE gets
  richer source text to check against) — consider doing c before/with the COHERENCE extension so
  COHERENCE checks against full text, not blurbs. Bigger (M), its own change.

## Other product ideas worth doing (Fable's fresh-eyes pass, all independent of the deploy branch)
Ranked, from `docs` + the roadmap: Atom feed (`/feed.xml`, ~50 lines Rust, S); `not_covered_blurb`
one-line footer (S, already written every run, dies as WRITE-only context); thread history pages
`/thread/{id}` (M, data already persisted); one-click HTTP per-story feedback (M, replaces dead
mailto); archive FTS5 search (M); og:image (S). Fable's direction challenge (endorsed): the last
two weeks were eval meta-work; the reader-facing surface has had ~zero investment — spend the next
block on product, starting with Task 1.

## Deploy (still Sean's call, held)
When greenlit: `deploy-check` skill → merge to main → `bin/deploy` (base-image migration → include
the newsroom service unit in the `-target` list) → watch the first cron. A fresh full dry-run
validates the whole accumulated branch.
