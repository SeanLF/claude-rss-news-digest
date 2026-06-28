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

LLM calls go through claude_cli.run_sync (Sonnet); the orchestration takes injectable
synth/audit callables so tests run with no LLM. Best-effort throughout: a synthesis failure
for one thread is logged and skipped, never crashing the digest.
"""

from __future__ import annotations

import json
import logging

from config import DEFAULT_MODEL

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


def _run_sonnet(user: str, system: str, *, model: str) -> str:
    import claude_cli

    return claude_cli.run_sync(
        user,
        model=model,
        system_prompt=system,
        tools=[],
        timeout=300,
        idle_timeout=120.0,
        thinking={"type": "disabled"},
    )


def synthesize_installment(
    prior_narrative: str | None,
    open_questions: list[str],
    article_ids: list[str],
    arts: dict,
    *,
    model: str = DEFAULT_MODEL,
) -> dict:
    """Synthesize today's installment for one thread (Sonnet). Raises on LLM/parse failure."""
    story_so_far = prior_narrative or "(nothing yet -- this is the thread's first tracked day)"
    questions = "\n".join(f"- {q}" for q in open_questions) or "(none yet)"
    prior = f"STORY SO FAR: {story_so_far}\nOPEN QUESTIONS:\n{questions}"
    user = f"{prior}\n\nTODAY'S SOURCE ARTICLES:\n{_bundle(article_ids, arts)}"
    return _parse_json(_run_sonnet(user, EVOLVE_SYSTEM, model=model))


def audit_whats_new(whats_new: list[dict], arts: dict, *, model: str = DEFAULT_MODEL) -> list[bool]:
    """Fact-check each whats_new fact against its cited TODAY source(s). Returns a supported flag
    per fact (same order). On audit failure, fail OPEN (keep facts) -- the synthesis already
    grounds against today's sources; the audit is a second line, not a gate that should erase
    everything if it errors."""
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
    try:
        verdicts = _parse_json(_run_sonnet("\n\n".join(claims), AUDIT_SYSTEM, model=model)).get("verdicts", [])
    except Exception:
        # Fail OPEN (keep grounded facts) but log at ERROR: a persistently-broken audit silently
        # stops dropping bad facts, which matters once threads are reader-visible (sub-project C
        # must wire this to a monitored health signal before then).
        logger.error("whats_new audit failed; keeping all facts (fail-open)", exc_info=True)
        return [True] * len(whats_new)
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
) -> list[dict]:
    """Synthesize installments for the CONTINUING threads this run (new single-day threads carry
    no prior, so threading adds nothing yet and we skip the spend). Each thread is independent and
    best-effort -- one failure is logged and skipped. Returns the verified installments."""
    out: list[dict] = []
    for a in assignments:
        if a.is_new or len(a.article_ids) < min_articles:
            continue
        try:
            installment = synth_fn(a.prior_narrative, a.open_questions, a.article_ids, arts, model=model)
            supported = audit_fn(installment.get("whats_new", []) or [], arts, model=model)
            verified = apply_installment(store, a, installment, supported, run_id)
            out.append({"thread_id": a.thread_id, **verified})
        except Exception:
            logger.warning("thread synthesis failed for thread %s (skipped)", a.thread_id, exc_info=True)
    return out
