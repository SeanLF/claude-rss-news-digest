# Web tier in TypeScript: inventory and forks (2026-09-23)

Plan B of the rewrite spec (§3, gate §7.2 B). Sean, 2026-09-23: no intermediate Rust data layer; the
TypeScript site replaces `circulation/` and reads the Postgres product schema directly, and it lands
before the pipeline cut-over because the Rust site cannot read Postgres. This file inventories what is
being replaced and settles the forks before code; it is not a plan.

## 1. Inventory: every route in `circulation/src`

"Reads" are legacy SQLite tables; the new column is where the same data lives in the product schema
(`digest/db/migrations/`, after the vocabulary rename of 44a8dfe). "Parity" is the gate's contract: **P** = byte parity with the Rust
response on the same data (after the normalisations in §5), **S** = status, `content-type`, `location`,
`cache-control`, `link` and `vary` only, the body judged on requirement plus a11y and Lighthouse.

| Route | Rust handler | Reads | New source | Parity |
|---|---|---|---|---|
| `GET /` (`?before`, `?year`, notices) | `handlers::index` | `digests` (count, bounds, page), `shown_narratives` (tier counts, source ids per run), `sources.json` | highest revision per `issues.issue_date`; `story_sources` | S |
| `GET /` with `Accept: text/markdown`, `GET /index.md` | `index`, `index_md` | as above | as above | P |
| `GET /archive` (fragment) | `archive::archive_fragment` | as above | as above | S |
| `GET /issues/{date}` | `handlers::get_digest` | `digests.html`, `preheader` | latest revision's `html` | S |
| `GET /issues/{date}.md`, negotiated | `get_digest` | `digests.html` | as above | P (§5) |
| `GET /today`, `/today/translate` | `today`, `today_translate` | newest `digests.date` | newest `issues.date` | S |
| `GET /{date}`, `/{date}.md`, `/{date}/translate` | legacy redirects | none | none | S |
| `GET /issues/{date}/translate`, `GET /translate?to=` | `translate::*` | `digests` existence | `issues` existence | S |
| `GET /feed.xml` | `handlers::feed` | 30 newest `digests` (date, preheader) | 30 newest issue dates, latest revision | P |
| `GET /search?q=` | `search::search` | FTS5 `shown_narratives_fts` join `digests` on `run_id` | `story_sources.search` tsvector, `issues.run_id` | S |
| `GET /threads`, `/threads/more` | `thread::threads_index`, `threads_fragment` | `threads` (label, status, updated_at), `thread_installments` | `thread_state` view, `thread_updates` of published runs | S |
| `GET /thread/{id}` | `thread::thread_page` | `threads`, `thread_installments`, `digest_runs.run_at`, `digests`, `thread_questions.status` | `threads.merged_into_id`, `thread_state`, `thread_updates`, `runs.started_at`, `thread_question_state` | S |
| `GET /stats` (`?days=7,30,90`) | `stats::stats_html` | `source_health`, `shown_narratives`, `digest_runs`, `run_usage`, `dedup_log`, `sources.json` | `source_fetches`, `story_sources`, `runs` of `sent_runs` (was `completed_at IS NOT NULL`), `sends.recipients` (was `articles_emailed`), `model_calls`, `dedup_matches` | S |
| `GET /stats.json` | `stats::stats_json` | as above | as above | P |
| `GET /sources` | `handlers::sources` | `sources.json` only | same file | S |
| `GET /ask`, `POST /ask` (SSE), `POST /ask.json` | `ask::*` | through the MCP tools | same | S; behaviour by the promptfoo eval |
| `GET /connect`, `/feedback`, `/privacy`, `/llms.txt`, `/llms-full.txt`, `/robots.txt` | `handlers::*` | none | none | P for `llms.txt` and `robots.txt`, S otherwise (`/privacy` and `/llms-full.txt` redirect) |
| `POST /subscribe`, `GET /confirm` | `subscribe`, `confirm` | none (Resend API) | none | S |
| `POST /mcp` | `mcp::jsonrpc` | via the eight tools | same | P per tool (§5) |
| `GET /mcp`, `/.well-known/mcp.json`, `/.well-known/mcp/server-card.json`, `/mcp/tools.json` | `mcp::listing`, `server_card`, `tools_json` | none | none | P (§5) |
| `GET /mcp/tools/{name}.json` (the JSON bridge) | `mcp::tool_get` | via the tool | same | P |
| `GET /health` | `handlers::health` | table existence | a `SELECT 1` plus the tables the site reads | S |
| `GET /favicon.ico`, `/apple-touch-icon(-precomposed).png`, `/og-image.png`, `/assets/fonts/source-serif-4.{hash}.woff2` | static | none | none | P (bytes) |
| anything else | `handlers::not_found` | none | none | S (404) |

