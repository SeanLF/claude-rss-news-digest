"""Cohesion gate: one judgement over the SELECTED clusters, between SELECT and WRITE.

The join groups articles on tag overlap and labels a cluster by majority vote over its
members' ``primary_event`` tags; nothing in the pipeline ever reasons "one event or two".
About a quarter of shipped clusters bundle a second event (docs/2026-08-01-cluster-junk-
drawer-findings.md), and the per-story WRITE, handed the whole cluster, writes both -- run
285 led a brief with a White House helipad because the cluster held it.

This module asks that one question, for the ~17 clusters that will ship, and writes
``cluster_cohesion.json``. ``write_fanout.build_branches`` reads it and hands WRITE the
dominant event's articles. Fail-open at every layer: a failed call, an unparseable reply,
a non-partition, or a dominant group that drops every id SELECT cited each leave that
cluster exactly as it is today, with the reason recorded. The gate only splits; it never
merges clusters or moves an article between stories.

Layering: same tier as cluster_extractjoin (imports claude_cli and usage). write_fanout stays
a leaf and reads the artifact as a file.
"""

from __future__ import annotations

import asyncio
import csv
import json
import logging
import time
from pathlib import Path
from typing import Any

import claude_cli
import usage
import write_fanout
from claude_agent_sdk import ThinkingConfig

logger = logging.getLogger(__name__)

COHESION_ARTIFACT = "cluster_cohesion.json"
TIERS = ("must_know", "should_know")
_SNIPPET_CHARS = 200
_GROUPS_PER_CALL = 12
_CONCURRENCY = 4

JUDGE_SYSTEM = """You are auditing a news-clustering system. Each GROUP below is a set of articles the \
system decided cover THE SAME news story. Some groups are right; some bundle a second, unrelated \
event that merely shares a place, a person, an organisation or a topic with the story.

Each GROUP header names the story the system selected that group for. For each group, partition \
its articles into EVENTS, and list that story's event FIRST -- the articles about the named story -- \
then every other event.

Rules:
- Different angles, reactions, analysis, follow-ups or later developments of ONE underlying event \
are ONE event (a strike, the market reaction to it, and an opinion piece about it = 1).
- Articles about events that merely share a place, a country, a person, an organisation or a topic \
are DIFFERENT events (a typhoon and a company's earnings that both happen in Hong Kong = 2; a \
lawsuit over AI rules and a helipad at the same building = 2).
- Judge only from the titles and snippets given. Do not guess at content you cannot see.
- Every article id appears in exactly one event. Use the ids exactly as given.

Return ONLY a JSON object, no prose:
{"results": [{"group": <int>, "events": [["<id>", "<id>"], ["<id>"]]}]}"""


def _thinking_for(model: str) -> ThinkingConfig | None:
    """Same policy as cluster_extractjoin: disabled for the 4.x family, SDK default otherwise."""
    if model.startswith(("claude-sonnet-4", "claude-haiku-4")):
        return {"type": "disabled"}
    return None


# --------------------------------------------------------------------------- #
# Pure pieces.
# --------------------------------------------------------------------------- #


def load_articles(claude_input_dir: Path) -> dict[str, dict[str, str]]:
    """id -> {title, summary} from every articles_*.csv. Only what the judge is shown."""
    out: dict[str, dict[str, str]] = {}
    for path in sorted(claude_input_dir.glob("articles_*.csv")):
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            if not header or "article_id" not in header:
                continue
            id_col = header.index("article_id")
            title_col = header.index("title") if "title" in header else None
            summary_col = header.index("summary") if "summary" in header else None
            for row in reader:
                if len(row) <= id_col:
                    continue
                out[row[id_col]] = {
                    "title": row[title_col] if title_col is not None and len(row) > title_col else "",
                    "summary": row[summary_col] if summary_col is not None and len(row) > summary_col else "",
                }
    return out


def build_judge_prompt(groups: list[dict], articles: dict[str, dict]) -> str:
    """One GROUP block per selected cluster: id, title, and a short snippet. Nothing else."""
    parts: list[str] = []
    for g in groups:
        ids = g["article_ids"]
        story = g.get("story")
        header = f"\nGROUP {g['group']} ({len(ids)} articles)"
        parts.append(f"{header} -- selected as: {story}:" if story else f"{header}:")
        for aid in ids:
            art = articles.get(aid, {})
            title = (art.get("title") or "").strip()
            snippet = " ".join((art.get("summary") or "").split())[:_SNIPPET_CHARS]
            line = f"  - {aid}: {title}"
            if snippet:
                line += f" -- {snippet}"
            parts.append(line)
    return "\n".join(parts).strip() + "\n"


