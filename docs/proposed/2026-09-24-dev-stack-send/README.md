# Dev stack: a TypeScript run through the send, and the site that serves it (2026-09-24)

One dev stack (`make dev-import`, then `make digest-start`): the worker, the TypeScript site and
resend-fake over one Postgres `digest` imported from `data/prod-20260923b.db`. Checked by hand, not
by a harness. Nothing was delivered: resend-fake stands in for Resend.

| run | threads | what happened | model calls |
|---|---|---|---|
| 306 (first) | off (the dev default then; now on, ae43524) | hold → approve → broadcast to 1 confirmed reader; issue 2026-09-24 served by the site; 5 must-know, 12 should-know | 39, $4.22 |
| 307 | off | started a minute after 306, fetched almost nothing, refused an empty digest (`EmptyDigest`); no issue, no send. Its alert wrongly said "sent": fixed in 53a0d66 | 6 |
| 306 (after a re-import) | on | hold → approve → broadcast; 17 thread updates, 5 continuations, all visible once published; `ONGOING · DAY n ↗` badges on the issue page; `ZERO_RECIPIENTS` alert fired because the re-import had emptied resend-fake's audience (fixed: the fake keeps its state, 0977665) | 51, $4.72 |

Also checked: a subscribe through the site, its confirmation email in resend-fake, the confirm link,
and the contact surviving a restart of the fake.

Reproduce: `make dev-import && make dev-up`, subscribe at the site, confirm from the email at
`http://resend-fake.news-digest.orb.local:8025`, then `make digest-start` and `make digest-approve`
if it holds. Queries used: `runs`, `issues`, `sends`, `thread_updates`, `thread_state` and
`model_calls` for the run id.

Not covered: a real Resend send; any of it on the box. These ran before the conditional hold
(5919787): each held 2 h and was approved by hand.
