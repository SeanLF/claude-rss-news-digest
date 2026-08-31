"""Threaded synthesis + ledger (sub-project B).

For each CONTINUING thread (identified by sub-project A), synthesize today's installment
against the thread's carried state: what's genuinely new, which open questions today's
sources answer, and what new questions arise. Then audit the new facts for faithfulness and
DROP the unsupported ones before persisting -- the same synthesize->audit discipline as the
production WRITE->COHERENCE pair. The surviving facts ARE the reader-facing delta (top few
joined) and the thread's running memory.

The faithfulness fix proven in the PoC (`scratch/cluster-replay/evolving_thread.py`): wall
MEMORY (the recent deltas, which may reference prior days) from FACTS (`whats_new`, which must
cite a TODAY source and carry nothing forward). The walling more than halved the unsupported
rate (22.5% -> 8.4%); the residual is mopped up here by the audit->drop.

LLM calls go through claude_cli.run_agent (Sonnet), capturing per-call token usage + cost so
B's spend is attributed in run_usage like every other stage; the orchestration takes injectable
synth/audit callables so tests run with no LLM. Best-effort throughout: a synthesis failure for
one thread is logged and skipped, never crashing the digest.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re

import threads
from claude_agent_sdk import ThinkingConfig
from config import DEFAULT_MODEL
from usage import usage_row_from_sdk

# Late-binding (sub-project D): a story's facets are often scattered across hard clusters (an
# "Iran deal" cluster + a separate "Hormuz shipping" + "reactions"). We expand a thread's seed
# cluster to the entity-soft neighbourhood across the run's articles so the synthesis sees the
# whole story. Entity signature = capitalized-name tokens; _LB_STOP drops sentence-leading
# function words ("The", "A") so they don't masquerade as entities.
_ENTITY_RE = re.compile(r"[A-Z][A-Za-z'&.-]+(?:\s+[A-Z][A-Za-z'&.-]+)*")
_LB_STOP = frozenset(
    [
        "the",
        "a",
        "an",
        "this",
        "that",
        "these",
        "those",
        "it",
        "its",
        "their",
        "his",
        "her",
        "our",
        "your",
        "new",
        "but",
        "and",
        "for",
    ]
)

logger = logging.getLogger(__name__)

EVOLVE_SYSTEM = """You maintain an EVOLVING daily digest thread for ONE ongoing news story. You are given RECENT UPDATES (what's already been reported to readers on prior days) + OPEN QUESTIONS, and TODAY'S source articles.

CRITICAL GROUNDING RULE: RECENT UPDATES are MEMORY -- they tell you what's ALREADY been reported so you can identify what is genuinely NEW today and avoid repeating it. Every fact you put in `whats_new` MUST be stated in TODAY'S articles, and you MUST record the exact today-article ID(s) that state it in that fact's `sources` list. NEVER carry a fact from RECENT UPDATES into whats_new -- if a development isn't in today's articles, it is not today's news. `resolved` and `still_open` may reference prior context; `whats_new` may NOT.

WHERE IDs GO: article IDs are internal bookkeeping and are shown to NOBODY. They belong in the `sources` list ONLY. NO prose field may contain an article ID -- not `fact`, not `new_questions`, not `still_open`, not a `resolved` entry's `question` or `how`. No "A238", no "(A238)", no "according to A238", no "[A238]". Every one of those strings ships verbatim to readers (facts as the story's summary, questions on the public thread page), so an ID written into any of them is a visible defect. Attribute in prose by OUTLET NAME ("according to Reuters") or not at all.

