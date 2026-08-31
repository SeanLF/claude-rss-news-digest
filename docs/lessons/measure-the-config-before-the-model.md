# Measure the config before you measure the model

*2026-08-30. From the COHERENCE model sweep: a model comparison that ran 94 times and
answered the wrong question twice before it answered the right one.*

## The lesson

**Before comparing models, verify every model is running the configuration it should be.**
A sweep that varies the model while holding a bad config constant does not measure the models —
it measures them all under a handicap, and the handicap may not fall equally.

Concretely: COHERENCE was compared across four models to decide whether to switch. Every one of the
60 runs inherited `thinking: {"type": "disabled"}` from a module-level constant. When thinking was
added as a second axis, the picture inverted:

```
                              recall        $/run
sonnet-5 / disabled  (prod)   0.761         1.357
sonnet-5 / adaptive           0.919         0.994     <- better AND 26% cheaper
opus-5   / adaptive           0.919         1.705     <- identical recall, 72% more
```

Turning thinking on was a **bigger** effect (+0.158, p=0.002) than swapping the model
(+0.109, p=0.028), and it reduced the model choice to a coin flip. The whole first sweep was
answering "which model is best at running badly configured?"

## Why it happened

The constant was justified by a real incident — extended thinking on a ~460-article clustering
prompt tripped the 32k output ceiling and aborted a run — and was then applied globally. Two
things made it invisible:

1. **The justifying stage stopped using the code path.** CLUSTER was replaced by a deterministic
   extract→join, so its prompt is never sent to a model. The premise was not stale, it was void,
   and nothing failed to tell anyone.
2. **A global default is not visible at the call site.** The per-stage override
   (`spec.thinking or _THINKING`) already existed. Nobody was choosing "disabled" for COHERENCE;
   they were inheriting it.

## The mechanism worth remembering

**A model with nowhere to think may substitute re-reading for reasoning.** Sonnet 5 with thinking
disabled burned **3.31M cache-read tokens per run**; with adaptive thinking, **573k** — 5.8x less,
while producing 2x the output tokens (the thinking itself) and catching more errors.

That inverts the usual intuition that reasoning costs more. Here it was **cheaper**, because the
re-reading it replaced was more expensive than the thinking. Do not assume the direction — measure
`cache_read_input_tokens` per run on both settings.

## How to apply it

- **Log the full request config alongside every eval record**, not just the model id. This sweep was
  only auditable because `thinking`, `usage`, and `total_cost_usd` were in each JSONL line.
- **`output_tokens_details.thinking_tokens` is the cheap proof the manipulation took effect** —
  0 on a disabled arm, non-zero on an adaptive one. Check it before trusting any thinking-related
  result.
- **When a global default has a stage-specific justification, check the stage still exists.**
  Grep for the code path, do not trust the comment.
- **Override at the one stage you measured**, not the global. Changing a shared default alters
  every stage on evidence gathered for one.
- **Rebuild the image before verifying an agent spec.** `.claude/agents/` is `COPY`'d at build time,
  so a `docker compose run` against a cached image reports the old frontmatter — this produced a
  false negative that briefly looked like a broken parser.

## Related

- `docs/2026-08-30-health-check-and-clustering-sota.md` — full measurement and decision
- `docs/lessons/strict-types-on-model-output-turn-drift-into-silent-loss.md` — the sibling failure:
  a constraint that guarantees shape while losing correspondence
