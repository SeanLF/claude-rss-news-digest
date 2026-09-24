# Headline search tuning: pre-registration (2026-09-23)

Written before any candidate was scored, and revised once before scoring after an adversarial review
(baseline, dedup confound, overlap depth, controls that could not fail, judge calibration, query set).
Results go below the line at the end; nothing above it changes after scoring except to mark a
deviation, named as one.

## Why

The TypeScript site ranks `story_sources.search` (headline weight A, source title weight B, English
stemming) with `ts_rank` over `phraseto_tsquery('english', q)`. On the prod clone "ceasefire" shares
3 of the Rust site's 26 distinct headlines in the top 50 (fork doc §7). The Rust site ranks FTS5
BM25 over `headline` and `original_title` (the same two texts), unstemmed, phrase match.

Rust is a reference, not ground truth. It is measured; the decision rests on relevance.

## Data

`data/prod-20260923b.db` (runs 1-305), copied with `cp -c` and imported with `bin/import-legacy`.
Rust's answer is recomputed from the same SQLite copy with the query `circulation/src/search.rs`
runs (FTS5 `MATCH` on the quoted query, `ORDER BY rank`, limit 50), through `node:sqlite`, and held
to the Rust server's recorded answers for "ceasefire" and "Iran nuclear" (`rust-recorded.json`).

## Queries (39, `queries.json`)

Derived from `story_sources` headline frequencies (`ts_stat` over distinct headlines) and
case-insensitive headline counts on the clone.

- Names: Netanyahu, Zelensky, Carney, Maduro, Musk, Xi Jinping, Anthropic, Mamdani (2 headlines)
- Places: Hormuz, Gaza, Taiwan, Sudan, Greenland, North Korea, Venezuela
- Topics: ceasefire, tariffs, tariff, climate, Ebola, earthquake, measles (11), bitcoin (4),
  hurricane (2), election
- Multi-word: Iran nuclear, Strait of Hormuz, interest rates, Supreme Court, peace talks,
  data centre, data center, oil prices, trade deal
- Common: AI, US, U.S., war, China ("US" is an English stop word: WRONG, see Deviations)

Singular/plural and spelling pairs (tariff/tariffs, centre/center) test stemming against `simple`.

## Metrics (per query, averaged over the 39)

A result's identity is the story, `(headline, issue date)`; one story has one row per cited source
(up to 61 rows on the clone). So that ranking and dedup are judged apart, the scorer reads every
system's output as its sequence of distinct stories, in first-appearance order.

- **rel@10 (primary).** Relevant stories among the first 10 distinct stories, over `min(10, R)`,
  where R is the distinct relevant stories in the judged pool for that query. Queries with R = 0 are
  left out of the mean.
- **overlap@10 with Rust.** Of Rust's first 10 distinct headlines, the fraction in the candidate's
  first 10 distinct headlines.
- **Recency.** Median age in days, before 2026-09-23, of the first 10 distinct stories.
- **Zero-result queries**: queries Rust answers where the candidate returns nothing.
- **Duplicate slots**: rows among the first 10 rows shown that repeat a story already shown; what
  dedup is decided on.

## Relevance judgement

Pooled (TREC style): every distinct `(query, headline)` among the first 10 distinct stories of any
system (candidates, Rust, controls). The judge sees only the query and the headline, shuffled, never
which system returned it. Relevant = the headline's story is about the query's subject: the person,
place or organisation is a principal subject, or the story is about the topic; for a multi-word
query, about the combined concept (Iran nuclear = Iran's nuclear programme), not merely both words.

1. **Hand labels first.** I label a seeded random sample of 60 pool pairs blind (query + headline
   only), before any model label exists.
2. **Two judge runs** by a cheap model (Haiku), one pass each over the whole pool, same prompt.
3. **Calibration**: each judge run's agreement with the hand labels is reported; a judge under 85%
   agreement with them is not used, and the whole pool is then labelled by hand.
4. **Final label**: the two runs' agreement; each disagreement adjudicated by hand, blind.

Labels are committed (`judgements.json`), so `make search-eval` re-scores with no model call.

**Band.** rel@10 is also computed under judge A's labels alone and judge B's alone.

## Candidates

B0 (what ships): phrase, `english`, `ts_rank` default weights, no dedup, ties `id DESC`. It returns
nothing for "US" (WRONG, see Deviations), which fails a hard constraint, so the baseline the rule compares against is
**B0′ = B0 + candidate 12** (fall back to `simple` when the English query is empty). B0 is reported.

Round 1, one factor at a time against B0′:
1. `ts_rank_cd` for `ts_rank`
2. normalization 1 (divide by 1 + log length)
3. normalization 2 (divide by length)
4. weights `{0, 0, 0.1, 1}` (source title down from 0.4 to 0.1)
5. `plainto_tsquery` (all words, any order)
6. `websearch_to_tsquery`
7. prefix: the query's last lexeme as `:*`
8. recency tie-break: `shown_at DESC` then `id DESC`
9. recency decay: rank / (1 + age_days / 180)
10. dedup: one row per `(run_id, headline)`, the story's best-ranked row
11. `simple` config for vector and query (no stemming, no stop words)
12. (in B0′) `english`, falling back to `simple` when the English query is empty

Round 2: combinations of the round-1 factors that beat B0′, plus the best of them with each remaining
factor. Pairs round 2 surfaces that the pool lacks are judged by the same process before scoring.

## Decision rule

Hard constraints (a candidate failing one is out):
- zero-result queries = 0;
- core Postgres only (no extension);
- as implemented, index-backed, p95 latency over the 39 queries ≤ 50 ms on the clone.

Ranking:
1. A candidate beats B0′ if its rel@10 exceeds B0′'s by ≥ 0.05 under the final labels AND is above
   B0′'s under judge A alone AND under judge B alone.
2. Among those, the best rel@10 wins; candidates within 0.02 of it are tied, and ties go to higher
   overlap@10 with Rust, then lower median age, then fewer changes from B0′.
3. If none beats B0′, B0′ ships.

Dedup (candidate 10) is decided on its own: adopted if it cuts duplicate slots and the winner with it
keeps rel@10 within 0.02 of the winner without it.

pg_search/ParadeDB BM25 is reported (not adopted) only if the winner's rel@10 is below Rust's by more
than 0.05.

## Negative controls (the harness exits non-zero if any fails)

- **Reference reproduced**: `node:sqlite` running search.rs's query returns the recorded Rust
  answers for "ceasefire" and "Iran nuclear", row for row.
- **Random rows**: 10 rows drawn at random from `story_sources` per query score rel@10 below B0′'s by
  ≥ 0.2 and overlap@10 < 0.1.
- **Oracle**: each query's judged-relevant pooled stories, first, score rel@10 = 1.
- **Shuffled labels**: the winner scored against label values permuted within each query's judged
  headlines loses ≥ 0.1. (Weak where nearly every matched headline is relevant; its value is reported.)
- **Truncation**: every scored system's first 10 distinct stories are fully judged (a non-zero
  unjudged count fails), and Rust returns rows for every query.

