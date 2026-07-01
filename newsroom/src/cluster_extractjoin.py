"""Extract→join clustering: a deterministic alternative to the holistic LLM CLUSTER stage.

Instead of one Sonnet call reading every article and grouping them, this runs a cheap
PER-ARTICLE extraction (``{entities, keywords, primary_event}``) then joins deterministically
(TF-IDF over the tag bag + agglomerative clustering). Same ``clusters.json`` schema out, so
SELECT/WRITE/COHERENCE/threads are unaffected.

Why it exists (quality, not cost): judged on real output the holistic stage over-splits big
stories, and SELECT surfaces the pieces as reader-facing duplicates (a published digest put the
same Venezuela earthquake in must_know twice). Extract→join yields cleaner, less repetitive,
DETERMINISTIC partitions (fewer internal duplicates across a 3-day task-grounded gate) and the
structured tags are reusable downstream. Validated equivalent-or-better in
``docs/2026-07-01-graph-gate-preregistration.md``; build plan in
``docs/2026-07-01-extractjoin-cluster-stage-plan.md``. This REPLACES the holistic cluster.md
agent outright (no runtime flag, by design); rollback is a code/image revert.

The pure functions (``build_extract_prompt``, ``parse_extract_items``, ``join_tags``) are
unit-tested without any model call; ``run_extractjoin_stage`` is the async pipeline entry that
does the batched extraction via the Agent SDK and returns a ``run_usage`` row.
"""

from __future__ import annotations

import csv
import json
import logging
import re
import time
from collections import Counter
from functools import partial
from pathlib import Path
from typing import Any

import claude_cli
import usage
from claude_agent_sdk import ThinkingConfig
from retry import with_retry_async

logger = logging.getLogger(__name__)


def _thinking_for(model: str) -> ThinkingConfig | None:
    """thinking=disabled restores the proven-good clustering behaviour on the 4.x family
    (Sonnet 4.6, Haiku 4.5), but Sonnet 5 / Opus 4.8 / Fable 5 have always-on thinking and
    400 on ``disabled`` -- omit it there (SDK default = adaptive). Same model-agnosticism
    reason ``effort`` is left unset; keeps CLUSTER_EXTRACT_MODEL swappable to a next-gen model
    (the documented Sonnet-5 direction) without every batch 400ing into a degenerate partition.
    """
    if model.startswith(("claude-sonnet-4", "claude-haiku-4")):
        return {"type": "disabled"}
    return None


# Reject the whole stage if more than this fraction of articles fall back to title-only tags:
# that means extraction is broken (auth/outage/refusal/prompt drift), and a title-only partition
# is near-degenerate (all singletons) -- better to fail the run (cron retries) than ship it. The
# healthy fallback rate is ~0 (the gate runs saw 0/498), so this only trips on real breakage.
_MAX_FALLBACK_FRACTION = 0.25
# Wall-clock budget for the whole extraction's transient-overload retries (mirrors the per-stage
# budget in orchestrate); shared across batches so a real outage is ridden out, then bounded.
_EXTRACT_RETRY_BUDGET_S = 14400
# sklearn TfidfVectorizer's default token_pattern: a doc with no 2+-char word token vectorizes to
# all-zeros, which collapses/merges spuriously -- such docs get a unique sentinel instead.
_TOKEN_RE = re.compile(r"\b\w\w+\b")

# Per-article extraction rubric. Mirrors the gate-validated scratch prompt: the primary_event
# phrase is the load-bearing join signal (not generic entities), and same-story articles must
# get matching entities + primary_event.
EXTRACT_SYSTEM = """You extract clustering metadata from news articles. For EACH input article, output:
- entities: 3-8 canonical named entities CENTRAL to the article (people, organizations, places, products, named events). Use the most common canonical form (e.g. "Donald Trump", not "Trump"/"the president").
- keywords: 3-8 salient lowercase topic terms (not entities) that characterize the specific story.
- primary_event: ONE short specific phrase naming the underlying story this article is about -- the kind of label you'd give the cluster it belongs to (e.g. "US-Iran interim peace deal congressional scrutiny", NOT "politics" or "Middle East").

Be specific and consistent: two articles about the SAME story must get the SAME entities and a matching primary_event phrase. Distinguish sub-stories (e.g. "Iran nuclear talks" vs "Iran oil market impact" are different primary_events even though they share entities).

Respond IMMEDIATELY with ONLY a JSON object, no prose, no markdown fence, one item per input article in input order:
{"items": [{"article_id": "A1", "entities": ["..."], "keywords": ["..."], "primary_event": "..."}]}"""

