# Thread re-linking: the label-drift recall bug + fix (2026-06-29)

Investigation triggered by a question while reviewing the live-e2e digest: why does today's
US-Iran/Hormuz story badge "Ongoing · day 4" when the Iran saga has run for weeks?

## The benign part: thread granularity is correct

"Day N" is the thread's **installment count**, not the story's calendar age. The linker
deliberately splits a big topic into distinct ongoing storylines (the design goal): the Iran
family is ~16 separate threads — Lebanon ceasefire, Hormuz shipping, nuclear diplomacy, military
strikes, the MoU — each internally coherent. Lumping them into one "Iran" thread would produce
grab-bag deltas; the split is right. (Confirmed: every multi-day Iran thread's installments stay
on one storyline.)

## The real bug: label-drift re-linking

Some storylines get a **fresh thread_id mid-stream** — the same story, two threads, day-count
reset. Measured re-threadings in the 204-215 backfill:

| storyline | split into | should be |
|---|---|---|
| Israel-Lebanon ceasefire | t200 (day 8) + t309 (day 3) | one thread, day ~11 |
| UK Labour leadership | t216 (day 7) + t287 (day 3) | one thread, day ~10 |
| US-Iran nuclear diplomacy | t218 + t277 | one thread |

**Root cause (measured, not guessed):** every prior thread was still *active* when the storyline
re-threaded (run gap < `THREAD_DORMANT_AFTER=3`), so dormancy is NOT the cause — raising the
dormancy window would not help. The cause is that the linker saw only each thread's **latest
label**, which drifts to a narrow facet ("Starmer resigns", "Israeli troops kill two in south
Lebanon"). When the storyline's next installment arrives ("Burnham to become PM",
"Israel-Lebanon ceasefire deal signed"), the latest label no longer recognizes it → spawns a new
thread.

A/B on the three failure points, latest-label-only vs showing the thread's recent arc, 3 samples
each (linker is stochastic — no temp=0 on the Agent SDK, so single draws are noisy):

| case | latest-only links correctly | recent-arc links correctly |
|---|---|---|
| UK (Starmer→Burnham) | **0/3** | **3/3** |
| Lebanon | 1/3 | **3/3** |
| nuclear (label hadn't drifted yet) | 3/3 | 3/3 (neutral) |

## The fix

Show the linker each active thread's **recent story arc** (last `RECENT_LABELS_K=4` installment
labels, oldest→newest) instead of only the latest label. `newsroom/src/threads.py`:
- `ActiveThread.recent_labels` new field; `link_threads` renders it arrow-joined
  ("earlier → … → latest"); `LINK_SYSTEM` tells the linker to judge against the whole arc.
- `ThreadStore.active_threads` populates it via a new windowed-SQL helper `_recent_labels`
  (one query, `ROW_NUMBER() OVER (PARTITION BY thread_id ...)`), fallback `[label]` for a
  brand-new thread.
- Fully backward-compatible: an `ActiveThread` with no `recent_labels` falls back to `label`.

## Validation at scale (full re-backfill with the fix)

| | before fix | after fix |
|---|---|---|
| Lebanon | day 8 + day 3 (split) | **one thread, day 10** |
| UK leadership | day 7 + day 3 (split) | **one thread, day 8** |
| Russia-Ukraine refineries | day 5 + day 6 (split) | **one thread, day 9** |
| US-Iran nuclear | t218 + t277 (split) | **one thread, day 7** |
| multi-day threads | 25 | 21 (fragments merged) |
| **over-merges** | — | **0 across all 21 multi-day threads** |

Every consolidated thread is coherent (each installment is genuinely the same storyline; the
old fracture points are now bridged), deltas stay grounded, day-counts now reflect true
storyline length. 435 thread/unit tests pass. The re-backfilled DB is the improved launch seed.

## Other tuning levers (not needed, noted)

- `THREAD_DORMANT_AFTER` (env): would only help *genuine* multi-run gaps; the measured bug was
  recall, not dormancy, so this was a red herring here. Still useful if real gaps appear in prod.
- `RECENT_LABELS_K` (constant, =4): how much arc the linker sees. 4 was sufficient; cost is a few
  extra tokens in one Haiku call/run (negligible).