---

## Results (2026-09-23)

Reproduce: `make search-eval` (imports a fresh copy, re-scores from `judgements.json`, no model call;
`results.json` is its output). Pool: 1,482 `(query, headline)` pairs.

**Judges.** Haiku A agrees with the 60 hand labels 58/60, Haiku B 57/60 (both over 85%, so used);
A and B agree 1452/1482; the 30 disagreements adjudicated by hand, blind.

| system | rel@10 | (A) | (B) | overlap@10 | median age d | zero | dup slots |
|---|---|---|---|---|---|---|---|
| Rust FTS5 BM25 | 0.995 | 0.995 | 0.992 | 1.000 | 151 | 0 | 88 |
| B0 = B0′ (was shipped) | 0.985 | 0.982 | 0.987 | 0.400 | 98 | 0 | 159 |
| ts_rank_cd | 0.982 | 0.977 | 0.985 | 0.392 | 92 | 0 | 181 |
| norm 1 | 0.987 | 0.985 | 0.990 | 0.297 | 222 | 0 | 31 |
| norm 2 | 0.985 | 0.982 | 0.987 | 0.264 | 222 | 0 | 27 |
| weights {0,0,0.1,1} | 0.985 | 0.982 | 0.987 | 0.400 | 99 | 0 | 159 |
| plainto / websearch | 0.985 | 0.982 | 0.987 | 0.397 | 97 | 0 | 158 |
| prefix | 0.979 | 0.977 | 0.982 | 0.341 | 105 | 0 | 127 |
| recency tie-break | 0.985 | 0.982 | 0.987 | 0.400 | 98 | 0 | 159 |
| recency decay | 0.956 | 0.954 | 0.959 | 0.264 | 69 | 0 | 234 |
| **dedup (ships)** | **0.995** | 0.992 | 0.997 | 0.400 | 98 | 0 | **0** |
| simple | 0.985 | 0.982 | 0.985 | 0.441 | 96 | 0 | 174 |
| random rows (control) | 0.049 | | | 0.003 | 123 | 0 | 0 |

Controls: reference reproduced (both recorded answers row for row), oracle 1.000, random 0.049,
shuffled labels 0.985 → 0.692, 0 unjudged. p95 of the shipped query 3.9 ms (117 timed queries).

**Decision, by the rule.** Relevance is saturated: on phrase match nearly every matched headline is
about the query, so no ranking factor moves rel@10 by 0.05, and none beats B0′. Round 2 is empty (it
combines only factors that beat B0′). B0′ ships, and dedup is adopted on its own terms (duplicate
slots 159 → 0, rel@10 0.985 → 0.995). Rust's rel@10 is within 0.05: no case for pg_search.

What a reader sees: "Netanyahu"'s first ten rows were one story ten times; they are now ten stories.
"ceasefire" shows 50 distinct stories where Rust's 50 rows hold 26; 6 of those 26 are in ours (was 3).
Site parity (goldens 20260923T230128Z) is unchanged: 138 of 144, 0 failures.

Overlap with Rust stays at 0.40 and is not a target: Rust scores 0.995 by being as relevant, not by
ordering better, and its median story is 53 days older than ours.

**Deviations, named.**
- The review's premise that B0 returns nothing for "US" was wrong: `us` is not in Postgres's English
  stop list (`phraseto_tsquery('english','US')` = `'us'`), so B0 and B0′ answer the 39 queries
  identically. The fallback still ships as B0′: it answers queries English reduces to nothing ("the",
  "who", "it"), which Rust answered and B0 did not; it is index-backed (`search_simple`, 2.3 ms for
  "the") and not measured by this query set.
- Judge B's first chunk returned 200 of 250 labels; the missing 50 were judged by a fresh Haiku run
  with the same prompt. Six of the twelve judge chunks numbered their lines from 1; they were mapped back to pool
  ids by line position (A and B agree 249/250 on a remapped chunk).