_EXTRACT_BATCH = 40
_SUMMARY_CAP = 300  # chars of summary shown per article (title carries most of the signal)


# --------------------------------------------------------------------------- #
# Pure helpers (unit-testable, no model calls).
# --------------------------------------------------------------------------- #
def load_articles(claude_input_dir: Path) -> dict[str, dict[str, str]]:
    """{article_id: {title, summary, source_id}} from every articles_*.csv in the dir."""
    arts: dict[str, dict[str, str]] = {}
    for path in sorted(claude_input_dir.glob("articles_*.csv")):
        with path.open(encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                aid = row.get("article_id")
                if aid:
                    arts[aid] = {
                        "title": row.get("title", ""),
                        "summary": row.get("summary", ""),
                        "source_id": row.get("source_id", ""),
                    }
    return arts


def build_extract_prompt(batch: list[str], arts: dict[str, dict[str, str]]) -> str:
    """TSV of (article_id, title, summary) for one extraction batch."""
    rows = ["article_id\ttitle\tsummary"]
    for aid in batch:
        a = arts[aid]
        title = a["title"].replace("\n", " ").replace("\t", " ")
        summ = a["summary"].replace("\n", " ").replace("\t", " ")[:_SUMMARY_CAP]
        rows.append(f"{aid}\t{title}\t{summ}")
    return "Extract clustering metadata for these articles:\n\n" + "\n".join(rows)


def parse_extract_items(text: str) -> list[dict]:
    """Pull the items array from a model response, tolerant of prose/fences.

    Tries the ``{"items": [...]}`` object first, then a bare ``[...]`` array. Returns [] if
    neither parses -- the caller then title-only-fallbacks the affected articles.
    """
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        try:
            obj = json.loads(text[start : end + 1])
        except ValueError:
            obj = None
        if isinstance(obj, dict) and isinstance(obj.get("items"), list):
            return obj["items"]
    start, end = text.find("["), text.rfind("]")
    if 0 <= start < end:
        try:
            arr = json.loads(text[start : end + 1])
            if isinstance(arr, list):
                return arr
        except ValueError:
            pass
    return []


def _tag_bag(tag: dict) -> str:
    """Term bag for one article: entities x3 (strongest same-story signal), primary_event x2.

    Returns "" when the article has no usable tags -- the caller then substitutes a per-article
    unique token so tagless articles stay singletons instead of collapsing into one junk blob
    (all-identical bags → cosine distance 0 → forced merge).
    """
    ents = [e.lower() for e in tag.get("entities", [])]
    kws = [k.lower() for k in tag.get("keywords", [])]
    pe = str(tag.get("primary_event", "")).lower().strip()
    parts = ents * 3 + kws + ([pe] * 2 if pe else [])
    return " ".join(p for p in parts if p.strip())


def _time_kernel(article_ids: list[str], published: dict, sigma_hours: float):
    """Gaussian temporal-proximity weight matrix K[i,j] = exp(-dt^2 / (2*sigma^2)), dt in hours.

    Same-story articles published close in time weigh ~1; the weight decays as they drift apart,
    so the combined similarity (tag-cosine * K) resists merging same-entity DIFFERENT-time stories
    (a recurring actor's separate events). An article with no publish time gets dt=0 to everyone
    (neutral weight 1) so it clusters on tags alone rather than being spuriously isolated.
    """
    import numpy as np

    n = len(article_ids)
    # Hours-from-epoch per article; NaN where unknown (treated as neutral below).
    hours = np.full(n, np.nan)
    for i, aid in enumerate(article_ids):
        ts = published.get(aid)
        if ts is not None:
            hours[i] = ts.timestamp() / 3600.0
    dt = np.abs(hours[:, None] - hours[None, :])
    dt = np.where(np.isnan(dt), 0.0, dt)  # unknown gap -> no penalty
    return np.exp(-(dt**2) / (2.0 * sigma_hours**2))


def join_tags(
    article_ids: list[str],
    tags: dict[str, dict],
    *,
    threshold: float,
    published: dict | None = None,
    sigma_hours: float | None = None,
) -> list[dict]:
    """Deterministically group articles by their extracted tags into clusters.

    TF-IDF over the per-article tag bag → agglomerative clustering (cosine, average linkage) at
    ``threshold``. Returns ``[{"story", "article_ids"}]``; ``story`` is the cluster's modal
    primary_event. Every input id appears in exactly one cluster (the invariant SELECT relies on).
    Threshold 0.80 is the held-out value from runs 204/205 (see the gate doc); the granularity-
    matching threshold rises with corpus size, so revisit if article counts shift materially.

    When ``published`` (``{article_id: datetime}``) AND ``sigma_hours`` are both given, the join
    additionally weighs each pair's tag-cosine similarity by a Gaussian temporal-proximity kernel
    (:func:`_time_kernel`) before clustering on the combined distance ``1 - sim*K`` -- so a lower
    threshold is needed to reach the same granularity (the kernel only shrinks similarity). Omit
    both (the default) for the exact prod behaviour, byte-for-byte.
    """
    if not article_ids:
        return []
    if len(article_ids) == 1:
        aid = article_ids[0]
        story = tags.get(aid, {}).get("primary_event") or "cluster 1"
        return [{"story": story, "article_ids": [aid]}]

    from sklearn.cluster import AgglomerativeClustering
    from sklearn.feature_extraction.text import TfidfVectorizer

    # A tagless article gets a unique token so it stays a singleton (never merges on emptiness).
    # Guard on TOKEN presence, not string truthiness: a bag like "u.s." is non-empty but
    # tokenizes to nothing under sklearn's pattern, which would otherwise yield an all-zero row.
    docs: list[str] = []
    for i, aid in enumerate(article_ids):
        bag = _tag_bag(tags.get(aid, {}))
        docs.append(bag if _TOKEN_RE.search(bag) else f"notags{i}")
    X = TfidfVectorizer().fit_transform(docs).toarray()

    if published and sigma_hours:
        import numpy as np
        from sklearn.metrics.pairwise import cosine_similarity

        # Combined distance = 1 - (tag-cosine similarity * temporal-proximity weight). With no
        # temporal signal this reduces to 1 - cosine == sklearn's metric="cosine", so the branch
        # below stays the identical clustering; here the kernel only ever shrinks similarity.
        sim = cosine_similarity(X) * _time_kernel(article_ids, published, sigma_hours)
        dist = np.clip(1.0 - sim, 0.0, None)
        np.fill_diagonal(dist, 0.0)
        labels = (
            AgglomerativeClustering(
                n_clusters=None, distance_threshold=threshold, metric="precomputed", linkage="average"
            )
            .fit(dist)
            .labels_
        )
    else:
        labels = (
            AgglomerativeClustering(n_clusters=None, distance_threshold=threshold, metric="cosine", linkage="average")
            .fit(X)
            .labels_
        )

    groups: dict[int, list[str]] = {}
    for aid, lab in zip(article_ids, labels, strict=True):
        groups.setdefault(int(lab), []).append(aid)

    clusters: list[dict] = []
    for members in groups.values():
        events = [tags[a].get("primary_event", "") for a in members if tags.get(a, {}).get("primary_event")]
        story = Counter(events).most_common(1)[0][0] if events else f"cluster {len(clusters) + 1}"
        clusters.append({"story": story, "article_ids": members})
    return clusters


def _merge_usage(rows: list[dict]) -> dict:
    """Sum raw-SDK usage dicts across extraction batches into one usage_row_from_sdk input."""
    keys = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    return {k: sum(r.get(k, 0) for r in rows) for k in keys}


# --------------------------------------------------------------------------- #
# Async stage entry.
# --------------------------------------------------------------------------- #
async def run_extractjoin_stage(
    claude_input_dir: Path,
    *,
    model: str,
    cwd: str | Path | None,
    threshold: float,
    batch_size: int = _EXTRACT_BATCH,
) -> dict[str, Any]:
    """Produce clusters.json via extract→join and return a ``run_usage`` row.

    Batched per-article extraction (SDK, ``model``) → deterministic join. Each batch retries
    transient overloads with backoff (``with_retry_async``, parity with the other stages); a
    batch that still fails, or returns unparseable/empty items, title-only-falls-back its
    articles. The stage then RAISES if the title-only fallback exceeds
    ``_MAX_FALLBACK_FRACTION`` of all articles -- gating on ACTUAL article coverage, not batch
    exceptions, so an extractor that "succeeds" but returns garbage/empty for every batch cannot
    silently ship a degenerate (all-singleton) partition. On raise, the run fails and the next
    cron run retries.
    """
    arts = load_articles(claude_input_dir)
    ids = list(arts.keys())
    if not ids:
        raise ValueError("extract-join: no articles found (articles_*.csv missing/empty)")

    async def _extract(prompt: str) -> claude_cli.StageResult:
        # Mechanical single-shot JSON extraction: no tools (no file I/O, unlike the other
        # stages), one turn. thinking and effort are both left MODEL-AGNOSTIC: effort unset
        # (Haiku 4.5 400s on it) and thinking chosen per model (_thinking_for -- next-gen
        # models 400 on thinking=disabled), so CLUSTER_EXTRACT_MODEL can swap 4.6<->Haiku<->
        # Sonnet 5 without a per-batch 400 collapsing the run into a degenerate partition.
        result = await claude_cli.run_agent(
            prompt,
            model=model,
            system_prompt=EXTRACT_SYSTEM,
            tools=[],
            max_turns=1,
            cwd=cwd,
            thinking=_thinking_for(model),
        )
        if not result.ok:  # non-success (incl. the subtype=success+is_error API-fail trap): retryable
            raise RuntimeError(result.error_summary())
        return result

    tags: dict[str, dict] = {}
    usage_rows: list[dict] = []
    total_cost = 0.0
    batches = [ids[i : i + batch_size] for i in range(0, len(ids), batch_size)]
    deadline = time.monotonic() + _EXTRACT_RETRY_BUDGET_S  # shared across batches: ride out an outage, bounded
    for n, batch in enumerate(batches, 1):
        prompt = build_extract_prompt(batch, arts)
        try:
            result = await with_retry_async(partial(_extract, prompt), label="cluster-extract", deadline=deadline)
        except (RuntimeError, ValueError) as e:
            logger.warning("extract-join batch %d/%d failed after retries, title-fallback: %s", n, len(batches), e)
            continue
        usage_rows.append(result.usage)
        total_cost += result.total_cost_usd
        added = 0
        for item in parse_extract_items(result.text):
            aid = item.get("article_id")
            if aid in arts and aid not in tags:
                tags[aid] = {
                    "entities": [str(x) for x in (item.get("entities") or [])],
                    "keywords": [str(x) for x in (item.get("keywords") or [])],
                    "primary_event": str(item.get("primary_event") or ""),
                }
                added += 1
        if added < len(batch):  # a "successful" batch that returned garbage/short is a corruption signal
            logger.warning(
                "extract-join batch %d/%d: %d/%d articles extracted (%d unparsed/mis-keyed)",
                n,
                len(batches),
                added,
                len(batch),
                len(batch) - added,
            )

    # Gate on ACTUAL coverage, not batch failures: an ok-but-empty extractor drops here.
    missing = [a for a in ids if a not in tags]
    if len(missing) > len(ids) * _MAX_FALLBACK_FRACTION:
        raise RuntimeError(
            f"extract-join: {len(missing)}/{len(ids)} articles ({len(missing) / len(ids):.0%}) fell back "
            f"to title-only (> {_MAX_FALLBACK_FRACTION:.0%}) -- refusing to ship a degenerate partition"
        )
    for aid in missing:  # a minority fall back to title-only so coverage stays 100%
        tags[aid] = {"entities": [], "keywords": [], "primary_event": arts[aid]["title"][:60]}
    if missing:
        logger.warning("extract-join: %d/%d articles title-only fallback", len(missing), len(ids))

    clusters = join_tags(ids, tags, threshold=threshold)
    out = {"clusters": clusters}
    (claude_input_dir / "clusters.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    logger.info(
        "extract-join: %d articles -> %d clusters (thr %.2f, $%.4f)", len(ids), len(clusters), threshold, total_cost
    )

    return usage.usage_row_from_sdk("cluster", model, _merge_usage(usage_rows), total_cost)
