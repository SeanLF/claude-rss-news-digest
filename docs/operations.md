# Operations Reference

Command reference and environment notes for running news-digest. Migrated from
the untracked `.claude/learnings.md` so it survives outside one machine.

Reusable *lessons* live in [`docs/lessons/`](solutions/); incident narratives
live in [`docs/postmortems/`](postmortems/). This file is the how-to.

## Containers

The newsroom container is **one-shot**: it runs and exits. There is no
persistent container to `exec` into.

```bash
# Run the pipeline (entrypoint passes flags to run.py)
docker compose run --rm digest-newsroom --dry-run

# Query the production DB (no sqlite3 binary on the server)
docker run --rm -v news-digest-data:/app/data <image> python3 -c "..."
```

- Production volume: `news-digest-data`
- Session JSONL volume: `news-digest-claude` (`/home/appuser/.claude/`)
- Systemd unit: `news-digest.service`

## Database

```bash
bin/migrate            # apply pending (local, runs in Docker)
bin/migrate --status   # check status
bin/ssh bin/migrate    # production
```

New migrations: `migrations/YYYYMMDDHHMMSS_description.sql`. The baseline uses
`CREATE TABLE IF NOT EXISTS`, so it is safe on existing databases with no
bootstrap step.

For queries, clone first (`bin/db-clone`) and query `data/digest-cloud.db`
locally. Note `make db-clone` **overwrites** `data/digest.db`, and raw-`cat`s
the prod DB over SSH, so it can truncate on a slow transfer -- verify
`page-count x pagesize == file size` and re-run if short.

## Local development

```bash
CLAUDE_CODE_OAUTH_TOKEN=$(op item get "seanfloyd.dev" \
  --fields NEWS_DIGEST_CLAUDE_OAUTH_TOKEN --reveal) \
  docker compose run --rm digest-newsroom .venv/bin/python src/run.py --dry-run
```

Requires `.env` (see `.env.example`). For `--dry-run` only display settings are
needed. Output lands in `data/output/digest-*.html`.

Run-mode flags:

- `--dry-run` -- no email, no DB writes; truncates to 20 articles
- `--no-email` -- full pipeline minus broadcast (still writes to the DB)
- `--write-only` -- re-render from existing selections
- `--force` -- override the duplicate-run guard (which fails closed)
- Use `--no-email --no-record --force` for a full-size test run

## Prompt test harness

```bash
bin/test-prompt snapshot                        # save current input
bin/test-prompt run baseline                    # run with the production prompt
bin/test-prompt run baseline --model opus --limit 5
bin/test-prompt diff <run1> <run2>
```

Custom prompts go in `newsroom/prompts/<name>.md`.

Use the `bin/test-prompt` wrapper rather than the `ci` service directly -- `ci`
mounts the host `.venv`, whose macOS symlinks break inside the container.

**The harness overwrites `data/claude_input/selections.json`**, a shared path,
so concurrent test runs clobber each other. To compare against production:

```bash
bin/ssh "sudo cat /var/lib/docker/volumes/news-digest-data/_data/claude_input/selections.json" \
  > /tmp/prod_selections.json
bin/test-prompt run <prompt> --model opus
diff <(jq --sort-keys . /tmp/prod_selections.json) \
     <(jq --sort-keys . data/runs/<run_id>/selections.json)
```

Runs are also copied to `data/runs/<run_id>/`.

### Debugging a hung harness

```bash
docker exec <container> ps aux
```

`0:00` CPU time after several minutes means stuck, most likely on auth/login.
Increasing CPU time means it is genuinely working. Stuck containers block new
`docker compose run` invocations -- kill them first.

## Environment notes

- `CLAUDE_CODE_EAGER_FLUSH=1` is required for usage tracking and is set in
  terraform, not in `docker-compose.yml`. See
  [`solutions/integration-issues/buffered-sdk-logs-need-eager-flush-before-reading.md`](solutions/integration-issues/buffered-sdk-logs-need-eager-flush-before-reading.md).
- `MODEL_NAME` controls model attribution; update via terraform tfvars.
- MCP tool unavailable locally: check `.mcp.json` uses `.venv/bin/python`, not
  `python3`.
- `ModuleNotFoundError` in production: systemd/terraform must not override the
  Docker `CMD` -- dependencies live in the venv, not global python. Do not append
  `python3 run.py` to the docker run command in `news-digest.tf`.
- Claude Code intentionally has no temperature/determinism setting
  ([claude-code#3370](https://github.com/anthropics/claude-code/issues/3370)).
  Use the API directly if a pipeline needs determinism.

---

## Superseded measurements

Kept for history. **Do not act on these** -- they describe the pre-Agent-SDK
dispatcher and have been contradicted by later work.

<details>
<summary><strong>MCP tool reliability by model (2026-02-02) -- SUPERSEDED</strong></summary>

Measured against the old thin-dispatcher architecture, which used an MCP
`write_selections` tool. Reported Opus 100%, Haiku 50-75%, Sonnet 0-25% tool-call
success on large contexts, and recommended Opus for production curation.

**Why it no longer applies:** the pipeline moved to Python-orchestrated
file-based subagents via the Agent SDK (`orchestrate.py`). Stages write files;
there is no large-context MCP tool call to fail. Production runs
CLUSTER/SELECT/WRITE/COHERENCE on `claude-sonnet-4-6` and RECAP on
`claude-haiku-4-5` reliably. The "use Opus for curation" recommendation is
stale and would roughly triple cost for no reliability gain.

The mitigation it proposed (an explicit "CRITICAL INSTRUCTION / you MUST call
the tool" block) is still a valid technique for forcing tool invocation in
small models, if that situation ever recurs.

</details>

<details>
<summary><strong>Clustering PoC results (2026-03-19) -- SUPERSEDED</strong></summary>

Compared TF-IDF, MiniLM (sbert), and model2vec against Claude's clustering over
runs 106-108. Best was MiniLM at ARI 0.497, purity 0.892, coverage 0.836,
642 MB RAM. Concluded that automated clustering could not replace editorial
judgment at 50% agreement.

**Superseded by** `docs/2026-06-26-cluster-eval-methodology.md`,
`docs/2026-06-26-cluster-eval-noground-truth-literature.md`, and the
extract-then-join CLUSTER stage shipped 2026-07-02
(`cluster_extractjoin.py`). Critically, the later work established that
Sonnet-vs-Sonnet ARI self-agreement is only 0.60-0.88, so the 0.497 figure was
being compared against a reference whose own reproducibility was never measured.
See [[a-tuned-composite-score-with-no-ground-truth-is-taste]].

The durable finding that survives: CLUSTER performs **editorial narrative
grouping, not deduplication**, so it cannot be cheaply replaced by a similarity
threshold.

</details>

<details>
<summary><strong>Language and convention notes -- moved</strong></summary>

Python 3.14 restored `except A, B:` syntax (catches both; ruff prefers the comma
form at 3.14 target). Rust unit tests live in a `#[cfg(test)]` module at the
bottom of the file they test; `tests/` is for integration tests only; small Rust
apps can stay in `main.rs` until roughly 1000 lines.

These are general language conventions rather than lessons from this codebase.

</details>
