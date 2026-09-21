# Four systems: design for the TypeScript-on-Temporal rewrite

**Date:** 2026-09-21
**Status:** draft for Sean's review; decisions marked *owed* carry a default that holds until he rules.
**Companion:** the design brief (claude.ai artifact "Four Systems Plan", revision 15) holds the evidence
tables this spec cites; `docs/proposed/coherence-planted/io-shape-2026-09-21/` holds the last measurement.

## 0. Goal, non-goals, and the shape of the decision

**Goal (Sean, 2026-09-21):** legibility and architecture first. One rewrite, in TypeScript, with Temporal as
the durable-execution layer, judged on the final digest rather than on parity with today's stages.

**Non-goals:** multi-tenancy (a second vertical is a second instance with its own sources and prompts);
edge or serverless hosting (ingestion is under a minute of a 20-minute run; the pipeline needs the CLI
binary and the subscription login on disk); microservices per module (one caller, one schedule, ten
sequential stages idle 23 h 40 min a day).

**How this was decided, honestly:** the 2026-09-17 spike pre-registered a rule under which a platform wins
only after the restructure-in-place arm is built and measured. Only the fits-the-box clause was measured
(262 MiB idle for server + Postgres, a four-activity workflow in 0.61 s, box left clean). The owner chose
the platform on legibility, human-in-the-loop, schedules, and TypeScript, which the rule did not weigh.
This is an owner decision, recorded as such.

**Evidence the decision rests on** (all in the brief): 3 of 13 commits in the 2026-09-18 deploy were
retry/deadline fixes at the seam Temporal turns into a declared policy; Probe 0 found two inputs (the
run date, the shared deadline) that a platform forces you to declare; Kestra needs 4 GiB, the whole box;
both Agent SDKs drive the same bundled CLI on the subscription (TS Max-usage issue closed 2026-03-31);
churn since June concentrates in orchestration and the product surface, not in ingest or the judges.

## 1. Frozen contracts (the only things carried verbatim)

These do not change during the rewrite. Each is a test in the new repo before any stage is built.

| Contract | Statement | Backing today |
|---|---|---|
| Article ids | Every article gets an opaque id `A{n}` per run; model stages see ids, never URLs. | `no_internal_article_ids` regression gate; `resolve_article_ids` |
| Provenance to Claude | No URL reaches any model stage. Source id, bias and factuality **do** (sources.csv). State the true invariant. | Probe 0 §A; articles CSV header |
| Output schema | must_know / should_know stories with headline, summary, why_it_matters (must_know only), sources[{article_id, angle, bias}], reporting_varies; preheader ≤ 157 chars. `cluster_index` is dropped from the contract (drifted 14%; citations resolve the cluster). | `schema.SELECTIONS_SCHEMA` |
| Coherence verdict | One result per story: headline, article_ids, pass, reason, failed_fields ⊆ {headline, summary, why_it_matters}, failure_kinds ∈ {contradicted, unsupported}. Shape only, never count. | `schema.COHERENCE_REPORT_SCHEMA` (shape-only, tested) |
| Fail-closed | A story whose field fails coherence is repaired or dropped, never shipped unchecked. | `merge.assemble_selections`, `validate_coherence` |
| Parsing boundary | Code parses a model artifact for four reasons only: the id→URL map, routing (fan-out, join), control flow on verdicts, persistence (dedup headlines, thread ids). Everything else is opaque text. | Brief, "parsing boundary" |
| Run date | One UTC-stamped day per run; the date is a workflow input read once. | `orchestrate._utc_today`; Probe 0 §B |
| DB tables | `digest_runs`, `shown_narratives`, `source_health`, `digests`, `run_usage`, `run_artifacts`, threads tables; migrations forward-only. | `migrations/` |
| Prompts | The seven agent prompts carry over as text. Their measured bands do **not** transfer to a new runner. | `.claude/agents/*.md` |

## 2. Pipeline

**Job.** Daily: 36 active feeds → ~600 kept → cluster, recap, select, write ×stories, preheader,
coherence, repair, recheck, threads → render (static archive + MJML email) → broadcast → archive.

**Owner's NFRs (Sean, 2026-09-21).** Not mission-critical; a failed day is a non-urgent incident (investigate,
fix, re-run or resume, post-mortem). A thin run (many feeds down) ships and logs; abort only on zero stories.
One UTC day per run. Retention grows: the artifacts are the analytics and the replay corpus.

