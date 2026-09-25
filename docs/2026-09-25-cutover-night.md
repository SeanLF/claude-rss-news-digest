# Cut-over night, 2026-09-25 (UTC): staged rehearsal, then temporal

Sean's call (2026-09-24 evening, Toronto): a hard replacement tonight instead of three staged gate days.
This is the order that keeps every step reversible until the first TypeScript send at 10:25Z.
Procedures are the runbook's (`2026-09-23-temporal-cutover-runbook.md`); this file only orders them and
adds the STOPs. Commands needing Touch ID (`bin/tf`, `bin/deploy` via 1Password) are Sean's.

**Why staged first, even tonight.** In `temporal` the worker's `BROADCAST_ENABLED` is true
(`news_digest_temporal_live`, news-digest-temporal.tf:28, :706). A rehearsal run there would email
subscribers unless rejected inside its hold. In `staged` broadcast is off by construction and the worker
writes only `digest_staged`, so the rehearsal cannot reach a reader.

**Clean rollback window**: until 10:25Z. Nothing TypeScript has been sent, `digest.db` is only ever read
through a snapshot, and `news_digest_pipeline = "python"` plus the runbook's teardown restores today.
After the first TypeScript send, roll forward: a rollback can double-send and the Rust site cannot show
TypeScript issues.

## 0. Preflight (no prod change)

- [ ] Review gate on seanfloyd.dev `digest-temporal` 6c1fb28..HEAD: findings fixed or accepted.
- [ ] `df -h /` on the Mac > 5 GiB; `bin/ssh 'df -h /; free -m'` on the box.
- [ ] `bin/ssh 'systemctl show -p ActiveState --value news-digest.service'` is `inactive`.
- [ ] Last Python deploy tag noted: `deploy/2026-09-18-053727Z`.
- [ ] Hotfix decision: deploy `fix/fulltext-ssrf` first, or let the cut-over retire the Python fetch.

## 1. Staged (the rehearsal)

```
cd ~/Developer/seanfloyd.dev && git merge --ff-only digest-temporal && bin/tf init
# terraform.tfvars: news_digest_pipeline = "staged"
cd ~/Developer/news-digest && make deploy-dry && bin/deploy -y; echo "deploy exit $?"   # never pipe it
```
Then the runbook's "First staged apply" checks (units active, `RESEND_LIVE=true` in worker.env,
kamal-proxy list, `digest_ro` refuses writes). **STOP** on any failure: teardown to `python`.

`T` is the runbook's `temporal` CLI wrapper ("Staged verification", step 3).

Rehearsal run for 2026-09-25 (no `force`: the copy has no run for this UTC day yet):
```
bin/ssh systemctl restart news-digest-worker        # fresh digest_staged from digest.db
bin/ssh "$T workflow start -t digest --type DigestWorkflow -w digest-staged-2026-09-25 -i '{\"runDate\":\"2026-09-25\"}'"
bin/ssh 'id=$(docker inspect -f {{.Id}} news-digest-worker); while [ -e /sys/fs/cgroup/system.slice/docker-$id.scope ]; do cat /sys/fs/cgroup/system.slice/docker-$id.scope/memory.peak; sleep 10; done'   # the worker's real peak; Ctrl-C when the run ends
```
Pass: the run completes; the "not sent" (broadcast disabled) email reaches Sean (proves Resend on the box);
the pre-send checks' failures, if any, are ones a live run could hold on; worker peak < 1280 MiB and
`free -m` never below ~200 MiB available. **STOP** otherwise: teardown to `python`; Python runs at 10:25Z.

## 2. Temporal (the switch)

```
# terraform.tfvars: news_digest_pipeline = "temporal"
#                   news_digest_hold_always_through = "2026-10-01"
cd ~/Developer/news-digest && make deploy-dry && bin/deploy -y; echo "deploy exit $?"
bin/ssh docker exec news-digest-worker node dist/cli/backfill-markdown.js
```
Then the runbook's "Cut-over" step 3 checks: timer disabled, schedule unpaused, `BROADCAST_ENABLED=true`,
`HOLD_ALWAYS_THROUGH=2026-10-01`, "digest imported", every check ok. Plus:
- [ ] `curl -s https://news-digest.seanfloyd.dev/health` healthy; `/issues/2026-09-24` and its `.md` serve.
- [ ] `bin/ssh docker exec news-digest-worker node dist/cli/check-injections.js` (or the make target
      against prod): 0 misses.
- [ ] Commit `bin/lib/prod-store` = `postgres` (runbook "Scripts still on SQLite").
**STOP** on any failure before 10:25Z: rollback (runbook "Rollback") to `python`.

## 3. 10:25Z (06:25 Toronto)

The first run holds 15 minutes (the cut-over hold): approve or reject from the `[Hold]` email. Unanswered,
it sends when the hold ends. Afterwards: the runbook's "Cut-over" step 4, healthchecks.io's ping (its
schedule and grace are unchecked: a false alarm is possible), and the issue on the site.
