# Web tier and operations: decisions of 2026-09-24

**Date:** 2026-09-24
**Supersedes:** §3 (web tier), §5 (infrastructure and operations), §6 (seams) and the Plan B gate in §7.2 of
`superpowers/specs/2026-09-21-four-systems-rewrite-design.md`. Where the two disagree, this record wins.
**Evidence:** two adversarial reviews of the design (architecture, then the whole settled design) and one of
the Python worker, all on 2026-09-24; the measurements below were taken the same day on the dev stack.

## Decided

**One box, one Postgres.** The Hetzner CX23 (2 vCPU, 4 GB, no swap, x86, nbg1) runs everything. One
Postgres server holds the product database `digest` and Temporal's databases. HA is not a requirement;
downtime is acceptable; backups are a bonus.

**TypeScript for the pipeline and the site; Python only for full text.** The Python worker stays because
`2026-09-23-fulltext-extractor-fork.md` measured every TypeScript extractor worse (rule 5, 0/5). The Rust
`circulation/` retires at the cut-over. Considered and rejected: a small Rust service (it only fits static
generation), static generation, a CDN, Cloudflare Workers, Bun for the worker (Temporal does not support it).

**The site renders every request, with no cache.** Issue page TTFB 4.7 ms mean over 100 issues, `/threads`
120-200 ms, 72 MiB resident. At near-zero readership a render cache buys nothing measurable, and a whole-page
cache conflicts with per-response security headers and with `Accept` negotiation on one path. The newspaper
model is a data policy: the issue body is frozen (the stored blob), the chrome around it is current.

**CSP by hash, not nonce.** Inline `<style>` and `<script>` are allowed by their `sha256-` hash, so the
header is a function of the page and is testable.

**Deleted:** `/ask` (readers bring their own agent), the public MCP surface (the owner runs one locally),
the `openai` and `@modelcontextprotocol/server` dependencies, and the parity requirement for both.

**Agent formats.** `.md` by suffix and by `Accept: text/markdown` with `Vary: Accept`; `.json` by suffix
only. Markdown for existing issues is backfilled once from the stored HTML, then the serve-time converter is
deleted and the pipeline writes Markdown from the structured selections. Bare `/{date}` links redirect
forever: old emails carry them.

**Images.** One Dockerfile, a shared base stage, three targets: `worker`, `site`, `dev` (the Codex judge's
CA store; the judge runs only locally). The site is bundled with esbuild to a pinned outfile; its SBOM comes
from the lockfile and esbuild's metafile, not from scanning an image with no `node_modules`. npm stays.

**Builds** move to GitHub Actions (the repo is public; native amd64), with the SBOM gate in CI and build
attestations checked in CI. Where the box pulls from is open (ghcr for the digest's images; seanfloyd.dev's
image is private). The box keeps its last few images so a rollback never needs a registry.

**Deploys refuse to start while a digest run is in flight.** The memory caps leave ~25-75 MiB spare and a
site deploy briefly runs two site containers.

**Operations.** A host systemd timer checks disk and pings healthchecks.io `/fail` above 80%, outside
Temporal (a full disk takes Temporal down with it). Postgres gets `--oom-score-adj=-1000`. healthchecks.io
(dead-man for the run, the disk check, the backup) and UptimeRobot (outside-in HTTP) stay; subscribe to the
upstream status pages instead of building a poller.

**Backups: one producer.** The on-box nightly verified `pg_dump` is the only producer. The Mac job copies
the newest dump and fails when it is older than 26 h; a deploy runs the on-box dump before migrating;
seanfloyd.dev's deploy stops snapshotting the digest's data.

**Schema types.** The TypeScript side generates its row types from the database instead of
`type Row = Record<string, unknown>` (`digest/src/store/db.ts`).

**The Python worker** becomes a standalone package in `digest/python/` (its code, its four settings and its
tests move out of `newsroom/`), with a committed lockfile, no dev dependencies in the image, base images pinned
by digest, a network that reaches only the Temporal server, no fetches of private or link-local addresses on
any redirect hop, a wall-clock limit per fetch, and `deadline` (not `completed`) when the run's deadline
cuts fetches short.

## Open

- Where the box pulls images from.
- Token-format parity between the Rust and TypeScript subscribe-confirm links, for links sent just before the
  cut-over.
- Deferred experiments: Bun for the site only; scale to zero.