def _json_object(text: str) -> dict | None:
    start, end = text.find("{"), text.rfind("}")
    if not (0 <= start < end):
        return None
    try:
        obj = json.loads(text[start : end + 1])
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def parse_verdicts(text: str) -> dict[int, list[list[str]]]:
    """group number -> events. {} when the reply holds no usable object."""
    obj = _json_object(text or "")
    if obj is None or not isinstance(obj.get("results"), list):
        return {}
    out: dict[int, list[list[str]]] = {}
    for r in obj["results"]:
        if not isinstance(r, dict) or not isinstance(r.get("group"), int):
            continue
        events = r.get("events")
        if not isinstance(events, list):
            continue
        clean = [[i for i in ev if isinstance(i, str)] for ev in events if isinstance(ev, list)]
        out[r["group"]] = clean
    return out


def validate_partition(article_ids: list[str], events: list[list[str]]) -> list[list[str]] | None:
    """The events in the judge's order when they partition article_ids exactly; else None.

    The order is information: the judge is asked to list the selected story's event first.
    Size is not -- on the 2026-09-03 replay, largest-first made Putin's Ukraine remarks
    outrank the Iran-support story SELECT chose (docs/2026-09-03-clustering-pocs.md).
    """
    expected = set(article_ids)
    seen: list[str] = [i for ev in events for i in ev]
    if not events or any(not ev for ev in events):
        return None
    if len(seen) != len(set(seen)) or set(seen) != expected:
        return None
    return [list(ev) for ev in events]


def _selected_stories(selected: dict) -> list[dict]:
    return [s for tier in TIERS for s in (selected.get(tier) or []) if isinstance(s, dict)]


def selected_groups(selected: dict, clusters: list[dict]) -> list[dict]:
    """One GROUP per selected story: the cluster's ids unioned with SELECT's citations, in
    the same order build_branches uses, so the judge sees what WRITE would see."""
    groups: list[dict] = []
    for n, story in enumerate(_selected_stories(selected)):
        ci = story.get("cluster_index")
        cited = [i for i in (story.get("article_ids") or []) if isinstance(i, str)]
        # The same resolution as the fan-out: the citations decide, the index only breaks a
        # total miss. A verdict keyed on a drifted index would never be applied.
        resolved = write_fanout.resolve_cluster_index(clusters, ci, cited)
        if resolved is not None:
            ci = resolved
        cluster_ids: list[str] = []
        if isinstance(ci, int) and 0 <= ci < len(clusters):
            entry = clusters[ci]
            ids = entry.get("article_ids") if isinstance(entry, dict) else None
            if isinstance(ids, list):
                cluster_ids = [i for i in ids if isinstance(i, str)]
        ids = list(dict.fromkeys([*cluster_ids, *cited]))
        if len(ids) < 2 or not isinstance(ci, int):
            continue  # nothing to partition, or nothing to key the verdict on
        label = clusters[ci].get("story") if 0 <= ci < len(clusters) and isinstance(clusters[ci], dict) else None
        story_label = label if isinstance(label, str) else None
        groups.append({"group": n, "cluster_index": ci, "article_ids": ids, "cited": cited, "story": story_label})
    return groups


def judge_selected(
    selected: dict,
    clusters: list[dict],
    verdicts_by_group: dict[int, list[list[str]]],
    groups: list[dict],
) -> dict:
    """Turn raw verdicts into the artifact document. No I/O, no model."""
    # SELECT's citations for THIS group's story (a group number is the story's position):
    # the tie-breaker and the "drops every cited id" guard. Two stories can share a
    # cluster_index and cite different articles, so never key this on the cluster.
    stories = _selected_stories(selected)
    verdicts: list[dict] = []
    split = strays_removed = 0
    for g in groups:
        ids = g["article_ids"]
        cited = g.get("cited")
        if cited is None:
            n = g["group"]
            story = stories[n] if 0 <= n < len(stories) else {}
            cited = [i for i in (story.get("article_ids") or []) if isinstance(i, str)]
        entry: dict[str, Any] = {
            # The story's position in SELECT's order: the fan-out's branch index, and the
            # key a verdict is applied by. Two stories can share a cluster_index.
            "group": g["group"],
            "cluster_index": g["cluster_index"],
            "article_ids": ids,
            "events": None,
            "dominant": ids,
            "strays": [],
            "applied": False,
            "reason": None,
        }
        raw = verdicts_by_group.get(g["group"])
        events = validate_partition(ids, raw) if raw is not None else None
        if raw is None:
            entry["reason"] = "no verdict"
        elif events is None:
            entry["reason"] = "not a partition"
        elif len(events) == 1:
            entry["events"] = events
            entry["reason"] = "one event"
        else:
            entry["events"] = events
            # The judge lists the selected story's event first. SELECT's citations are the
            # other authority on what the story is: the dominant is the first listed event
            # that holds at least one of them, so WRITE never writes from articles SELECT
            # did not cite. No event holds any: refuse, and the branch stays as it is.
            dominant = None
            if cited:
                dominant = next((ev for ev in events if any(i in ev for i in cited)), None)
            else:
                dominant = events[0]
            if dominant is None:
                entry["reason"] = "dominant drops every cited id"
            else:
                entry["dominant"] = dominant
                entry["strays"] = [i for i in ids if i not in dominant]
                entry["applied"] = True
                split += 1
                strays_removed += len(entry["strays"])
        verdicts.append(entry)
    return {"judged": len(groups), "split": split, "strays_removed": strays_removed, "verdicts": verdicts}


