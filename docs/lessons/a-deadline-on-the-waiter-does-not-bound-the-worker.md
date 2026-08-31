# A deadline on the waiter does not bound the worker

*2026-08-31. Run 281 stalled for 62 minutes inside a step with a 120-second deadline,
and shipped nothing until it was killed by hand.*

## What happened

The run completed CLUSTER, RECAP and SELECT normally, logged `[Select complete]` at 10:30:47,
and then went silent while burning a full CPU core. At 11:32 it was still going — no further log
line, no `[Write started]`, no `article_fulltext.json`, `selected.json` still the newest file in
`claude_input/`. `py-spy dump` against the live process, taken twice 23 minutes apart, gave the
same two stacks:

```
Thread 449 (active)
    document_fromstring (lxml/html/__init__.py:740)
    try_readability      (trafilatura/external.py:41)
    compare_extraction   (trafilatura/external.py:96)
    _fetch_one           (fulltext.py:130)

Thread 448 (idle)
    wait                       (concurrent/futures/_base.py:300)
    _fetch_for_selected_inner  (fulltext.py:194)   <- the 120s deadline, still waiting at 62 min
```

One pathological document. Identical stack across both dumps, so it was not grinding through many
articles slowly — it was stuck on one.

## The lesson

`fulltext.py` bounds the step like this:

```python
done, not_done = wait(futures, timeout=config.FULLTEXT_DEADLINE_S)
...
executor.shutdown(wait=False, cancel_futures=True)
```

That deadline governs **how long the main thread waits**, not how long a worker may run.
`cancel_futures=True` cancels tasks that have not STARTED; a task already inside a C extension is
untouched, and `shutdown(wait=False)` simply declines to join it. The code's own comment states the
safety argument, and the argument is wrong:

> Already-running fetches finish on their own (bounded by the per-fetch timeout)

The per-fetch timeout is trafilatura's `DOWNLOAD_TIMEOUT`. **It bounds I/O. Nothing bounds
extraction.** Parsing is CPU work in `lxml`, and the Python-level parts of trafilatura's
readability comparison hold the GIL. On a 2-vCPU box that starves the waiter so thoroughly that it
cannot be scheduled to observe that its own timeout expired an hour ago.

**A timeout on thread A is not a bound on thread B.** It is a bound on A's patience, and A can only
act on it if it gets scheduled. State the invariant as "no worker may run longer than N", and then
ask what actually enforces it.

## What does enforce it

- **A process, not a thread.** `ProcessPoolExecutor` (or an explicit `multiprocessing.Process` with
  `join(timeout)` then `terminate()`) can be killed mid-parse. A thread holding the GIL cannot be
  interrupted from Python at all — there is no safe `Thread.kill`, and `signal.alarm` only ever
  fires on the main thread, which is exactly the thread that is starved here.
- **A hard limit on the input**, before the expensive call: cap bytes fetched, and skip documents
  over a size the parser has been measured to handle. Cheap, and it removes most of the tail.
- **A watchdog that outlives the process.** The systemd unit has a generous start timeout and no
  per-stage watchdog, so nothing outside the run noticed. `run_health` only evaluates a run that
  finished; a run that never finishes is invisible to it.

## Generalising

This is the third instance in this repo of the same shape: **the mechanism that was supposed to
bound a failure could not run in the state the failure produced.**

- A rotating 100 KB log is not somewhere an invariant can be evaluated from — the argument that
  produced `cluster_health.json`.
- `abort_run` deleting a failed run's forensics — the one thing tracing would genuinely have added
  (`docs/2026-08-30-llm-tracing-backend-options.md`).
- And now: a deadline evaluated on a thread the failure starves.

When something is declared best-effort and additive — as this step explicitly is, with WRITE and
COHERENCE designed to work off CSV summaries alone — check that its failure mode is also additive.
**This one could not fail softly, because it never got as far as failing.** It just stopped the
digest.

## Recovering, for next time

`FULLTEXT_ENABLED=false` is the kill switch (`config.py:106`). The recovery that worked:

```bash
systemctl stop news-digest.service
docker run -d --rm --init --name news-digest-run ... \
  -e FULLTEXT_ENABLED=false ... <image> --resume
```

`--resume` skipped CLUSTER, RECAP and SELECT on their valid archived outputs and went straight to
WRITE, so none of the ~$1.28 already spent was paid twice. Copy the `ExecStart` from
`systemctl cat news-digest.service` rather than reconstructing the flags.

Expect a higher coherence failure rate on a fulltext-less run: stories with no full text fail at
**30.0% vs 14.2%** (measured 2026-08-30, n=469). That is the expected consequence of the recovery,
not a new regression.

---

## Corrections from building the fix (2026-08-31, same day)

**1. There are TWO manifestations, and the machine decides which you get.** This lesson said the
waiter "cannot be scheduled to observe its own timeout" — true of run 281, where py-spy caught
thread 448 still inside `wait()` after 62 minutes. But reproducing it on a fast multi-core machine
gives the *other* shape: the deadline fires, logs, and returns on time — and the parse keeps
burning a core afterwards. Measured against the pre-fix module: returned at 2.0s, then **2.99 CPU
seconds in the next 3 wall seconds**, with nothing holding a reference to it.

So the naive assertion — *"the step returns within its deadline"* — **passes against the broken
code**. On a 2-vCPU box the GIL-holding thread also starves the waiter; on 8+ cores it does not.
Same bug, different symptom, and only one of them is visible to the obvious test.

The invariant worth asserting is therefore not *"did it return in time"* but **"when it returned,
was any of its work still running"** — `getrusage(SELF) + getrusage(CHILDREN)` across a window
after the call. Pre-fix 2.99s, post-fix 0.00s.

**2. `DOWNLOAD_TIMEOUT` does not bound the fetch either.** This lesson said it "bounds I/O".
Too generous: urllib3's retry strategy multiplies it, and cbc.ca was measured taking **41 seconds
against a 10-second timeout**, three URLs in a row. Nothing in this path was bounded — not the
download, not the parse. (trafilatura's own `EXTRACTION_TIMEOUT=30` is a red herring: `cli_utils.py`
only, never the library `extract()` path.)

**3. A pool was the wrong shape; `subprocess` is the right one.** `ProcessPoolExecutor` is
disqualified by this lesson's own requirement — killing a running worker raises
`BrokenProcessPool` and fails every *pending* future, so it would poison the remaining articles,
and there is no public API to kill one task. Python 3.14 also defaults `multiprocessing` to
`forkserver` on Linux, which either re-executes `run.py` in every child (368 ms/doc vs 74) or holds
the whole import graph resident (106 MB for the run's lifetime); `fork` is out because CPython 3.12+
warns on forking from a multi-threaded process and this runs inside `asyncio.to_thread`.

A plain `subprocess.run(timeout=...)` has none of that: SIGKILL is available, partial stdout comes
back, and the killed path and the normal path are the same code. **Cost: +0.52s (+1.2%) on a real
43-URL run, zero articles lost.**