**Measured.** ~20 min wall clock (sd 4.2 over 30 runs); ~$5.2 API-equivalent per run (sd $0.85); model time
is ~19 of the 20 minutes; RSS 12 s, fulltext 44 s.

### 2.1 Workflow and activities

- One `DigestWorkflow(run_date)` per day, started by a Temporal Schedule (10:25Z; overlap: skip; catch-up: one).
  The schedule replaces the systemd timer, the dup-run guard, and reboot catch-up.
- Every model call and every network fetch is an **activity**. The workflow is deterministic: sequencing,
  timers, signals. Replay never re-runs a model.
- **Artifacts are blobs, not files.** An activity persists its output in SQLite (`run_artifacts`) and returns a
  pointer `(run, stage, hash)`. The local file is only the SDK's Read surface, materialised into a temp
  directory for the model and discarded. Largest artifact today ~270 KB; the 2 MB payload cap is not the
  reason for pointers, the evals are.
- **Idempotent on output.** An activity that finds a valid artifact for its pointer returns it. "Resume run N at
  WRITE" is the same workflow started with run N as input. History is for visibility and forensics; the
  artifacts are the record. History retention: 30 days.
- **Per-feed and per-link fetches are activities with their own retry policy.** gnews decoding runs at
  publish, over survivors only (run 303: 26 decodes for 10 links used), in parallel with the threads stages,
  best-effort under the existing deadline; its constraint is a per-IP daily budget, not a connection.
- **Retry semantics, per activity.** Model calls retry within an outage-sized budget (Claude status data:
  median ~1 h, worst ~3 h). Validation verdicts and the broadcast send are `maximum_attempts = 1`: a verdict
  is a result, a send is at-most-once (the 2026-06-16 draft-reuse rule carries over). Workflow run timeout
  under the box's ceiling. Every timeout carries its reason in this document, not a number alone.
- **Deploys never overlap the run window** (schedule paused during deploy). `patched()` is the documented
  exception path, not routine.

### 2.2 Stage I/O shape (measured 2026-09-21, planted278, 13 reps)

- **Write goes.** Every stage returns its result as the final message; the activity validates and persists it.
  Measured: no rep needed a retry; structured output arrived every time; the shipped loop wrote once.
- **Text in, text out** for generation and positive extraction: RECAP, PREHEADER, CLUSTER extract; SELECT to be
  measured.
- **Checker default = corpus inline in the user turn + Grep and Read available**, with the rule "no FAIL without
  a Grep behind it" checked in the transcript, not asked for in prose. Measured: recall 8/8 and 0/24 false drops
  in 3/3 reps (band never below 1); it never drops the two adjudication fields every other shape drops. The rule
  is followed for some fails only (4–7 of 9 unbacked per rep), so the transcript check is what keeps the number
  honest. Cost ~$1 cold for every shape; warm-rep numbers are cache reuse and are not quoted.
- **Schema-constrained final message where code parses the artifact** (coherence, select, write); the schema
  constrains shape, never count (2026-08-21).
- **Failure tiers.** (1) Shape or encoding error → one fix-it turn in the same session carrying the validator's
  message (untried today; cheap; the same shape as an error tool result). (2) Still failing, or a content
  problem (citation to a missing id) → a fresh sample under the activity's bounded retry policy. (3) A verdict
  is never retried. A fix-it turn is for encoding only: asking a model that satisficed to "fix" content yields
  a patched output that passes the check and is worse.
- **Tool scoping.** Read and Grep on the input directory only; no shell, no network, no Write. This is the
  prompt-injection posture: publisher text reaches a model with nothing to mutate and nothing to exfiltrate to.
- All of the above is **re-measured on the new runner before it ships**; no band transfers.

### 2.3 Human in the loop (three signals)

