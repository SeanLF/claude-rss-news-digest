"""Repair-not-drop: regenerate a COHERENCE-failed headline/summary instead of
dropping the whole story.

The shipped COHERENCE reframe (adversarial probes + Sonnet 5) is precise but
strict, so it drops MORE stories -- and merge.py drops the WHOLE story on a
headline/summary failure (only a why_it_matters-only failure degrades). On run
245 that dropped a must_know lead over one unsupported clause. Repair localizes
the flagged field, regenerates it from the SAME cited sources the checker used,
re-checks it, and keeps the story only if the re-check passes.

This module is the deterministic, model-free core (RARR-shaped: localize ->
minimal correction -> guard -> re-check). The model calls (the repairer prompt
and the scoped re-check) are wired in Step 2 behind config.REPAIR_ENABLED; here
we build the request, apply a repaired field under hard guards, assemble the
resolution merge consumes, and log every event. Every guard falls back to
today's drop -- repair can only ever KEEP a story it could not otherwise keep,
never ship something worse.
"""

import json
import logging
from pathlib import Path

from merge import (
    _INTERNAL_ID_PATTERNS,
    _REPAIRABLE_FIELDS,
    _coherence_failed,
    _item_article_ids,
    _norm_headline,
    _result_matches,
)

logger = logging.getLogger(__name__)

# _REPAIRABLE_FIELDS (imported from merge, the single source of truth) is the set
# of fields repair regenerates in the MVP; Phase 2 adds why_it_matters repair.
_TEXT_FIELDS = ("headline", "summary", "why_it_matters")


def _index_by_article_ids(payload: dict) -> dict[frozenset[str], dict]:
    """Index a ``{"results": [...]}`` payload by each result's article_ids identity.

    Keying on article_ids (not the headline) is drift-proof: a repair need not
    echo the headline, and the headline may itself be the field being repaired.
    Results whose article_ids is missing or not a list of strings are skipped.
    """
    index: dict[frozenset[str], dict] = {}
    for result in payload.get("results", []):
        ids = result.get("article_ids")
        # Require a NON-EMPTY id list: two stories with empty sources would both
        # key to frozenset() and collide, letting one story's repaired text
        # attach to another. An empty-ids result is skipped (reads as missing).
        if isinstance(ids, list) and ids and all(isinstance(i, str) for i in ids):
            index[frozenset(ids)] = result
    return index


def _usable_repairable_fields(result: dict) -> list[str] | None:
    """The sorted repairable fields named by a FAILED coherence result, or None
    if the failure is not a clean headline/summary case repair can handle.

    Returns None (leave on the drop path) when failed_fields is absent, empty,
    not a list of strings, or names anything outside {headline, summary} --
    including a mix with why_it_matters or an unknown field name. This mirrors
    merge._why_it_matters_only_failure's "over-dropping is safer" stance: only a
    failure set that is a non-empty subset of the repairable fields is repaired.
    """
    fields = result.get("failed_fields")
    if not isinstance(fields, list) or not fields:
        return None
    if not all(isinstance(f, str) for f in fields):
        return None
    fieldset = set(fields)
    if not fieldset <= _REPAIRABLE_FIELDS:
        return None
    return sorted(fieldset)


def build_repair_requests(draft: dict, coherence: dict) -> dict:
    """Build the repair requests for every story with a repairable failure.

    A request carries the three text fields verbatim (the repairer needs full
    context, not just the flagged field), the flagged fields, the checker's
    reason, and the story's article_ids (both its stable identity and the exact
    source universe the checker verified against). Stories that pass, are
    unmatched, or fail in a non-repairable shape produce no request and are left
    for merge.py to handle exactly as today.
    """
    results = coherence.get("results", [])
    failed = [r for r in results if _coherence_failed(r)]

    requests = []
    for tier in ("must_know", "should_know"):
        for item in draft.get(tier, []):
            item_ids = _item_article_ids(item)
            item_norm = _norm_headline(item.get("headline", ""))
            match = next((r for r in failed if _result_matches(r, item_ids, item_norm)), None)
            if match is None:
                continue
            fields = _usable_repairable_fields(match)
            if fields is None:
                continue
            requests.append(
                {
                    "article_ids": sorted(item_ids),
                    "failed_fields": fields,
                    "reason": match.get("reason", ""),
                    "fields": {f: item.get(f, "") for f in _TEXT_FIELDS},
                }
            )
    return {"requests": requests}


