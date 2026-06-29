"""Threaded synthesis + ledger (sub-project B).

For each CONTINUING thread (identified by sub-project A), synthesize today's installment
against the thread's carried state: what's genuinely new, which open questions today's
sources answer, what new questions arise, and an updated running narrative. Then audit the
new facts for faithfulness and DROP the unsupported ones before persisting -- the same
synthesize->audit discipline as the production WRITE->COHERENCE pair.

The faithfulness fix proven in the PoC (`scratch/cluster-replay/evolving_thread.py`): wall
MEMORY (the running narrative + ledger, which may reference prior days) from FACTS (`whats_new`,
which must cite a TODAY source and carry nothing forward). That walling more than halved the
unsupported rate (22.5% -> 8.4%); the residual is mopped up here by the audit->drop.

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

EVOLVE_SYSTEM = """You maintain an EVOLVING daily digest thread for ONE ongoing news story. You are given the STORY SO FAR (a running summary + OPEN QUESTIONS from prior days) and TODAY'S source articles.

CRITICAL GROUNDING RULE: the STORY SO FAR is MEMORY for context and continuity ONLY. Every fact you put in `whats_new` MUST be stated in TODAY'S articles and cite the exact today-article ID(s) that state it. NEVER carry a fact from the STORY SO FAR into whats_new -- if a development isn't in today's articles, it is not today's news. `updated_narrative`, `resolved`, and `still_open` may reference prior context; `whats_new` may NOT.

Produce today's installment:
- whats_new: today's genuinely NEW developments, EACH grounded in and citing today's articles (verifiable from the cited article alone). If nothing is new, return [].
- resolved: which OPEN QUESTIONS today's articles now answer, and how (cite today's article). Use the EXACT wording of the open question you are resolving.
- new_questions: new open questions today's developments raise.
- still_open: prior open questions still unanswered (use their exact wording).
- updated_narrative: a 2-3 sentence running summary of the whole story to date (may use memory).
Invent nothing; preserve disagreement.
Output ONLY JSON: {"whats_new": [{"fact": "...", "sources": ["A1"]}], "resolved": [{"question": "...", "how": "..."}], "new_questions": ["..."], "still_open": ["..."], "updated_narrative": "..."}"""

SUMMARY_CHARS = 400  # how much of each article summary to feed the synthesis/audit prompts

AUDIT_SYSTEM = """You are a strict fact-checker. You are given CLAIMS, each with the FULL TEXT of the source article(s) it cites. For each claim decide if it is SUPPORTED by its cited source text ALONE (the specific -- number/name/date/quote -- must actually appear or be directly entailed). If the cited text does not support it, mark supported=false.
Output ONLY JSON: {"verdicts": [{"id": 1, "supported": true/false, "issue": "short reason if false"}]}"""


def _parse_json(text: str) -> dict:
    """Parse the first complete JSON object, ignoring any trailing prose the model emits."""
    return json.JSONDecoder().raw_decode(text[text.index("{") :])[0]


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
                thinking={"type": "disabled"},
            ),
            timeout=300,
        )
    )
    if not result.ok:
        raise RuntimeError(f"claude failed: {result.error_summary()}")
    if usage_rows is not None and result.usage:
        usage_rows.append(usage_row_from_sdk(subagent, model, result.usage, result.total_cost_usd or 0.0))
    return result.text


def synthesize_installment(
    prior_narrative: str | None,
    open_questions: list[str],
    article_ids: list[str],
    arts: dict,
    *,
    model: str = DEFAULT_MODEL,
    usage_rows: list[dict] | None = None,
) -> dict:
    """Synthesize today's installment for one thread (Sonnet). Raises on LLM/parse failure."""
    story_so_far = prior_narrative or "(nothing yet -- this is the thread's first tracked day)"
    questions = "\n".join(f"- {q}" for q in open_questions) or "(none yet)"
    prior = f"STORY SO FAR: {story_so_far}\nOPEN QUESTIONS:\n{questions}"
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
    health signal rather than silently keeping facts unchecked forever."""
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
    text = _run_sonnet("\n\n".join(claims), AUDIT_SYSTEM, model=model, subagent="thread_audit", usage_rows=usage_rows)
    verdicts = _parse_json(text).get("verdicts", [])
    supported_by_id = {v.get("id"): v.get("supported", True) for v in verdicts}
    return [bool(supported_by_id.get(i, True)) for i in range(1, len(whats_new) + 1)]


def apply_installment(store, assignment, installment: dict, supported: list[bool], run_id: int) -> dict:
    """Persist a synthesized installment: drop unsupported whats_new facts, update the running
    narrative, resolve answered questions, raise new ones, and store the verified installment.
    Returns the verified installment (unsupported facts removed)."""
    whats_new = installment.get("whats_new", []) or []
    kept = [f for f, ok in zip(whats_new, supported, strict=False) if ok]
    verified = {**installment, "whats_new": kept}

    tid = assignment.thread_id
    open_now = set(assignment.open_questions)
    new_questions = [q for q in (installment.get("new_questions", []) or []) if q]
    narrative = installment.get("updated_narrative")

    # One atomic unit: never leave the narrative advanced / questions resolved while the
    # installment row is left content-less (a partial write the next run would read as truth).
    with store.transaction():
        if narrative:
            store.set_narrative(tid, narrative)
        for r in installment.get("resolved", []) or []:
            question = r.get("question", "")
            if question in open_now:  # only resolve questions we actually carried (avoid drift)
                store.resolve_question(tid, question, run_id, r.get("how", ""))
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
    synth_fn=synthesize_installment,
    audit_fn=audit_whats_new,
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
    the in-memory count is authoritative for alerting -- the DB row is best-effort durable history."""
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
                a.prior_narrative, a.open_questions, article_ids, arts, model=model, usage_rows=usage_rows
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