The eight MCP tools and their reads: `get_latest_issue`, `get_issue` (issue html → Markdown),
`list_issues` (the index Markdown), `search_headlines` (search), `list_threads`, `get_thread`
(threads), `get_sources` (`sources.json`), `get_stats` (the `/stats.json` value).

Behaviour that is not a route but is part of the contract:
- **Paths:** every route is wrapped in `NormalizePathLayer::trim_trailing_slash()` (`main.rs`), so
  `/stats/` answers as `/stats`; a known path with the wrong method answers 405 (axum), not 404.
- **Limits:** MCP, both doors, 120 requests per client per minute and 600 overall; `/ask` 3 per client and
  6 overall per minute, one answer in flight per client and two overall, 500 answers a day; subscribe 5
  per client per hour. The client key is the rightmost `X-Forwarded-For` hop (`mcp::client_key`).
- **MCP transport:** stateless, JSON responses (no SSE), the SDK's allowed-hosts check off (a public
  endpoint; a loopback-only Host check would refuse every real request).
- **Configuration read at build time:** `sources.json` and `design/tokens.css` are compiled into the
  binary; the TypeScript site reads them at startup from the paths the digest image already copies.

Rust today: 15.9k lines in `circulation/src` (`fd -e rs . circulation/src -x wc -l`), 3.9k of them the
HTML templates, and 5.4k to 6.2k inline tests depending on how the test modules are counted.

## 2. Framework: Hono

| | Hono 4.13.8 | Fastify 5.12.5 |
|---|---|---|
| Maintained | pushed 2026-09-22, not archived, 32k stars | pushed 2026-09-23, not archived, 37k stars |
| Request model | Web-standard `Request`/`Response` | Node `req`/`reply` with its own abstractions |
| MCP | the official SDK v2 (`@modelcontextprotocol/server` 2.1.0) ships a `WebStandardStreamableHTTPServerTransport` and a first-party `@modelcontextprotocol/hono` adapter | a first-party `@modelcontextprotocol/fastify` adapter exists too |
| Security headers | `hono/secure-headers` built in, with a per-request CSP nonce | `@fastify/helmet`, nonce by plugin option |
| Tests | `app.request()` in-process, no socket; fits vitest as digest runs it | `app.inject()`, equivalent |

Reproduce: `gh repo view honojs/hono --json pushedAt,isArchived`, the same for `fastify/fastify` and
`modelcontextprotocol/typescript-sdk`; `npm view hono version`.

**Hono**, because the site is a set of handlers that build strings, and Hono is the thinnest thing that
routes them: Web-standard objects end to end (the MCP transport takes a `Request` and returns a
`Response` with no adapter glue), the secure-headers middleware already does the nonce, and nothing here
needs Fastify's schema validation or plugin lifecycle. The SBOM check (`still_active --sbom`) runs on the
commit that adds the dependencies, with the result in that commit.

Other adoptions, each replacing a hand-rolled Rust part:

| Rust | TypeScript | Why |
|---|---|---|
| rmcp 3.4.0 (the lockfile's) | `@modelcontextprotocol/server` 2.x | official SDK; both advertise `2025-11-25` as latest |
| reqwest + hand-written SSE frame parser in `ask.rs` | `openai` SDK (7.x) against OpenRouter's OpenAI-compatible API, `stream: true` | the SDK owns SSE framing, UTF-8 across chunks and tool-call deltas; the fallback legs, budgets and caps stay ours |
| `htmd` | `turndown` 7.2 with rules mirroring `markdown.rs` (labels bolded, `aria-hidden` dropped, empty anchors dropped) | the maintained HTML→Markdown converter for JS; byte parity with htmd is measured, not assumed (§5) |
| `mailchecker` crate | `mailchecker` npm, same project and list | same behaviour |
| HMAC token, rate limiter | `node:crypto` HMAC; a 20-line fixed-window limiter | wire format of the token kept, so links mailed by the Rust server still confirm |
| Resend HTTP calls | `resend` SDK, already a digest dependency | one client |

## 3. Where it lives: inside `digest/`, as `digest/src/site/`

One toolchain is the stated gain (spec §3), and the site needs the store's Postgres client
(`store/db.ts`), its PGlite test helpers, the id-leak scrubbers (`contracts/leaks.ts`,
`threads/text.ts`) and `sources.json` handling that digest already has. A separate `site/` package would
duplicate the Node 26, TypeScript 7, oxlint and vitest setup and a second lockfile, or need npm
workspaces, which moves digest's lockfile under a unit other agents are editing.

What keeps the site from dying with the pipeline is the process boundary, not the package: the site
runs as its own container (`node dist/site/main.js`) built from the same `digest/Dockerfile`, and a test
fails if anything under `src/site/` imports the workflow, activities, runner or Temporal, or if anything
outside it imports the site. The cost: the site image carries the pipeline's production dependencies on
disk (not in memory; unloaded modules cost nothing resident). Revisit with workspaces if that image size
ever matters.

## 4. Static versus dynamic

Spec §3 asked for a static archive rendered by the pipeline where that fits. Measured against what is
there, it does not fit, and every route is served from Postgres per request:

- The issue body already *is* rendered by the pipeline (`issues.html`); the site only injects chrome.
- The other "static" pages are parameterised: the index pages by cursor and year, threads by cursor,
  stats by window (7/30/90). Pre-rendering means every variant on every run plus a trigger on every
  template change, and a shared volume or object store between the worker and the site.
- What static files would buy is real: readers would survive a Postgres outage. The site's database is
  the Postgres the Temporal stack runs, restarted by a pin bump and capped at 384 MiB beside Temporal
  (data-model doc §2.3 item 3), so the dynamic site goes down whenever that Postgres does. A failed run
  alone does not take it down (the database outlives the worker), but those infrastructure events do.
  Sean accepted short downtime for the site (data-model doc §6, decision 1), which is what makes the
  trade acceptable, not an argument that it is free.

This is a deviation from spec §3's "static archive rendered by the pipeline", named here; the spec's
NFR "readers survive any pipeline failure" holds for pipeline failures and not for Postgres outages.
Reversible: the templates are pure functions of data, so a pipeline activity could write them to files
later without a second renderer.

## 5. Parity harness and the known divergences

The harness records the Rust responses from the Rust server running on a copy of the prod clone
(`data/prod-20260923b.db`), then runs the TypeScript app in-process against the same data imported into
Postgres with `bin/import-legacy`, and compares per contract. Goldens are derived (reproducible from the
clone and the Rust commit), so they live under gitignored `data/site-parity/`; the harness, the
comparison and its negative control are committed. Time-windowed answers (stats) are recorded with the
capture time; every windowed query takes "now" as a parameter (never Postgres `now()`), and the
TypeScript side is run with it set to the capture time.

Expected divergences, each a decided change rather than a regression, reported by count not hidden:
1. **Search order and match set.** FTS5 phrase match with BM25 versus `phraseto_tsquery('english')`
   with `ts_rank`: stemming and stop words widen the match, and the order differs (data-model doc).
   Compared as sets per query; the size of the difference is reported.
2. **Thread `updated_at` and order.** `threads.updated_at` is not derivable (data-model doc §5.1): the
   new value is the latest published installment's time, so dormant threads reorder in `list_threads`.
3. **Markdown from htmd versus turndown.** Byte parity is the target; the residual is measured over
   every issue in the clone and each class of difference is either fixed with a turndown rule or listed.
4. **Protocol versions** on the MCP card and in version negotiation. Both SDKs advertise `2025-11-25`
   as latest; rmcp's known list (the card's `protocol_versions`) also names `2026-07-28` and not
   `2024-10-07`, the TypeScript SDK's supported list the reverse, reaching `2026-07-28` only through
   `server/discover`. Measured by the harness, not assumed. The tool catalogue, the tool texts and the
   error codes are held to parity.