def _leaks_internal_id(text: str) -> bool:
    """Whether repaired text carries an internal cluster index or opaque article
    id -- the same reader-facing leak merge.py guards on selected.json's blurb."""
    return any(p.search(text) for p in _INTERNAL_ID_PATTERNS)


def apply_repairs(repair_requests: dict, repaired: dict) -> dict:
    """Apply repaired field text under hard guards, one verdict per request.

    A story is patched ONLY if the repairer returned exactly the flagged fields
    (no more -- a clean field is untouchable by construction; no less -- an
    unrepaired flagged field is still bad) and every returned field is non-empty
    and free of internal-id leaks. Any violation yields ok=False with an empty
    patch, so merge.py falls back to today's drop. The self-reported ``action``
    is carried for logging only, never trusted as a guard.
    """
    index = _index_by_article_ids(repaired)

    applied = []
    for req in repair_requests.get("requests", []):
        ids = frozenset(req.get("article_ids", []))
        flagged = set(req.get("failed_fields", []))
        entry = {"article_ids": sorted(ids), "ok": False, "patched_fields": {}, "action": None, "guard": None}

        result = index.get(ids)
        if result is None:
            entry["guard"] = "missing from repaired output"
            applied.append(entry)
            continue

        entry["action"] = result.get("action")
        present = {f: result[f] for f in _TEXT_FIELDS if f in result}
        if set(present) != flagged:
            entry["guard"] = f"repaired fields {sorted(present)} do not match flagged fields {sorted(flagged)}"
            applied.append(entry)
            continue

        guard = None
        for field, value in present.items():
            if not isinstance(value, str) or not value.strip():
                guard = f"{field} is empty or whitespace"
                break
            if _leaks_internal_id(value):
                guard = f"{field} leaks an internal id"
                break
        if guard:
            entry["guard"] = guard
            applied.append(entry)
            continue

        entry["ok"] = True
        entry["patched_fields"] = present
        applied.append(entry)
    return {"applied": applied}


def build_repair_resolution(applied: dict, recheck: dict) -> dict:
    """Assemble the resolution merge.py consumes -- one verdict per applied entry.

    A patched story is kept (status ``repaired``) ONLY if its scoped re-check
    passed; a re-check that failed OR has no verdict for the story (cannot
    confirm the fix) is ``recheck_failed`` and drops. A guard-failed apply never
    reaches the re-check -- there is nothing patched to re-verify -- and stays
    ``guard_failed``. Every non-``repaired`` status falls back to today's drop in
    merge, so repair is strictly a release valve: it can only rescue a story the
    checker would otherwise have dropped.
    """
    recheck_index = _index_by_article_ids(recheck)

    results = []
    for entry in applied.get("applied", []):
        ids = frozenset(entry.get("article_ids", []))
        resolution: dict[str, object] = {
            "article_ids": sorted(ids),
            "status": "guard_failed",
            "patched_fields": {},
            "recheck_pass": None,  # nosec B105 -- a re-check verdict (bool/None), not a secret; the key just contains "pass"
        }
        if entry.get("ok"):
            recheck_result = recheck_index.get(ids)
            # Missing verdict fails closed: no entry -> treat as not-passed.
            passed = recheck_result is not None and not _coherence_failed(recheck_result)
            resolution["recheck_pass"] = passed
            if passed:
                resolution["status"] = "repaired"
                resolution["patched_fields"] = entry.get("patched_fields", {})
            else:
                resolution["status"] = "recheck_failed"
        results.append(resolution)
    return {"results": results}


def append_repair_log(path: Path, event: dict) -> None:
    """Append one repair event as a JSON line to the accruing repair corpus.

    This log (data/repair_log.jsonl) is the real-world record of every repair --
    original text, checker reason, repaired text, guard/recheck outcome -- that
    turns each production run into ground truth for the eval. Creates the file
    (and parents) on first write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event) + "\n")