Produce today's installment:
- whats_new: today's genuinely NEW developments (not already in RECENT UPDATES). ORDER THEM MOST IMPORTANT FIRST, and write each as ONE clean, self-contained sentence a reader could see as the story's update -- because the top few will be shown verbatim as today's summary. EACH must be grounded in and cite today's articles (verifiable from the cited article alone). If nothing is new, return [].
- resolved: which OPEN QUESTIONS today's articles now answer, and how (cite today's article). Use the EXACT wording of the open question you are resolving.
- new_questions: new open questions today's developments raise.
- still_open: prior open questions still unanswered (use their exact wording).
Invent nothing; preserve disagreement.
Output ONLY JSON: {"whats_new": [{"fact": "...", "sources": ["A1"]}], "resolved": [{"question": "...", "how": "..."}], "new_questions": ["..."], "still_open": ["..."]}"""

SUMMARY_CHARS = 400  # how much of each article summary to feed the synthesis/audit prompts

AUDIT_SYSTEM = """You are a strict fact-checker. You are given CLAIMS, each with the FULL TEXT of the source article(s) it cites. For each claim decide if it is SUPPORTED by its cited source text ALONE (the specific -- number/name/date/quote -- must actually appear or be directly entailed). If the cited text does not support it, mark supported=false.
Return ONE verdict per claim: N claims means N verdicts, ids 1..N, none omitted or merged.
Output ONLY JSON: {"verdicts": [{"id": 1, "supported": true}, {"id": 2, "supported": false, "issue": "short reason"}]}"""

# Appended for the second attempt. Worded impersonally on purpose: _run_sonnet starts a FRESH
# query() with no resume, so the model reading this never saw the reply being described. The
# "one JSON object" line is not filler -- a model that notices its own mistake tends to emit the
# bad object, some prose, then a corrected one, which is the very shape that made run 271 look
# like a one-verdict answer.
_AUDIT_REASK = """

IMPORTANT: an earlier attempt at these exact claims came back unusable ({problem}). Return EXACTLY {n} verdicts, ids 1 through {n}, one per CLAIM above, each carrying "supported": true or false. Output ONE JSON object and nothing else -- no prose, no second attempt inside the same reply."""


def _parse_json(text: str) -> dict:
    """Parse the first complete JSON object, ignoring any trailing prose the model emits."""
    return json.JSONDecoder().raw_decode(text[text.index("{") :])[0]


def _json_objects(text: str) -> list[dict]:
    """Every top-level JSON object in the reply, in order.

    A model that catches its own mistake mid-reply writes the bad object, a line of prose, then a
    corrected one. Replaying run 271's real audit prompts, 3 of 36 replies did exactly that -- and
    on the six-claim thread the FIRST object was the malformed one and the second was right. Taking
    only the first (`_parse_json`) picks the wrong object precisely when it matters, so the audit
    reads them all and keeps whichever actually answers the claims.
    """
    decoder = json.JSONDecoder()
    objects: list[dict] = []
    i = 0
    while (i := text.find("{", i)) >= 0:
        try:
            obj, end = decoder.raw_decode(text[i:])
        except ValueError:
            i += 1  # a brace inside prose, not the start of an object
            continue
        if isinstance(obj, dict):
            objects.append(obj)
        i += end
    return objects


def _verdict_pairs(verdicts) -> tuple[list[tuple], int]:
    """((id, supported) per verdict, count whose `supported` could not be read as a boolean).

    Three cases, and the difference between the last two is the whole point:

    * `supported` is a boolean, or an unambiguous spelling of one (``1``/``0``, ``"true"``,
      ``"no"``) -- the judgment, read for what it plainly says.
    * `supported` is PRESENT but says nothing readable (``null``, ``"maybe"``) -- the auditor
      judged and we cannot recover the answer. The safe reading is NOT supported, so the fact
      drops. Never `bool(value)`: ``bool("false")`` is True, which used to ship the very fact
      the auditor had rejected.
    * `supported` is ABSENT -- nothing was judged, so this is not a verdict at all. Leaving it out
      makes the claim read as unanswered, which is what keeps a shape-only reply LOUD instead of
      resolving to a clean "everything supported" with no failure recorded.

    Pairs rather than a dict so the caller can still see a duplicate id; ``dict()`` would collapse
    one auditor verdict onto another and pick a winner silently.
    """
    if not isinstance(verdicts, list):
        return [], 0
    pairs, unreadable = [], 0
    for v in verdicts:
        if not isinstance(v, dict) or "id" not in v or "supported" not in v:
            continue
        supported = _read_supported(v["supported"])
        if supported is None:
            supported = False
            unreadable += 1
        pairs.append((v["id"], supported))
    return pairs, unreadable


# Unambiguous spellings of a verdict. Observed reality is that the auditor returns real JSON
# booleans (286/286 verdicts across a 48-call replay of run 271), so this is belt-and-braces --
# but the alternative to reading `1` and `"true"` is dropping a fact the auditor supported.
_TRUTHY = {"true", "yes", "y", "1"}
_FALSY = {"false", "no", "n", "0"}


def _read_supported(value) -> bool | None:
    """The verdict a value plainly states, or None when it states nothing readable."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        token = value.strip().lower()
        if token in _TRUTHY:
            return True
        if token in _FALSY:
            return False
    return None


