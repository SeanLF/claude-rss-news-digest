# Fulltext extractor fork: TypeScript or the Python activity (pre-registered 2026-09-23)

Spec §4: "Python stays where a tool needs it (trafilatura for fulltext until a TypeScript extractor is
measured on our selected articles)." §8: a fork names its measurement, threshold and both branches, costed,
before it runs. This is that for fulltext.

## Corpus

Every production run from 300 onward with archived `selected.json` and `article_index.json`. The candidates
are what production fetches: the first `FULLTEXT_PER_STORY` (3) article ids of every selected story, deduped.
Each page is fetched **once**, by trafilatura's own `fetch_url` under the production config, and the bytes
are saved (gitignored `data/fulltext-fork/`). Every arm extracts from those same bytes, so the comparison is
of extractors and not of pages that changed between fetches.

## Arms

- **trafilatura** (production): `extract(html, include_comments=False, include_tables=False)`, the reference.
- **defuddle** 0.19.4 over linkedom, markdown output with link targets stripped.
- **readability** (@mozilla/readability 0.6.0) over linkedom, `textContent`.
- **dom-smoothie** (dom-smoothie-js, the Rust `dom_smoothie` crate as WebAssembly), text output. It was added
  before the measurement ran, on Sean's question about Rust libraries. WebAssembly needs no per-platform
  binary. rs-trafilatura was not added: it has no Node binding, and reaching it means maintaining napi glue.

All three are truncated at 4000 characters, as production's `FULLTEXT_MAX_CHARS` does.

## Metrics (promptfoo, one test per page, one provider per arm)

- **M1 success:** extracted text of at least 200 characters.
- **M2 agreement:** where an arm and trafilatura both succeed, token F1 against trafilatura (a multiset of
  lowercased word tokens).
- **M3 boilerplate:** the share of lines matching subscribe, newsletter, cookie, sign-in, advertisement,
  copyright, or "related" / "read more".
- **M0 fetch parity** (for the TypeScript branch only): Node `fetch`, with a 10 s timeout, succeeds on the
  same URLs at a rate within 2 points of `fetch_url`.

## Decision rule

The best TypeScript arm, ranked on M1 and then median M2, replaces trafilatura only if **all** of these hold:

1. M1 is no more than 2 points below trafilatura's.
2. Median M2 is at least 0.80.
3. M3 is no more than 1 point above trafilatura's.
4. M0 holds.
5. On the five pages with the lowest F1, reading both outputs finds the TypeScript arm kept the article body
   at least as well on three or more. Each read is recorded as a row.

Otherwise the Python activity stays. trafilatura is a reference, not ground truth: rule 5 exists because a
low F1 can mean either extractor is wrong.

## Branches, costed

- **TypeScript.** defuddle and linkedom go into the worker. The fetch uses `AbortSignal.timeout` and a 2 MB
  body cap. The parse runs in a `worker_threads` Worker that is `terminate()`d at a hard deadline. The bound
  is on the worker, not the waiter (docs/lessons: a deadline on the waiter does not bound the worker): a
  runaway parse would otherwise block the event loop, and with it every heartbeat on the worker. No second
  language in the image.
- **Python.** A Python worker (temporalio Python SDK) on its own `fulltext` task queue runs the existing
  `fulltext._collect_isolated`, and the TypeScript workflow calls it by activity name, which is Temporal's
  polyglot model rather than glue. The cost is a second image (python slim, plus trafilatura and lxml), a
  second process, and a second dependency set to pin and audit.

## Result (2026-09-23): the Python activity stays

Corpus: runs 300-304 from a live production clone, 218 candidates, 183 fetched
(`data/fulltext-fork-20260923T025813Z/`, gitignored). Run with `make fulltext-pages` and then
`make fulltext-fork`.

| arm | M1 success | M2 median F1 (p25) | M3 boilerplate |
|---|---|---|---|
| trafilatura | 151/183 (82.5%) | 1.000 (reference) | 1.62% |
| dom-smoothie | 151/183 (82.5%) | 0.956 (0.881) | 1.28% |
| readability | 151/183 (82.5%) | 0.933 (0.853) | 7.58% |
| defuddle | 145/183 (79.2%) | 0.916 (0.866) | 1.50% |

The best TypeScript arm is dom-smoothie. It passes rules 1-3 and **fails rule 5 at 0/5**:

| page | trafilatura kept | dom-smoothie kept |
|---|---|---|
| france24 302-A132 | the article lead | the YouTube consent notice |
| france24 301-A164 | the full body (3910 ch) | header chrome and a photo caption |
| france24 302-A137 | the consent notice, then the article | the consent notice, then other stories' headlines |
| arstechnica 304-A46 | the lead | a later section; the lead is lost |
| aljazeera 303-A23 | the title and "live page closed" | the site navigation menu |

Rule 4 (fetch parity) was not run: rule 5 already decides the fork.

M3 understates navigation junk, because "skip links" and "navigation menu" are not in the pre-registered
list. That makes the comparison kinder to the TypeScript arms, not harsher.

Next: the Python branch as costed above, a Python worker on a `fulltext` task queue.
