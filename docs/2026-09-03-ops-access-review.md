# Reading prod without cloning it: why the ops surface is a CLI, not Tailscale-only routes

*2026-09-03. Question raised by Sean: the previous session listed a read-only ops surface
(`get_run`, `run_usage`, `get_artifact`, `journal`, `source_health`, `run_health`) as the tool
gap that cost it the most time, and asked whether some circulation routes could be
Tailscale-only, and what the risk would be of exposing that on the digest.*

## Verdict

**Do not add ops routes to circulation, in any form. Ship a CLI over the SSH channel that
already exists.** Built as `bin/ops` (`newsroom/tests/test_ops.py`). The measured pain was
never the absence of a channel — SSH over Tailscale has been there all along — it was the
absence of shaped queries, which is why the last review cloned a 176 MB database twice.

## The finding that decided it: circulation cannot see a client address

circulation runs as `digest-circulation-<sha>-<ts>` on the internal `kamal` Docker network,
exposing `8080/tcp` to that network only — no published host port. `kamal-proxy` v0.9.2 holds
`0.0.0.0:80` and `:443` and routes to it by Host. Verified on the box.

So every request reaching circulation arrives from kamal-proxy. The peer address is the
proxy's. **A "Tailscale-only route" inside circulation could only mean trusting
`X-Forwarded-For`** — an authorisation boundary made of a header the caller writes.

`mcp.rs:829`'s `client_key` takes the **rightmost** hop (`split(',').next_back()`), which is
the correct choice when exactly one trusted proxy appends the real address. That makes the
existing rate limiter sound. *Unverified:* I did not empirically confirm that kamal-proxy
appends rather than replaces — Go's `httputil.ReverseProxy` appends by default, and the
rightmost parse assumes it, but nothing here tests it.

Even granting it, rate limiting and authorisation are not the same bar. **The consequence of
being wrong is asymmetric.** A limiter that mis-attributes an address costs some requests. An
authorisation gate that mis-attributes one exposes thinking traces, coherence reports, and
logs. "Good enough for the limiter" is not an argument for the gate.

## The second structural problem: four doors, one router

The MCP surface is deliberately four entry points onto one tool table — `POST /mcp`,
`GET /mcp`, the `.well-known` card, and `GET /mcp/tools/{name}.json`. A filter that hides ops
tools has to hold at all four, and the GET bridge takes arguments from the query string, which
is the easiest of them to get subtly wrong. `disable_allowed_hosts()` is on deliberately
(public endpoint), so the Host header cannot separate them either.

## What exposure would actually mean, by tool

- **`run_usage`, `source_health` — already public.** `get_stats` serves per-source fetch health
  and per-run articles-kept and AI cost today. No new exposure.
- **`get_run`, `run_health` — new, arguably on-brand.** Invariant results and drop counts;
  transparency is the product's differentiator.
- **`get_artifact` — no.** Thinking traces, coherence reports and draft selections: the raw
  model reasoning plus every story flagged as fabricated and dropped before publication. Not a
  breach; it is unpublished editorial, and a very quotable one.
- **`journal` — no.** A grep-able journal is an unbounded read. These logs carry email
  addresses: test sends (`broadcast.py:179`, `:225`) and alert recipients (`:274`). **Not** the
  subscriber list — the production broadcast logs only a count and an audience id (`:150`), and
  the list lives in Resend. Bounded, but not public.

## The option that was not needed

A second axum listener bound to the tailnet address (`100.98.97.17`) would be a real boundary —
the kernel, not a header — and the box already has the pattern (`registry` is published on
`127.0.0.1:5000`). It was rejected on cost, not soundness: a second container to deploy and
clean up, a bind address that fails closed but could take the site down if it shared the public
container, tailnet ACLs to reason about, and a code path keeping ops routes off the public
router. All of that to reach data that `bin/ssh` already reaches.

**Revisit it if** something without shell access needs these reads — a dashboard, a scheduled
job off-box, or a hosted agent. Nothing does today: every session here has Bash.

## What was built

`bin/ops`, a standalone Python CLI following the existing `bin/` convention:

```
bin/ops run|usage|health|artifacts [ID]      # ID defaults to the latest run
bin/ops artifact ID NAME                     # one archived artifact to stdout
bin/ops journal [--since 6h] [--lines 200] [--grep PAT]
bin/ops <any> --print-command                # show what would run, run nothing
```

It resolves the `digest-newsroom` image on the box (no pinned registry host), runs the query in
a container with the data volume mounted, and prints JSON. The run id and artifact name are
bound parameters passed through the environment; they never enter SQL text, and
`build_payload` returns identical text whatever they hold.

### The read-only guarantee, negative-controlled on prod

Two independent layers, both **tested against the live database, not assumed**:

```
MOUNT: read-only OK (OSError)                        # -v news-digest-data:/d:ro
SQLITE: refused OK (attempt to write a readonly database)   # file:...?mode=ro
READS still work: 274
```

The mount probe ran first, which is what made the SQLite write probe safe to attempt at all.
Nothing was written. A test asserts both layers and that no payload carries a write verb.

The live run also found a real bug the unit tests could not: `journalctl` rejects `--since 6h`
("Failed to parse timestamp") because systemd wants a sign on a relative time. Fixed, with the
test written first.

### The tests were blind, and the review proved it

Adversarial review could not break the quoting (it ran the generated command strings through a
real shell with stubbed `docker`/`journalctl` and never landed an injection) or the read-only
layers. It broke the **tests** instead, and both defects were real:

- `test_remote_command_never_writes_to_the_volume...` asserted only `":rw" not in cmd`.
  Docker's default with no suffix is read-write, so deleting the `:ro` suffix — a one-token
  diff — left the test passing.
- `test_artifact_name_is_bound_not_interpolated` inspected `build_payload`'s text, and
  `build_payload`'s `run_id`/`name` parameters were never used in its body. The real value
  travels by environment variable into the generated script. A version of that script which
  concatenated `OPS_NAME` into SQL passed every assertion.

Both are now positive assertions, and the injection test executes the payload against a
scratch database. Each was then **negative-controlled**: with `:ro` deleted, and again with
the parameter binding replaced by string concatenation, the relevant test fails and only that
test fails. A first attempt at those controls silently applied no edit at all and reported 21
passing — the harness has to be broken on purpose before a green run means anything.

Two smaller findings, also fixed: `_relative_time` matched on the stripped value but
substituted the original, so `" 6h"` became `"- 6h"`; and an extra positional argument was
silently discarded, hiding an operator typo behind a result that looks right.

## What this does not change

`bin/ops` grants the operator nothing they did not already have — it is the same SSH channel,
with the queries written down. That is exactly why `journal --grep` is acceptable here and
would not be over HTTP.
