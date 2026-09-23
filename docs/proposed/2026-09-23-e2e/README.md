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
