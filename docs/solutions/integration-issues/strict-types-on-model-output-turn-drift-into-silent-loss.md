---
title: A strict type check on model output turns formatting drift into silent data loss
date: 2026-07-25
category: integration-issues
module: threads, orchestrate
problem_type: integration
severity: high
applies_when:
  - Validating a field parsed out of an LLM's JSON response
  - A model-driven stage returns "nothing matched" and you cannot tell whether that is real
  - Writing a few-shot output example that mixes types in one field
tags: [llm, json, validation, haiku, silent-failure, prompt-design, thread-linker]
---

The thread linker asks Haiku to map today's stories onto active threads and
returns `{"links": [{"story": 0, "thread": 3}, ...]}`. Validation was:

```python
if isinstance(si, int) and 0 <= si < len(today_labels) and isinstance(tid, int) and tid in valid_ids:
```

On run 244 (2026-07-25) the model answered **correctly** — it matched the
Fedorov story to thread 261, the Kyiv strike to thread 12, and three more — but
wrote every id as a JSON **string**: `{"thread": "261"}`. `isinstance(tid, int)`
rejected all 16 links, so the digest shipped with `0 continued, 16 new`: every
"Ongoing · day N" badge vanished, and five ongoing stories were re-opened as
duplicate threads, one of them a 25-day arc carrying 106 open questions.

Nothing errored. No retry fired. The only trace was an INFO line reading
`0 continued, 16 new`, which is exactly what a genuinely quiet news day looks
like.

## The prompt taught the drift

The output example mixed types for one field:

```
{"links": [{"story": 0, "thread": 3}, {"story": 1, "thread": "NEW"}]}
             int ^                       str ^
```

Asked to be consistent, the model normalised to strings. This had been a
coin-flip since the linker shipped; run 244 is simply the first time it flipped
all the way. **If a few-shot example shows two types in one field, expect the
model to pick either one for all of them.** Use a single type, and `null` rather
than a sentinel string, when the field is "id or nothing".

## Three rules that fall out

**Coerce, don't reject.** Whether the model writes `261` or `"261"` is
formatting, not a different answer. Parse permissively at the boundary and
validate on meaning (`tid in valid_ids`) instead of representation.

**The coercer must never raise.** The first fix used
`value.lstrip("-").isdigit()`, which lets `"--5"` through to `int()` — and
`"²".isdigit()` is `True` while `int("²")` raises. That call site sat *outside*
the function's own try/except, so a `ValueError` would have escaped to the
caller's blanket handler and dropped the whole stage: the exact failure the
helper existed to prevent. Use `isdecimal()`, and drop sign handling the callers
already range-check.

**"Nothing matched" needs a reason code.** All-empty is indistinguishable from
legitimately-empty, so log the difference:

- parsed some links, validated *none* → ERROR, this is a bug
- parsed some, validated *some* → WARNING, drift is usually partial and each
  rejected link silently demotes a real continuation
- parsed *nothing* (prose, refusal, renamed key, empty completion) → ERROR
- model returned all-`null` → silent, that is a real quiet day

The middle case matters most: gating on `if not linked` hides a 10-of-16 loss
entirely, and partial drift is the common shape.

## How it was found

Replaying the exact prompt (44 threads, 16 labels, rebuilt read-only from the
production DB) and printing the **raw** response, rather than reasoning about
what the model "should" have returned. The parsed-and-validated view showed
zero links; only the raw text showed sixteen correct answers in quotes.

When a model-driven stage returns nothing, capture the unparsed response before
theorising. See also
[[a-rename-is-silent-until-every-reference-is-updated]] — the same run surfaced
`DIGEST_ALERT_EMAIL` vs `HEALTH_ALERT_EMAIL`, which had silenced every health
alert for months.