5. **Headers are a superset**: the security-header set (CSP with a nonce, HSTS with preload,
   permissions-policy, referrer-policy, nosniff, frame-options) is new on every response.
6. **MCP transport refusals.** A request without `text/event-stream` in Accept (406), with the wrong
   content type (415) or an unparseable body gets a JSON-RPC error object from the TypeScript SDK where
   rmcp sent plain text; for the unparseable body the SDK's status is 400 (a parse error), rmcp's 415.
7. **JSON is compared as values**, not bytes: serde_json prints a whole float as `1.0` where
   JavaScript prints `1`, and sorts object keys. A consumer parsing the JSON sees the same values.

Markdown is compared as a document: both texts must render the same under CommonMark with GFM tables
(`digest/src/site/parity/document.ts`), since htmd and turndown spell the same document differently
(`*` or `-` bullets, trailing spaces, table padding). A comparison that passes only this way is counted
separately from byte equality.

A divergence a request is expected to show is marked `known` in `requests.ts` with its reason; the
comparison still runs and is reported, and the gate counts it apart from unexplained failures. Search
answers capped at 50 are compared by count, since two rankings pick different fifties.

Held to parity rather than excused: the trailing-slash and 405 behaviour of §1 (Hono in strict mode
answers `/stats/` with 404, so the app is built non-strict and answers wrong methods with 405).

## 6. Requirements carried in, from spec §3 and the gate

- The journal lines that log an email beside an IP on subscribe and confirm go; the IP stays only for
  rate limiting and is not logged with an address.
- Opt-in misconfiguration is a hard failure at startup: subscriptions enabled with double opt-in on and
  no `SUBSCRIBE_TOKEN_SECRET` (or no `DIGEST_DOMAIN`) refuses to start, instead of adding contacts
  without confirmation.
- The contact-count line near 900 already exists on the send path (`CONTACT_THRESHOLD` in
  `digest/src/activities/broadcast.ts`, logged every send); the site adds none.
- The gate's "monitor green for seven days" needs an UptimeRobot monitor in terraform (spec §3). That
  lives in seanfloyd.dev, which this unit does not edit; it is on the infrastructure list it hands over.

## 7. Results (2026-09-23, on the prod clone runs 1-305)