def _answer_for(obj: dict, n: int, claim_ids) -> tuple[dict, int] | None:
    """({id: supported}, unreadable count) if this object answers the claim list, else None.

    "Answers" is deliberately one rule with no carve-outs: the `verdicts` array must hold exactly
    n elements, each a verdict, with ids exactly 1..n. Anything the claim list does not account
    for -- a duplicate id, an out-of-range id, a bare string sitting among the verdicts -- means
    the auditor was not tracking the claims, and it is not this function's business to guess which
    stray elements are harmless. An earlier draft tolerated junk beside a complete set, which made
    acceptance depend on whether the STRAY happened to parse rather than on whether the claims
    were covered: a duplicate id was rejected while a bare string was waved through.
    """
    raw = obj.get("verdicts", [])
    if not isinstance(raw, list) or len(raw) != n:
        return None
    pairs, unreadable = _verdict_pairs(raw)
    supported_by_id = dict(pairs)
    if len(pairs) != n or set(supported_by_id) != set(claim_ids):
        return None
    return supported_by_id, unreadable


def _describe_mismatch(obj: dict, n: int, claim_ids) -> str:
    """Why an object failed `_answer_for`, in the words the alert and the re-ask will carry."""
    raw = obj.get("verdicts", [])
    if not isinstance(raw, list):
        return f"`verdicts` was {type(raw).__name__}, not a list of {n}"
    pairs, _ = _verdict_pairs(raw)
    supported_by_id = dict(pairs)
    missing = [i for i in claim_ids if i not in supported_by_id]
    return (
        f"verdicts missing/misaligned for claim(s) {missing or 'none'} "
        f"({len(raw)} element(s), {len(pairs)} usable, ids {sorted(supported_by_id, key=str)})"
    )


def _bundle(article_ids: list[str], arts: dict) -> str:
    return "\n\n".join(
        f"{a}: {arts[a]['title']}\n   {arts[a].get('summary', '')[:SUMMARY_CHARS]}" for a in article_ids if a in arts
    )


def _article_signature(art: dict) -> set[str]:
    """Entity-token signature of one article (capitalized names in title + summary lead)."""
    text = f"{art.get('title', '')} {art.get('summary', '')[:SUMMARY_CHARS]}"
    sig: set[str] = set()
    for ent in _ENTITY_RE.findall(text):
        for word in re.split(r"[\s-]+", ent.lower()):
            word = word.strip(".'&")
            if len(word) >= 3 and word not in _LB_STOP:
                sig.add(word)
    return sig


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def _hub_entities(sigs: dict[str, set[str]], *, max_df: float) -> set[str]:
    """Entities appearing in more than `max_df` of the run's articles -- hubs like Trump/US/China
    that connect unrelated stories. Dropping them is the IDF fix for late-binding's over-pull
    (naive entity-overlap pulls 11/15 unrelated; with hubs removed the neighbourhood is the story
    family). Only meaningful at run scale, so the caller skips this on tiny sets."""
    n = len(sigs)
    counts: dict[str, int] = {}
    for sig in sigs.values():
        for tok in sig:
            counts[tok] = counts.get(tok, 0) + 1
    return {tok for tok, c in counts.items() if c / n > max_df}


