# Thread-as-entity PoC: does an entity key make run 235 unrepresentable?

*2026-08-26. Replayed against real archived run-235 artifacts. Code in
`scratch/thread-entity-poc/`.*

## The question

Run 235 shipped two identical top cards because `story` — a non-unique modal label —
was used as a unique identity key by three consumers. Fixed 2026-07-17 with two
guard layers. The open question: would modelling a thread as an **entity** (keyed
object) make the collapse *structurally impossible* rather than guarded against?

## Method

`replay.py` runs four parts against the real `run_artifacts` rows for run 235
(`clusters.json` 38 KB, `selections.json` 34 KB) and `thread_installments` for runs
230–245. Part A is a **negative control**: if the harness cannot reproduce a known
bug, nothing after it means anything.

## A — the harness reproduces the defect ✓

```
thread_installments rows for run 235: 19
distinct keys after keying on cluster_story: 18   <-- one row lost to collision

card[0] thread=284  US-Iran war enters fifth day...      summary -> DELTA-FOR-THREAD-284
card[1] thread=284  Iran signals diplomatic opening...   summary -> DELTA-FOR-THREAD-284

>> two top cards share a summary: True
```

Nineteen rows collapse to eighteen keys in a single dict comprehension. Confirmed on
production data.

## B — the linker was right, and render threw the answer away

```
thread   6  CONTINUED existing thread   label='US-Iran military strikes escalation July 2026'
thread 284  created NEW thread          label='US-Iran military strikes escalation July 2026'
```

**This is the finding that reframes the bug.** The thread layer assigned two distinct
ids to the two colliding clusters. It was correct. The render layer then discarded that
answer and re-derived identity from the label.

So run 235 is not a missing-key problem. It is a **layering violation**: the thread
layer owns thread identity, and `attach_thread_context` reached around it and recomputed
identity from a lossy derivative. The postmortem called `story` "a lossy derivative" of
the join decision; the same sentence applies one layer up.

## C — the entity model removes the collapse point

Modelled as a Restate Virtual Object keyed by thread id, fed the real installments for
threads 6 and 284:

```
thread   6  installments=21  delta=delta-r236-t6
         arc=['US-Iran military strikes escalation July 2026', 'US military strikes on Iran']
thread 284  installments=3   delta=delta-r235-t284
         arc=['US-Iran military strikes escalation July 2026', ...]

do the two threads yield the same delta? False
both carried the SAME label in run 235?  True
```

Identical labels, distinct objects, distinct state. **There is no handler that takes a
label and returns a thread**, so `by_story` has no analogue. The collapse point is not
guarded — it does not exist.

## D — a content-derived key is NOT the answer, and neither is the label

Thread 6 is one continuous story across runs 230–245. Measured, per consecutive day:

| | stayed the same |
|---|---|
| `story` label | **0 / 14** |
| content key (sha256 of sorted article_ids) | **0 / 14** |

Every single day, both change. Sample labels for the *same* thread: *"US-Iran tensions
Trump threats and ceasefire breakdown"* (230) → *"US-Iran military exchange and Strait
of Hormuz conflict"* (232) → *"US-Iran conflict and Oman-mediated Strait of Hormuz
talks"* (245).

So the label fails at **both** jobs: not unique within a run (part A), not stable across
runs (here). And a content hash fixes only the first.

**The two jobs are different and must not share a key:**

- **identity** — unique within a run. Content-derived works. This is what run 235 broke.
- **continuity** — stable across runs. Neither a label nor a content hash can do this,
  because the article set turns over completely every day. This is the **linker's** job
  and it already works — `ActiveThread` matches on the *arc*, not the latest label.

## Verdict

**The entity model does make run 235 unrepresentable**, and it is a real improvement over
a guard: a guard can be forgotten at the next call site, a missing handler cannot.

**But it is not why you would adopt Restate or Temporal.** The cheapest correct fix is to
carry the `thread_id` the linker already assigned through to render, and delete the
by-label lookup entirely. That needs no new runtime — it needs the render layer to stop
recomputing something the layer below it already decided. The entity model is the same
discipline, enforced by construction rather than by convention.

Framed in the layering rule: today `digest.attach_thread_context` depends on
`threads`' *output format* (a label string) instead of its *identity*. That is the
coupling to remove. Whether the thing on the other side is a dict keyed on `thread_id`
or a Virtual Object keyed on `thread_id` is a runtime choice, not an architecture one.

## Restate gotchas found while building this

- A handler declared with **no request parameter** takes no body **and no
  `Content-Type` header**. Any Content-Type — including `application/json` — returns
  400. `urllib` always sets one when `data` is non-None, so this needs `http.client`.
- `ctx.key()` returns a **string**, not the type you keyed with.
- Virtual Object state persists across driver reruns, which is the point, but makes a
  naive test script accumulate state between attempts. Use `clear_all` or fresh keys.

## Next

If pursued: the interesting remaining question is not identity but **dormancy and
merge**. `decay_threads` overwrites status in place and `touch_thread` overwrites the
label — per `an-audit-record-of-the-verdict-cannot-audit-the-verdict`, that erases the
evidence a mis-link would leave. An entity with retained state and an append-only arc
keeps it. That is worth measuring against the 14 refused detachments.