- **Parity** (`make site-parity`, goldens `data/site-parity/20260923T230128Z`, Rust commit d17a32c): 138 of
  144 requests equal (3 of them as Markdown documents), the 6 others the known divergences of §5 (3 thread
  listings: `updated_at`; 3 MCP transport refusals), 0 unexplained. Every issue's Markdown, 282 of 282,
  renders the same as htmd's; 80 are byte-equal. Search: "Iran nuclear" returns the same 37 headlines.
  "ceasefire" matches 1,469 rows against FTS5's 1,468 (counted outside the harness: `SELECT count(*) FROM
  shown_narratives_fts WHERE shown_narratives_fts MATCH '"ceasefire"'` on the clone, and `... WHERE search
  @@ phraseto_tsquery('english','ceasefire')` on the import), but both answers are capped at 50 and the
  rankings pick different fifties: they share 3 of Rust's 26 distinct headlines. Passes only by the capped
  rule; a reader will see different "ceasefire" results. The recording is kept in the main checkout's
  `data/site-parity/20260923T230128Z` (gitignored); `make site-parity-record` makes a fresh one.
- **a11y and Lighthouse** (`bin/web-check --base` against the local site over the clone): the structural
  invariants pass on 10 pages; Lighthouse accessibility, best practices and SEO are 100 on all 10, with the
  CSP in force (a violation would have cost best practices its console-errors audit).
- **/ask eval** (`bin/ask-eval`, the two free OpenRouter legs): 6 of 8. The 2 failures are the model ending
  a turn empty after its tool budget; the Rust server run the same hour on the same models failed the
  script's first probe (504, timed out), so the eval cannot currently separate the port from the legs.
- **Dependencies** (`still_active --sbom` on digest's production SBOM): the new ones are all `ok` except
  turndown's `@mixmark-io/domino` (`stale`); the three `archived` are the ones already owed (require-from-string
  via the Agent SDK, source-map-loader via Temporal, xtend via pg).

## 8. What the cut-over owes (seanfloyd.dev is not edited here)

Infrastructure, in seanfloyd.dev:
- A web container from the digest image with `node dist/site/main.js`, replacing `digest-circulation` behind
  kamal-proxy on the same host name and health path (`/health`), port 8080, 256 MiB.
- Its environment: `DIGEST_DATABASE_URL` to the `digest` database with a **read-only role** (the site issues
  no writes; the pipeline's role migrates, and must `ALTER DEFAULT PRIVILEGES ... GRANT SELECT` to the site's
  role so tables and views later migrations add stay readable; the site's container must not run
  `migrate.js` as local compose does), `DIGEST_NAME`, `DIGEST_DOMAIN`, `HOMEPAGE_URL`, `SOURCE_URL`,
  `CONTACT_EMAIL`, the Resend and subscribe variables, the `ASK_*` variables. With subscriptions on and
  double opt-in on, a missing `SUBSCRIBE_TOKEN_SECRET`, `DIGEST_DOMAIN` or `RESEND_FROM` now stops the
  container at start; check the secrets exist before the apply.
- Network access from the web container to the Postgres that Temporal runs; no volume mount (the SQLite
  file mount goes).
- The UptimeRobot monitor for the site (spec §3) in terraform, which the plan B gate's seven green days need.
- Drop any header the proxy sets that now duplicates the site's own (CSP, HSTS, frame options).

Deleting `circulation/`, after the cut-over deploy has served from the TypeScript site:
- the tree itself, `Cargo.lock` with it; the `ci-rust` and `digest-circulation` services and the
  `cargo-*` volumes in `docker-compose.yml`; `bin/circulation`; the `circulation` Makefile target;
- `bin/ci`: the Rust suite and its `_SCOPE` rows; `newsroom/tests/test_ci_scope.py` rows naming them;
- `newsroom/tests/test_markdown_chrome_contract.py` (reads `markdown.rs`) and the Cargo.lock pins in
  `test_sbom_exceptions.py`, repointed at `digest/src/site/markdown.ts` and digest's lockfile;
- `bin/deploy` (`SERVICE_CIRCULATION`, the Rust image build and its provenance check), `bin/check-versions`,
  `bin/cssdiff` and `newsroom/tools/web_check.py` (`SERVICE` becomes `digest-site`, which needs data:
  `make site-local` first), `bin/ops` where it names the container;
- `bin/site-parity-record` builds the Rust image: re-record once before deleting and keep that recording
  with the commit that deletes the tree, or retire the harness with it;
- `newsroom/tests/test_pipeline_contract.py` and `test_sources_catalogue.py` (they read circulation's
  sources handling; repoint at `digest/src/site/sources.ts`), the `circulation/sources.json` symlink,
  `bin/a11y-check`'s usage text, the comments in `design/tokens.css` and `newsroom/templates/digest.css`;
- `newsroom/tools/web_check.py` now defaults to 8081 (circulation's local port since the site took 8080);
  at deletion it targets `digest-site` on 8080 again;
- README, CLAUDE.md and `docs/operations.md` sections on circulation.