def expand_neighbourhood(
    seed_ids: list[str], arts: dict, *, threshold: float, max_extra: int, hub_max_df: float = 0.12
) -> list[str]:
    """Widen a thread's seed article set with the most entity-similar articles elsewhere in the
    run (late-binding). Returns seed_ids + up to `max_extra` neighbours scoring >= threshold by
    entity-Jaccard against any seed article, AFTER dropping hub entities (IDF) so a shared
    "Trump"/"US" doesn't fuse unrelated stories. The threshold + cap + the synthesis's cite-today
    grounding are the remaining guards against over-pull."""
    sigs = {aid: _article_signature(arts[aid]) for aid in arts}
    # IDF hub-stripping only bites at run scale; on tiny sets every entity looks like a hub.
    hubs = _hub_entities(sigs, max_df=hub_max_df) if len(sigs) >= 30 else set()
    disc = {aid: (sig - hubs) for aid, sig in sigs.items()}

    seed = [a for a in seed_ids if a in arts]
    seed_sigs = [disc[a] for a in seed if disc.get(a)]
    if not seed_sigs:
        return list(seed_ids)
    seed_set = set(seed)
    scored: list[tuple[float, str]] = []
    for aid in arts:
        if aid in seed_set:
            continue
        best = max((_jaccard(disc[aid], ss) for ss in seed_sigs), default=0.0)
        if best >= threshold:
            scored.append((best, aid))
    scored.sort(reverse=True)
    return list(seed_ids) + [aid for _, aid in scored[:max_extra]]


# Named rather than inlined so the value sent to run_agent and the value recorded in
# run_usage cannot drift apart.
_THINKING: ThinkingConfig = {"type": "disabled"}


def _run_sonnet(user: str, system: str, *, model: str, subagent: str, usage_rows: list[dict] | None) -> str:
    """Run one Sonnet call via run_agent (so token usage + cost are captured) and return its text.
    When usage_rows is provided, append this call's run_usage row so B's spend shows up in the
    run_usage breakdown like every other stage. Raises if the call doesn't end successfully."""
    import claude_cli

    result = asyncio.run(
        asyncio.wait_for(
            claude_cli.run_agent(
                user,
                model=model,
                system_prompt=system,
                tools=[],
                idle_timeout=120.0,
                thinking=_THINKING,
            ),
            timeout=300,
        )
    )
    if not result.ok:
        raise RuntimeError(f"claude failed: {result.error_summary()}")
    if usage_rows is not None and not result.usage:
        # claude_cli normalizes a missing SDK usage payload to {}, which is falsy -- so the call
        # happened, was billed, and would leave no run_usage row. Say so, or the stage quietly
        # under-reports its own spend.
        logger.warning("%s call returned no usage payload; its cost is missing from run_usage", subagent)
    if usage_rows is not None and result.usage:
        usage_rows.append(
            usage_row_from_sdk(
                subagent,
                model,
                result.usage,
                result.total_cost_usd or 0.0,
                duration_ms=result.duration_ms,
                # Explicit `effort=None` records a deliberate SDK default; omitting it
                # would record NULL, which means "not recorded" (see usage._UNSET).
                thinking=_THINKING,
                effort=None,
            )
        )
    return result.text


def synthesize_installment(
    recent_updates: list[str],
    open_questions: list[str],
    article_ids: list[str],
    arts: dict,
    *,
    model: str = DEFAULT_MODEL,
    usage_rows: list[dict] | None = None,
) -> dict:
    """Synthesize today's installment for one thread (Sonnet). `recent_updates` is the memory --
    the last few days' delta lines (what's already been reported) so the model can compute what's
    genuinely NEW. Raises on LLM/parse failure."""
    updates = "\n".join(f"- {u}" for u in recent_updates) or "(nothing yet -- this is the thread's first tracked day)"
    questions = "\n".join(f"- {q}" for q in open_questions) or "(none yet)"
    prior = f"RECENT UPDATES:\n{updates}\nOPEN QUESTIONS:\n{questions}"
    user = f"{prior}\n\nTODAY'S SOURCE ARTICLES:\n{_bundle(article_ids, arts)}"
    return _parse_json(
        _run_sonnet(user, EVOLVE_SYSTEM, model=model, subagent="thread_synthesis", usage_rows=usage_rows)
    )


