# Gate fixture day-300 and the first live judge band (spec §7.2), 2026-09-22

Fixture: `digest.html` is the production digest of 2026-09-18 (run 300) from the `digests` table;
`inputs/` holds run 300's archived `articles_*.csv`, `draft_selections.json`, `article_fulltext.json` and
`selections.json` with every URL replaced by `[link]` (43 replacements; Hacker News summaries carry
"Article URL: https://…", see the finding below). The judge receives the digest with hrefs and URLs
stripped, and reads the inputs itself.

Command: `cd digest && npm run build && node dist/cli/gate.js --digest ../docs/proposed/gate-fixtures/day-300/digest.html
--inputs $PWD/../docs/proposed/gate-fixtures/day-300/inputs --judges gate/judges.json --reps 5 --out
../docs/proposed/gate-fixtures/day-300/band.json`. Judges: Claude Opus 5 (`claude -p`, Read/Grep/Glob) and
Codex (`codex exec`, read-only sandbox). Five must_know stories × seven criteria = 35 cells per rep, 5 reps each.

## Self-agreement bands (share of cells with the same verdict in all five reps)

| criterion | Claude Opus 5 | Codex |
|---|---|---|
| 1 Supported | 1.00 | 0.40 |
| 2 Bound correctly | 0.80 | 0.40 |
| 3 Not stale | 1.00 | 1.00 |
| 4 Earns its slot | 0.60 | 1.00 |
| 5 One event | 1.00 | 1.00 |
| 6 Selection | 0.20 | 0.20 |
| 7 Reads clean | 0.00 | 1.00 |
| overall | 0.66 | 0.71 |

Fails per rep: Claude 5, 13, 6, 4, 5; Codex 6, 7, 6, 6, 10.

**Not usable as a gate criterion until the rubric is tightened** (agreement under 0.8): criterion 6 for both
judges, criterion 4 and 7 for Claude, criteria 1 and 2 for Codex. Criterion 6 asks the judge to name a
missing story; Codex named the same omission (Ukraine's 2027 defence budget) in every rep but flipped
which selected stories that makes fail. Claude's criterion 7 flipped every rep: the stripped digest shows
`[link]` where a URL was, which one rep reads as a template token and the next does not. The scrub, not
the digest, is under test there; the rubric should say what `[link]` is.

## Disagreements between the two first runs (7 of 35 cells): to Sean

| story | criterion | Claude | Codex |
|---|---|---|---|
| 0 | 1 | fail: 'veneer of democracy' is quoted from A175, not a cited source | pass |
| 0 | 6 | pass | fail: Ukraine defence budget omitted |
| 1 | 6 | pass | fail: same |
| 2 | 2 | fail: binds the Iran war to Saudi Arabia | pass |
| 2 | 4 | fail: why_it_matters repeats the summary | pass |
| 2 | 6 | pass | fail: same omission |
| 3 | 6 | pass | fail: same omission |

Under the spec, a day with an unresolved disagreement is not a passed day. Three of the seven are the two
families reading the same digest differently on content (0/1, 2/2, 2/4); four are one family's view on
selection, the criterion neither judge is stable on.

## Finding for the production pipeline

Run 300's `articles_*.csv` carried 24 URLs inside Hacker News summaries ("Article URL: https://…"). Spec §1
says no URL reaches a model stage; for HN items today it does. The prepare step should scrub summaries.

Raw verdicts and reasons: `band.json`.
