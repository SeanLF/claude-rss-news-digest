# Pre-rebrand hardening — findings + punch-list (2026-07-04)

Two read-only improvement scans (Python `newsroom/`, Rust `circulation/`) + a `still_active`
dependency audit, run before the chrome/redesign port (which will touch a lot of code). Goal:
fix what's cheap and independent now; flag what should be done *with* the rebrand.

## Shipped this session

- **`02490ca` `fix(db)`** — SQLite `foreign_keys=ON` + `busy_timeout` on all 23 connection sites
  (FKs were silently unenforced). WAL deferred (interacts with `bin/db-clone`'s raw copy).
- **Hardening quick-wins** (this commit) — all verified + CI-green:
  - `cluster_extractjoin.parse_extract_items` filters non-dict items → a malformed extraction batch
    no longer crashes the whole CLUSTER stage (would lose the day's digest).
  - `merge.assemble_selections` raises `RuntimeError` (not `AttributeError`) if draft/coherence JSON
    isn't an object — matches its docstring; it's a public entry point.
  - `util.rs::format_date` bounds-guards the `MONTH_NAMES` index — a malformed month ("2026-13-01")
    no longer panics (under `panic="abort"` a panic kills the worker).
  - `feeds.py` deleted dead `get_source_id_by_name` + cache; `schema.py` sort-key coerces path to str.

## Dependencies (`still_active` audit)

Prod is **very likely already safe** — not the 7 live vulns the raw report suggests:
- `constraints-prod.txt` already pins **`lxml==6.1.1`** (> the 6.1.0 XXE fix). The other flagged
  packages (urllib3, idna, requests, pygments) aren't pinned, so prod **floats them to latest** at
  build → gets the fixes. `torch` is `eval`-extra only (not prod/CI). The SBOM reflects a stale
  local `.venv`.
- **Recommended durable fix:** add a Python dep vuln-scan (e.g. `pip-audit`/`osv-scanner`) to
  `make ci-full`, mirroring the existing `cargo audit`, so a vulnerable resolve fails CI
  automatically instead of relying on a manual `still_active` run. (Not done — decide fail-vs-warn.)

## Newsroom punch-list (not yet fixed) — ranked

**Rebrand-prep — do FIRST, it de-risks the rest:**
- **Brand strings scattered, several bypass `DIGEST_NAME`** — hardcoded `"News Digest"`
  (`render.py:389`, `broadcast.py:116/159/168`), all alert from/subject/body copy
  (`broadcast.py:191/201/203/230/235/242`), medal emojis (`render.py:371`). Route everything through
  `DIGEST_NAME` / a small `branding` module so the rename is one edit. **Single highest-leverage prep.**
- **`render.py:329-330` `{{MUST_KNOW}}`/`{{SHOULD_KNOW}}` filled with no existence check** — a template
  placeholder renamed during the rebrand yields a zero-article digest with no error. Mirror the
  raise-on-missing in `replace_placeholders:403`. Also `render.py:437-486`: after all replacements,
  scan for residual `{{...}}` and raise (several strip regexes hard-match exact template copy).

**HIGH (silent failure / correctness):**
- **`run.py:444-454` `--resume` skips archival + thread processing** — a resumed digest ships to
  subscribers but never lands in `/archive` or thread/dedup state. Move the archive +
  `_process_story_threads` block into `_render_record_deliver` so both paths converge. (Needs a
  side-effect test.)
- **`render.py:180` `inline_styles` swallows every exception with no log** — email ships un-inlined
  (broken styling) invisibly. Add `logger.warning` before the fallback.
- **`digest.py:121-166` `resolve_article_ids` drops unresolved stories with only a warning** — a stale
  `article_index.json` silently guts the digest. Surface a dropped-count metric + fail loud past a
  small threshold.

**MED:**
- `feeds.py:180` incremental date filter is fail-open (`parse_date → None` means "keep") — undated
  feeds defeat the freshness gate every run. Decide: drop undated, or stamp fetch-time.
- `render.py:197` email `<style>` resolver only matches bare `<style>` (misses `type="text/css"`).
- `broadcast.py:100` `get_audience_contact_count` fails open to 0 while the send still goes to the real
  audience (persists `recipients=0`). `broadcast.py:208/249` alert senders catch only `ResendError`
  (transport errors escape a non-fatal path).
- `orchestrate.py:314` coherence validator reaches into merge.py `_`-private API — promote to a shared
  module before the rebrand shuffles files.
- `config.py` env parsing crashes at import on a bad value with no var name / no range check.

**Test gaps:** no dedicated `test_dedup.py` / `test_prepare.py` / `test_feeds.py` / `test_digest.py`;
the resume path has no side-effect test. These are exactly the FP-prone / assembly paths a rebrand
could silently break.

**Quick wins:** `merge.py:298/305` array-guard (partly done); `db.py:80/254` except-scope too broad
(datetime/AttributeError escape `sqlite3.Error`); `prepare.py:125` escape-then-truncate splits entities.

## Circulation punch-list (not yet fixed) — ranked

The crate is in good shape (parameterized SQL, no reachable handler panics, clean data layer).
Weight fixes toward the **data layer that survives the rewrite**.

- **`stats.rs:60 fetch_stats_data` has ZERO tests** (HIGH) — the most complex query in the crate; a
  silent break during the port would be invisible. Add a seeded-temp-DB test (pattern in
  `archive.rs`/`thread.rs`).
- **Extract `open_ro(db_path)`** — the `Connection::open_with_flags(READ_ONLY)` + error-map boilerplate
  is copy-pasted ~8 sites; centralize (+ `busy_timeout`) so the rewrite re-touches less.
- **Extract `fetch_index_page`** — `handlers.rs:56 index` mixes query + inline HTML and duplicates the
  digest-list query (now in 3 forms); also loads ALL digests unbounded per request. Extract the data
  fn (or reuse `archive::fetch_archive`) so the rewrite is pure rendering + paginate the first load.
- **`get_digest` chrome via 5 sequential `inject()` string splices** (`handlers.rs:586-619`) — fragile
  needles, fail-open; replace with structured composition during the port rather than porting forward.
- **`Tier` enum** for `"must_know"/"should_know"` (stringly-typed across archive/stats/search) before
  three data structs get re-consumed by new templates.
- `stats.rs:206 dedup_stats … unwrap_or(None)` swallows real DB errors as "no rows"; unify handler
  error types behind one `AppError: IntoResponse`.
- `feed.rs` hand-built Atom (LOW) — escaping is adequate; the one real gap is `escape_html` not
  stripping XML-1.0-illegal control chars (low-probability LLM input).

## Suggested sequencing

1. **Now / standalone (safe, independent):** the shipped fixes above; add `pip-audit` to `ci-full`;
   `fetch_stats_data` tests; `open_ro` extraction; `inline_styles` logging; the resume-path fix.
2. **With the rebrand (coupled to templates/chrome):** brand→`DIGEST_NAME`; template placeholder
   existence checks; `fetch_index_page` / `get_digest` composition; `Tier` enum.