def audit_whats_new(
    whats_new: list[dict], arts: dict, *, model: str = DEFAULT_MODEL, usage_rows: list[dict] | None = None
) -> list[bool]:
    """Fact-check each whats_new fact against its cited TODAY source(s). Returns a supported flag
    per fact (same order). RAISES on LLM/parse failure -- the caller (synthesize_threads) owns the
    fail-open decision AND counts the failure, so a persistently-broken audit is recorded as a
    health signal rather than silently keeping facts unchecked forever.

    An unusable REPLY (no JSON, or verdicts that don't answer the claim list) buys one re-ask
    first -- a malformed answer, not a broken endpoint, so spending the fail-open on the first bad
    draw throws away the audit for a whole thread. Whether a second draw is likelier to land is
    NOT measured; what the re-ask actually adds is a sharper instruction, since it names the
    required count and forbids the self-correcting two-object reply. Exactly one re-ask: a
    persistently-broken auditor must still reach the caller's count-and-alert, and a daily run is
    no place for a retry spiral. Transport failures are NOT re-asked -- those raise out of
    _run_sonnet and the caller fails open as before.

    Addressable failure rate, measured on prod: 4 in the 36 runs since the alignment check landed
    (`701e7f9`, deployed 2026-07-17), about 1.8% of the 220 audit calls in that window. Run 215's
    older failure predates the check and was some other path, so it is not in this denominator."""
    if not whats_new:
        return []
    claims = []
    for i, f in enumerate(whats_new, 1):
        srcs = "\n".join(
            f"  [{s}] {arts[s]['title']}. {arts[s].get('summary', '')[:SUMMARY_CHARS]}"
            for s in f.get("sources", [])
            if s in arts
        )
        claims.append(f"CLAIM {i}: {f.get('fact', '')}\nCITED SOURCE(S):\n{srcs or '  (none cited)'}")
    user = "\n\n".join(claims)
    n = len(whats_new)
    claim_ids = range(1, n + 1)
    problem = ""
    for attempt in (1, 2):
        prompt = user if attempt == 1 else user + _AUDIT_REASK.format(problem=problem, n=n)
        text = _run_sonnet(prompt, AUDIT_SYSTEM, model=model, subagent="thread_audit", usage_rows=usage_rows)
        objects = _json_objects(text)
        if not objects:
            problem = f"no JSON object in the reply, which began {text[:60]!r}"
        else:
            # LAST match wins: when a reply carries two objects the later one is the model's
            # correction of the earlier, and that is the answer it meant to give.
            usable = [answer for obj in objects if (answer := _answer_for(obj, n, claim_ids))]
            if usable:
                supported_by_id, unreadable = usable[-1]
                if unreadable:
                    # The audit DID cover every claim, so this is not an audit_failure -- but a
                    # verdict we had to read as "unsupported" silently drops a fact, so say it.
                    logger.warning("thread audit returned %d verdict(s) with an unreadable `supported`", unreadable)
                return [supported_by_id[i] for i in claim_ids]
            problem = _describe_mismatch(objects[-1], n, claim_ids)
        logger.warning("thread audit reply unusable on attempt %d/2: %s", attempt, problem)
    raise ValueError(f"audit {problem}")


