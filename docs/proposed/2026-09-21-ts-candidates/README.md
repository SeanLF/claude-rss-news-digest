# TypeScript candidate libraries, maintenance gate, 2026-09-21

`still_active --sbom=ts-candidates.cdx.json --markdown` over a hand-built CycloneDX SBOM of the
candidates at their npm-latest versions (no install; the SBOM is the candidate list, not a lockfile).
Full table in `still_active.md`. Re-run against the real lockfile's SBOM once the scaffold exists
(`--fail-if-critical` is the gate in the plan's spine).

| Candidate | Verdict | Note |
|---|---|---|
| @temporalio/{workflow,worker,client} 1.24.0 | current, clean | MIT |
| @anthropic-ai/claude-agent-sdk 0.3.278 | current, clean | non-standard licence; bundles the CLI |
| feedsmith 3.0.0 | current, clean | feed parsing; lenient by default, typed errors |
| @rowanmanning/feed-parser 2.1.5 | current, clean | fallback feed parser |
| rss-parser 3.13.0 | **flagged** | last release 2023/04, OpenSSF 3.2: out |
| @mozilla/readability 0.6.0 | **warned** | last release 2025/03, OpenSSF 5.5 |
| defuddle 0.19.4 | current, clean | fulltext extraction candidate, accuracy unmeasured |
| mjml 5.4.1 | current, clean | OpenSSF 5.0 |
| hono 4.13.8 | current, clean | web tier |
| better-sqlite3 13.0.3 | current, clean | SQLite driver |
| zod 4.6.5 | current, clean | schemas |
| promptfoo 0.123.1 | current, clean | eval runner |
| vitest 5.0.1 | current, clean | tests |

Consequence for the spec: feed parsing goes to TypeScript (feedsmith); fulltext extraction stays a Python
activity (trafilatura) until defuddle is measured against it on our selected articles, and readability is
not the fallback.
