# Language & tooling standards (mid-2026)

High-signal reference notes for an AI coding assistant working in this repo — current stable
versions, prevailing philosophy, best practices, and pitfalls per language/tool as of mid-2026.
Terse by design. Each file carries an "as of" date and is web-grounded; **verify versions before
relying on them** — this material dates fast.

Project stack these are tailored to: Python newsroom pipeline (uv, ruff, pytest, Claude Agent SDK) ·
Rust/Axum `circulation` web server · HTML email via Resend · SQLite + yoyo migrations · RSS/Atom feeds ·
Docker Compose · Makefile-driven CI.

### Languages
- [python.md](python.md)
- [rust.md](rust.md)
- [bash.md](bash.md)

### Web
- [html.md](html.md)
- [css.md](css.md)
- [javascript.md](javascript.md)
- [email-rendering.md](email-rendering.md)

### Data & formats
- [json.md](json.md)
- [yaml.md](yaml.md)
- [sql.md](sql.md) — SQLite
- [xml-rss-atom.md](xml-rss-atom.md) — feed ingest + syndication

### Build & infra
- [make.md](make.md)
- [docker.md](docker.md)

### AI & landscape
- [claude-agent-sdk.md](claude-agent-sdk.md)
- [tech-industry-direction.md](tech-industry-direction.md) — where the industry is heading

---

**Two code findings surfaced while writing these** (follow-ups, not doc issues):
1. `newsroom/src/db.py` opens SQLite with no `WAL` / `busy_timeout` / `foreign_keys=ON` — FK constraints
   are unenforced and the batch pipeline can block web reads. See [sql.md](sql.md).
2. `circulation/src/feed.rs::render_atom_feed` hand-builds Atom via `format!` + `escape_html` (string
   concatenation) — malformed/injection risk. See [xml-rss-atom.md](xml-rss-atom.md).