# --------------------------------------------------------------------------- #
# The stage.
# --------------------------------------------------------------------------- #


def _write_artifact(claude_input_dir: Path, doc: dict) -> None:
    (claude_input_dir / COHESION_ARTIFACT).write_text(json.dumps(doc, indent=2), encoding="utf-8")


def write_skipped(claude_input_dir: Path) -> None:
    """The artifact for a run with the gate off, so its absence never reads as a failure."""
    _write_artifact(
        claude_input_dir,
        {"model": None, "outcome": "skipped", "judged": 0, "split": 0, "strays_removed": 0, "verdicts": []},
    )


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


async def run_cohesion_stage(claude_input_dir: Path, *, model: str, cwd: str | Path | None) -> dict:
    """Judge the selected clusters, write the artifact, return a run_usage row.

    Fail-open: any failure writes an artifact with ``outcome: failed`` and no applied verdict,
    logs it, and still returns a (possibly zero) usage row so the spend reaches run_usage.
    """
    stage_start = time.monotonic()
    usage_rows: list[dict] = []
    total_cost = 0.0
    doc: dict = {"model": model, "outcome": "failed", "judged": 0, "split": 0, "strays_removed": 0, "verdicts": []}
    try:
        selected = _load_json(claude_input_dir / "selected.json")
        clusters_doc = _load_json(claude_input_dir / "clusters.json")
        clusters = clusters_doc.get("clusters") if isinstance(clusters_doc, dict) else None
        if not isinstance(selected, dict) or not isinstance(clusters, list):
            raise ValueError("selected.json or clusters.json malformed")
        articles = load_articles(claude_input_dir)
        groups = selected_groups(selected, clusters)
        sem = asyncio.Semaphore(_CONCURRENCY)
        failed_batches: list[str] = []

        async def _judge(batch: list[dict]) -> dict[int, list[list[str]]]:
            """One batch's verdicts. A failed batch is its own loss: its groups read
            'no verdict' and every other batch still applies."""
            nonlocal total_cost
            try:
                async with sem:
                    result = await claude_cli.run_agent(
                        build_judge_prompt(batch, articles),
                        model=model,
                        system_prompt=JUDGE_SYSTEM,
                        tools=[],
                        max_turns=1,
                        cwd=cwd,
                        thinking=_thinking_for(model),
                    )
                if not result.ok:
                    raise RuntimeError(result.error_summary())
            except Exception as e:
                failed_batches.append(f"{type(e).__name__}: {e}")
                logger.error("cohesion: batch of %d group(s) failed open (%s: %s)", len(batch), type(e).__name__, e)
                return {}
            usage_rows.append(result.usage or {})
            total_cost += result.total_cost_usd or 0.0
            return parse_verdicts(result.text)

        batches = [groups[i : i + _GROUPS_PER_CALL] for i in range(0, len(groups), _GROUPS_PER_CALL)]
        verdicts: dict[int, list[list[str]]] = {}
        for part in await asyncio.gather(*(_judge(b) for b in batches)):
            verdicts.update(part)
        # failed: no batch answered (nothing applied); partial: some did; completed: all did.
        outcome = "completed"
        if batches and len(failed_batches) == len(batches):
            outcome = "failed"
        elif failed_batches:
            outcome = "partial"
        doc = {"model": model, "outcome": outcome, **judge_selected(selected, clusters, verdicts, groups)}
        if failed_batches:
            doc["reason"] = "; ".join(failed_batches)
        logger.info(
            "cohesion: %d selected cluster(s) judged, %d split, %d stray article(s) removed ($%.4f)",
            doc["judged"],
            doc["split"],
            doc["strays_removed"],
            total_cost,
        )
    except Exception as e:
        doc["reason"] = f"{type(e).__name__}: {e}"
        logger.error("cohesion: gate failed open -- WRITE sees the clusters unchanged: %s", doc["reason"])
    _write_artifact(claude_input_dir, doc)
    merged = {
        k: sum(r.get(k, 0) for r in usage_rows)
        for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    }
    return usage.usage_row_from_sdk(
        "cohesion",
        model,
        merged,
        total_cost,
        duration_ms=int((time.monotonic() - stage_start) * 1000),
        thinking=_thinking_for(model),
        effort=None,
    )