1. **Pre-broadcast hold**, 2 h then proceed (availability stance is non-urgent). Catches bad content sent
   (the run-247 id leak class), not nothing-sent (that is the dead-man's switch plus explicit timeouts).
2. **Retries exhausted**: the workflow parks on a signal offering "retry more" or "abort".
3. **Operator note**: a signal payload injected into the next attempt's prompt as an operator section, and
   persisted as an input artifact because it changes the run.

*Owed:* the channel for reaching Sean. Default: Temporal UI over Tailscale plus a signed link by email.

## 3. Web tier

**Job.** Static archive (issues, archive, threads, stats, sources, feed) rendered by the pipeline; a server for
what is dynamic: search, ask (OpenRouter, 2 legs deployed, cap 3), translate, MCP, subscribe/confirm.

**Owner's NFRs.** Readers survive any pipeline failure. Secrets matter most. No PII in the database; the one
exception is five journal lines logging email beside IP on subscribe/confirm, which the rewrite drops.

**Measured.** Rust today: 15.9k lines incl. 3.9k HTML-in-Rust and ~5.4k inline tests; 3.8 MiB RSS; 0% CPU.
Footprint decides nothing (2.8 GB free); one toolchain is the gain. TypeScript rewrite ~5k lines, sequenced after
the pipeline.

**Requirements.**
- Security headers as seanfloyd.dev sends them on the same box (CSP with a nonce for the injected digest CSS and
  the /ask script, HSTS preload, permissions-policy, referrer-policy, nosniff, frame-options). Confirmed absent
  today.
- Monitoring: UptimeRobot imported into terraform (official provider) with a second monitor for the digest site.
  Confirmed absent today: restart=always and the kamal-proxy check at deploy swap only.
- Contracts other clients read keep parity with today: the feed, the MCP tools, the JSON bridge. Everything else
  is judged on its requirement plus a11y and Lighthouse checks, not on parity with the Rust HTML.
- Subscriber lifecycle stays delegated to Resend Marketing (audience broadcasts; 1,000 contacts on the free tier,
  sends unlimited). The contact count is logged each send; a threshold line near 900 is added. The opt-in
  misconfiguration path (secret missing → contact added anyway) becomes a hard failure.

## 4. Eval system

**Owner's NFRs.** Metrics are Claude-defined today and the golden sets are not trusted. Band before metric.
Every harness must be able to say "keep what we have". Instruments are negative-controlled.

**Measured.** 15 modules, 11 scripts; ten of fifteen decorrelated from the pipeline; the five that couple do so
through one primitive: run a stage on an input directory with a model and thinking config.

**Requirements.**
- The stage-runner primitive is an activity with a CLI entry; evals call it, never the workflow, in any language.
- promptfoo (already in the repo for /ask) is the runner; the band-and-planted-fixture protocol is expressed on top
  of it: N runs of the same case, self-agreement measured before any metric, planted defects as ground truth.
- The five "restore a run" reimplementations collapse onto the activity runner plus `replay`.
- Python stays where a tool needs it (trafilatura for fulltext until a TypeScript extractor is measured on our
  selected articles). The Python evals, fixtures and tests stay until the TypeScript harness reproduces each on
  the same fixture; they are deleted per harness, never wholesale.
- `read-schema`-style constrained arms cannot produce the `malformed` signal; the write-up says so.

## 5. Infrastructure and operations

**Owner's NFRs.** Backups at each deploy and daily are sufficient. Terraform owns everything operational.

**Requirements.**
- Terraform owns: the Temporal server (version pinned; current 1.32.0), its Postgres with a nightly `pg_dump`
  (not a volume copy) and one restore drill, the worker unit, the schedule bootstrap, UptimeRobot monitors, the
  healthchecks ping, DNS, secrets via env-file for every container (circulation's are on the docker command line
  today). Rule: terraform owns what runs; code owns what the run does. Schedules and retry policies are code.
- One Temporal server, one namespace per repo. Nothing on it is mission-critical.
- The deploy script shrinks to build, push, apply, smoke. What stays bash is decided in plan 4: the SBOM gate,
  snapshot-as-rollback, provenance check.
- SQLite stays, WAL on, busy timeout on both sides; the web tier's reader has neither today.
- Disk growth gets a monitor; retention is a decision, not an accident.

## 6. Seams

- **SQLite**: pipeline (writer) and web (reader) share one file; locking is declared on both sides.
- **Subscriber flow**: circulation → Resend → newsroom; delegated, with the threshold line as the one instrument.
- **Email ↔ web**: two renderers, one data source; the rewrite adds the parity test the current tests say is missing.
- **Provenance**: no URL reaches Claude; source identity does. Stated once, tested once.
- **Retention**: grow by decision; disk is the instrument.

## 7. The gate

Inputs: archived input days from run 300 onward (four eligible today, one more per day). Gate on ≥ 3.

1. **Planted defects** (fabrications, strays) for recall: ground truth by construction, unbiasable by any family.
2. **Two whole-digest judges from different families** with the rubric below: Opus or Fable (owner's pick) and,
   for now, Codex CLI or Gemini CLI on their own subscriptions; OpenRouter as fallback. Each judge's
   self-agreement band is measured on the same digest five times before any score counts. Disagreements go to
   Sean: the only human label in the loop, spent where it matters.
3. **Not gates**: the L1 caps (calibrated to shipped output), the why-judge golden (45 cases, provenance
   unclear), per-stage artifact diffs. All stay as diagnostics.
4. **Speed and cost**: paired on the same input day. First measure the old system's same-day band by replaying one
   closed day (never done). Then the new system, n ≥ 3, must sit within the old distribution (mean $5.18 sd $0.85;
   20.1 min sd 4.2). A band test, not a point comparison.
5. **Cut-over** when the gate passes on three days: schedule moves to Temporal, the Rust server retires when the
   static archive and dynamic routes serve, the Python pipeline tree is deleted; Python evals stay per §4.

### 7.1 Draft rubric for the whole-digest judge (owed: Sean edits)

Scored per story and per digest, each criterion pass/fail with a one-line reason; the judge sees the digest and
the day's article CSVs, never URLs.

1. **Supported.** Every specific in headline, summary and why_it_matters (number, date, name, place, quote,
   quantifier) appears in that story's cited sources. Absence is a fail; paraphrase and compression are not.
2. **Bound correctly.** Each specific is attached to the entity and predicate the sources give it.
3. **Not stale.** Office-holders, administrations and world state match the cited articles and the run date.
4. **Earns its slot.** why_it_matters adds a mechanism, contradiction or consequence the summary does not already
   say; filler fails.
5. **One event per story.** A headline or summary that bolts a second event on fails.
6. **Selection.** The must_know set is the day's most consequential stories given the inputs; a story a reader
   of the inputs would expect and does not find is named.
7. **Reads clean.** No internal ids, no template tokens, no truncation; preheader within its cap.

## 8. Order and fan-out

1. Spec reviewed by Sean (this document). Plan written with `writing-plans`, in the ask-module shape: target tree,
   interfaces, invariant ledger (every deleted comment or narrative doc becomes a test seen red first), rename
   commits separate from extraction commits, CI green per commit, one sized review per commit.
2. Archive the recheck outputs (known gap A2) and measure the recheck band with the existing harness. Small,
   additive, keeps prod honest during the rewrite.
3. Measure the old system's same-day band by replaying one closed day. Gates §7.4.
4. **Spine, serial, one owner**: TS scaffold and CI, the frozen contracts as tests, the workflow file with the
   three signals, Temporal in terraform, the activity-runner CLI, the gate harness (planted defects, two judges,
   bands).
5. **Fan-out, one agent per unit, roughly two thirds of the volume**: activities from requirements (feeds, dedup,
   cluster extract-join, recap, select, fulltext, gnews, write fan-out, preheader, coherence, repair + recheck,
   threads + synthesis, render, email, broadcast, health invariants); web routes; terraform units independent of
   Temporal; eval harnesses on promptfoo. Oracle per unit: the stable tier's tests and measured findings for
   ingest and dedup; the gate for everything downstream of CLUSTER. Sonnet for mechanical work, Opus for the
   spine and every adversarial review; every agent definition pins a model.
6. Cut-over on three passed days.
7. Public-history rewrite (two files in fdd958d carry pilot names and pricing), manifest first, after the rewrite.

Implementation runs in fresh sessions (planning and building in one session measured ~3× less efficient).

## 9. Decisions owed by Sean, with the default that holds meanwhile

| Decision | Default |
|---|---|
| Adjudicate stories 9 and 4 `why_it_matters` on planted278 | Labels stand; the inline-grep arm is recorded as disagreeing with them 3/3 |
| Channel for the three HitL signals | Temporal UI over Tailscale + signed link by email |
| Judge rubric (§7.1) | The draft above |
| What stays bash in the deploy | SBOM gate, snapshot-as-rollback, provenance check |
