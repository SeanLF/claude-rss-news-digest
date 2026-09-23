# First fresh end-to-end run of the TypeScript pipeline (2026-09-23)

Run 305 on a scratch copy of the live production clone (`data/e2e-20260923T0500Z.db`, gitignored), started
with `make digest-start DATE=2026-09-23` against local Temporal. The run fetched live feeds and ran every
model stage. Full text went through the Python worker on the `fulltext` queue, and the real TypeScript
render produced the web issue and the email. The hold was ended with a reject signal; broadcast is still a
stub.

| | run 305 (TypeScript) | old system, run 300 band |
|---|---|---|
| cost | $4.46 | $4.36-6.08 |
| wall, start to render | ~13 min | 779-1035 s |
| stories shipped | 17 (5 must-know, 12 should-know) | 16-22 |
| COHERENCE | 2/17 flagged, both repaired, 0 dropped | |
| full text | 36/49 extracted, `completed` | runs 300-304: 25-34 of 40-47 |

Cost by stage:

| stage | calls | cost |
|---|---|---|
| write | 17 | $1.28 |
| cluster-extract | 16 | $1.15 |
| coherence | 1 | $0.76 |
| select | 1 | $0.51 |
| recheck | 1 | $0.49 |
| repair | 1 | $0.22 |
| recap and preheader | | $0.04 |

Checks on the output:
- The stored full text holds no URLs.
- The page holds no internal article ids.
- Screenshots were taken at desktop width and at a true 390 px. Headless Chrome's `--window-size=390` renders
  at a 500 px viewport and crops, so the narrow shot framed the page in a 390 px iframe.

Known gaps, all stubs: 10 links are raw `news.google.com` URLs (gnews), and there are no thread badges
(threads). The masthead ignores `DIGEST_NAME`, which the Python render also does.

One run is a single sample, not a band.

## Run 305 again, with every stage real (2026-09-23, after plan A's five units merged)

This is a fresh run on a new, migrated scratch copy of the live clone (`data/e2e-20260923T0800Z.db`),
with production's thread settings (THREADS_ENABLED and THREAD_LATEBIND on) and broadcast disabled. A
stub check of the worker's wiring found none left: 36 activities, all real.

| stage | result |
|---|---|
| curation | 17 stories; COHERENCE flagged 0 of 17 |
| full text (Python worker) | 33 of 48 extracted, `completed` |
| Google News links (Python worker) | 13 of 13 decoded, `completed`; 0 raw news.google.com links in the page or the email |
| threads | linker ok, 8 syntheses, 0 audit failures; 9 thread badges rendered ("Ongoing · day 29") |
| record | disabled means not delivered: no web copy, no shown headlines, completed_at NULL |
| cost | $4.49 (write $1.48, extract $1.20, coherence $0.76, select $0.49, threads $0.48) |

Checks on the output:
- The page holds no internal article ids.
- Screenshots at desktop and a true 390 px are correct.

The run exposed one defect, fixed alongside this note: the unsent issue still left 17 thread
installments, which circulation's thread pages would serve. An issue that is not sent now takes back its
thread writes (`threadsRetract`).

It also confirmed that migrations are not the worker's job. The first start failed loudly on
`workflow_run_id`, because the scratch copy predated 20260923120000. In temporal mode bin/deploy applies
migrations, as it does today.