def apply_installment(store, assignment, installment: dict, supported: list[bool], run_id: int) -> dict:
    """Persist a synthesized installment: drop unsupported whats_new facts (the survivors ARE the
    thread's running record + next-day memory), resolve answered questions, raise new ones, and
    store the verified installment. Returns the verified installment (unsupported facts removed)."""
    whats_new = installment.get("whats_new", []) or []
    kept = [f for f, ok in zip(whats_new, supported, strict=False) if ok]
    # PRE-audit citations, persisted with the installment. This is the grounding scope for the
    # run's open questions, and it has to be pre-audit: the audit drops unsupported facts and
    # takes their sources with them, so a question referencing a dropped fact would otherwise
    # be ungrounded (prod thread 6 asks about A12, whose fact did not survive). Run-scoped by
    # construction -- article ids are per-run labels, meaningless outside their own run.
    cited_ids = sorted({s for f in whats_new if isinstance(f, dict) for s in threads._cited(f.get("sources"))})
    verified = {**installment, "whats_new": kept, "cited_ids": cited_ids}

    tid = assignment.thread_id
    open_now = set(assignment.open_questions)
    new_questions = installment.get("new_questions", []) or []

    # One atomic unit: never leave questions resolved while the installment row is left
    # content-less (a partial write the next run would read as truth).
    with store.transaction():
        for r in installment.get("resolved", []) or []:
            question = r.get("question", "")
            if question in open_now:  # only resolve questions we actually carried (avoid drift)
                store.resolve_question(tid, question, run_id, r.get("how", ""))
        # Questions are stored UNCHANGED and suppressed at render time instead. Dropping one
        # here is permanent: it never reaches the thread's carried OPEN QUESTIONS memory and can
        # never be resolved, so a false positive silently erases a real question forever. The
        # renderer's suppression is terminal and covers already-stored rows, so the write path
        # only has to make the leak visible.
        if new_questions and threads.clean_questions(new_questions, cited_ids) != new_questions:
            logger.warning(
                "thread %s: a new question cites an article id inline; it will be suppressed in "
                "the public ledger (see thread_synthesis EVOLVE_SYSTEM)",
                tid,
            )
        if new_questions:
            store.add_questions(tid, new_questions, run_id)
        store.set_installment_content(tid, run_id, json.dumps(verified))
    return verified


def synthesize_threads(
    assignments,
    arts: dict,
    run_id: int,
    store,
    *,
    model: str = DEFAULT_MODEL,
    min_articles: int = 2,
    synth_fn=None,
    audit_fn=None,
    record_health: bool = True,
    usage_rows: list[dict] | None = None,
    latebind_threshold: float | None = None,
    latebind_max_extra: int = 12,
) -> tuple[list[dict], int]:
    """Synthesize installments for the CONTINUING threads this run (new single-day threads carry
    no prior, so threading adds nothing yet and we skip the spend). Each thread is independent and
    best-effort -- a synthesis failure is logged and skipped.

    The faithfulness audit fails OPEN (keep grounded facts if the auditor itself errors) but the
    failure is COUNTED and recorded to thread_runs as a health signal -- a persistently-failing
    audit means unsupported facts are no longer being dropped, which must surface (not silently
    swallow) before threads are reader-visible. Returns (verified installments, audit_failures);
    the in-memory count is authoritative for alerting -- the DB row is best-effort durable history.
    synth_fn/audit_fn are resolved at call time (overridable) so tests can inject fakes."""
    synth_fn = synth_fn or synthesize_installment
    audit_fn = audit_fn or audit_whats_new
    out: list[dict] = []
    audit_failures = 0
    for a in assignments:
        if a.is_new or len(a.article_ids) < min_articles:
            continue
        # Late-binding (sub-project D): widen the seed cluster to its entity-soft neighbourhood.
        article_ids = a.article_ids
        if latebind_threshold is not None:
            article_ids = expand_neighbourhood(
                a.article_ids, arts, threshold=latebind_threshold, max_extra=latebind_max_extra
            )
        try:
            installment = synth_fn(
                a.recent_updates, a.open_questions, article_ids, arts, model=model, usage_rows=usage_rows
            )
        except Exception:
            logger.warning("thread synthesis failed for thread %s (skipped)", a.thread_id, exc_info=True)
            continue
        whats_new = installment.get("whats_new", []) or []
        try:
            supported = audit_fn(whats_new, arts, model=model, usage_rows=usage_rows)
        except Exception:
            logger.error("whats_new audit failed for thread %s; keeping facts (fail-open)", a.thread_id, exc_info=True)
            audit_failures += 1
            supported = [True] * len(whats_new)
        try:
            verified = apply_installment(store, a, installment, supported, run_id)
            out.append({"thread_id": a.thread_id, **verified})
        except Exception:
            logger.warning("persisting thread %s installment failed (skipped)", a.thread_id, exc_info=True)

    if record_health:
        try:
            store.record_run_health(run_id, synthesized=len(out), audit_failures=audit_failures)
        except Exception:
            # Durable history is best-effort; the returned in-memory count is what drives the alert.
            logger.error("failed to record thread run health (run %s)", run_id, exc_info=True)
    return out, audit_failures
