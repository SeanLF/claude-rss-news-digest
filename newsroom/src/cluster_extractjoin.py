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

import asyncio
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
    """Per-model thinking config for the extraction call: ``disabled`` for the 4.x family
    (Sonnet 4.6, Haiku 4.5), omitted (SDK default = adaptive) for next-gen models
    (Sonnet 5 / Opus 4.8 / Fable 5).

    Originally a hard-400 workaround: next-gen models used to REJECT ``thinking=disabled``, so a
    ``CLUSTER_EXTRACT_MODEL=<next-gen>`` build would 400 every batch into a degenerate partition.
    As of claude-agent-sdk 0.2.110 that 400 no longer reproduces (verified live by ``bin/sdk-canary``),
    but the split is RETAINED deliberately as CONFIG POLICY, not a 400 dodge: adaptive is the
    validated config for next-gen (the S5 extraction sweep; and on WRITE, forcing ``disabled`` on
    S5 induced a self-revision rewrite pathology). ``effort`` is likewise left unset -- Haiku 4.5
    used to 400 on it (also no longer reproduces on 0.2.110 per ``bin/sdk-canary``), and the
    extraction call has no reason to spend on effort -- keeping CLUSTER_EXTRACT_MODEL swappable
    across 4.6 / Haiku / next-gen. Re-run ``bin/sdk-canary`` after an SDK bump to re-check both.
    """
    if model.startswith(("claude-sonnet-4", "claude-haiku-4")):
        return {"type": "disabled"}
    return None


# Reject the whole stage if more than this fraction of articles fall back to title-only tags:
# that means extraction is broken (auth/outage/refusal/prompt drift), and a title-only partition
# is near-degenerate (all singletons) -- better to fail the run than ship it. Failing is fail-CLOSED
# (no digest ships), but recovery is not automatic: the systemd unit is once/day, so it means no
# digest until the next scheduled run or a manual `--resume`. The healthy fallback rate is ~0 (the
# gate runs saw 0/498), so this only trips on real breakage.
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
# The ~13 per-batch extraction calls are independent, so they run concurrently (bounded) rather
# than serially -- the single longest wall-clock chunk of the stage. Precedent: scratch/tg_parallel
# ran 6-12 concurrent chains on one OAuth token with 0 rate-limit failures; 4 is well within that.
# Each batch keeps its own with_retry_async, so a 429 under concurrency is still backed off.
_EXTRACT_CONCURRENCY = 4
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
            return [it for it in obj["items"] if isinstance(it, dict)]
    start, end = text.find("["), text.rfind("]")
    if 0 <= start < end:
        try:
            arr = json.loads(text[start : end + 1])
            if isinstance(arr, list):
                return [it for it in arr if isinstance(it, dict)]
        except ValueError:
            pass
    return []


def _write_cluster_health(
    claude_input_dir: Path, *, articles: int, title_only_fallback: int, batches_lost: int
) -> None:
    """Record this stage's degradation counts where an invariant can read them.

    Best-effort: a failure here must never cost the digest. It is observability for a run that
    otherwise completes and ships, which is exactly the failure class run_health exists for --
    every one of the 7 archived degraded runs exited 0 with nothing but a log line to show for
    it, and that log lives in a 100 KB rotating file.
    """
    try:
        (claude_input_dir / "cluster_health.json").write_text(
            json.dumps(
                {
                    "articles": articles,
                    "title_only_fallback": title_only_fallback,
                    "batches_lost": batches_lost,
                }
            )
        )
    except OSError as e:
        logger.warning("could not write cluster_health.json (non-fatal): %s", e)


def coerce_tag(item: dict) -> dict:
    """The tag dict for one raw extraction item, with every value normalised to a string.

    ``_tag_bag`` lowercases entities and keywords directly, so it assumes normalised input --
    an assumption that held only while the fold was its sole feeder. Model JSON is not
    normalised: ``"entities": null`` raises TypeError and ``"entities": [2026]`` raises
    AttributeError, and neither is caught by ``_run_batch``, so one malformed item out of ~688
    would escape a ``return_exceptions=False`` gather and cost the run its digest.

    Sharing one coercion between the usable-check and the fold also makes them agree by
    construction rather than by coincidence: ``str(None)`` is the truthy token "None", so an
    uncoerced ``"primary_event": null`` reads as usable to the check while the coverage gate
    reads it as tagless -- exactly the silent wholesale loss the check exists to catch.
    """
    return {
        "entities": [str(x) for x in (item.get("entities") or [])],
        "keywords": [str(x) for x in (item.get("keywords") or [])],
        "primary_event": str(item.get("primary_event") or ""),
    }


def _duplicate_count(items: list[dict], batch: list[str]) -> int:
    """How many in-batch items repeat an id already seen -- counted separately from mis-keys.

    A response repeating one id 40 times and a response about 40 other articles both lose the
    batch, but one is a generation loop and the other an id-handling fault. Reporting them as a
    single number sends the reader hunting the wrong bug, which is the failure the split logging
    exists to prevent.
    """
    wanted, seen, dupes = set(batch), set(), 0
    for it in items:
        aid = it.get("article_id")
        if aid in wanted:
            dupes += aid in seen
            seen.add(aid)
    return dupes


def items_for_batch(items: list[dict], batch: list[str]) -> list[dict]:
    """The items a batch's response may key: ``article_id`` must be one THIS batch asked about.

    Scoping to the batch (not the whole corpus) is a CORRECTNESS guard, not bookkeeping. A model
    that renumbers its output -- answering A401..A440 with "A1".."A40" -- would otherwise write one
    batch's tags onto another batch's ARTICLES whenever the other batch produced nothing itself, and
    do it silently: the count still equals the batch size, so no warning fires and the digest ships
    with 40 articles clustered on metadata extracted from 40 different articles.

    Duplicates are dropped first-seen, so the returned length is the number of DISTINCT articles the
    response actually covered -- a response repeating one id 40 times covered one article, not 40.
    """
    wanted, seen = set(batch), set()
    out = []
    for it in items:
        aid = it.get("article_id")
        if aid in wanted and aid not in seen:
            seen.add(aid)
            out.append(it)
    return out


def response_snippet(text: str, keep: int = 200) -> str:
    """Head+tail of a model response, whitespace-collapsed, for the zero-yield log line.

    The raw extraction response reaches nothing anyone reads: ``run_artifacts`` keeps the inputs
    and ``clusters.json``, never the model's text. (It does persist in the SDK session JSONL in
    the ``news-digest-claude`` volume, but no code reads that and it is awkward to reach from a
    one-shot container.) So when a batch comes back unusable this snippet is the evidence that
    actually lands where someone will see it -- and it is what distinguishes a prose preamble
    from a truncated body from a refusal. Bounded on both ends so a 3k-token response cannot
    flood the log.
    """
    flat = " ".join(text.split())
    if keep <= 0 or len(flat) <= 2 * keep:
        # keep<=0 would make flat[-keep:] the WHOLE string plus a lying "elided" label.
        return flat
    return f"{flat[:keep]} ...[{len(flat) - 2 * keep} chars elided]... {flat[-keep:]}"


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
    # Fold stray same-label clusters -- but ONLY on the non-temporal (prod) path. The temporal kernel
    # intentionally separates same-tag recurring events that share a modal label (distinct installments
    # of one topic), so folding there would undo that precision. Without the kernel, same-tag articles
    # always merge, so a same-label split is a tag-disjoint fragment worth folding (the run-235 fix).
    if published and sigma_hours:
        return clusters
    return _merge_same_story(clusters)


# A cluster this small that shares a modal label with a larger one is treated as a fragment of it
# (e.g. run 235's lone "Iran FM visits Qatar" article vs the 58-article Iran cluster) and folded in.
_STRAY_ABSORB_MAX = 2


def _merge_same_story(clusters: list[dict], *, absorb_max: int = _STRAY_ABSORB_MAX) -> list[dict]:
    """Fold stray clusters into a same-``story`` anchor so the label stays a usable identity key.

    The join separates on the full tag bag, but ``story`` is the *modal* primary_event -- a lossy
    derivative -- so two tag-disjoint clusters can collide on the label. Everything downstream keys on
    that label as a unique story id (``cluster_id``; thread linking; ``digest.attach_thread_context``'s
    by-story lookup, where a collision renders two stories with the SAME thread summary -- the run-235
    identical-card bug). We restore uniqueness conservatively: for each duplicated label the largest
    cluster is the anchor (ties resolve to the first-occurring, deterministically) and every sibling with
    ``<= absorb_max`` articles (a fragment) folds into it. Any *substantial* (> absorb_max) sibling is
    LEFT separate -- force-merging distinct multi-article stories is worse than the collision, which the
    render layer guards against instead -- so a label with N substantial clusters still emits N of them.
    Original order and the every-article-exactly-once partition are preserved.
    """
    positions: dict[str, list[int]] = {}
    for i, c in enumerate(clusters):
        positions.setdefault(c["story"], []).append(i)
    anchor_of = {
        story: max(idxs, key=lambda i: len(clusters[i]["article_ids"]))
        for story, idxs in positions.items()
        if len(idxs) > 1
    }
    if not anchor_of:
        return clusters

    out: list[dict] = []
    for i, c in enumerate(clusters):
        story = c["story"]
        if story not in anchor_of:
            out.append(c)
        elif i == anchor_of[story]:
            ids = list(c["article_ids"])
            seen = set(ids)
            folded = 0
            for j in positions[story]:
                if j != i and len(clusters[j]["article_ids"]) <= absorb_max:
                    for a in clusters[j]["article_ids"]:
                        if a not in seen:
                            seen.add(a)
                            ids.append(a)
                    folded += 1
            if folded:
                logger.info("cluster: folded %d stray(s) into %r (now %d articles)", folded, story, len(ids))
            out.append({"story": story, "article_ids": ids})
        elif len(c["article_ids"]) <= absorb_max:
            continue  # folded into its anchor above
        else:
            # error, not warning: two substantial clusters colliding on a modal label is an
            # unexpected upstream condition worth chasing (surfaced above routine noise). Non-fatal
            # -- they are kept separate and the render layer guards against duplicate summaries.
            logger.error(
                "cluster: two substantial clusters share label %r (this one has %d articles) -- left "
                "separate; render layer guards against duplicate thread summaries",
                story,
                len(c["article_ids"]),
            )
            out.append(c)
    return out


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
    stage_start = time.monotonic()
    arts = load_articles(claude_input_dir)
    ids = list(arts.keys())
    if not ids:
        raise ValueError("extract-join: no articles found (articles_*.csv missing/empty)")

    async def _extract(prompt: str) -> claude_cli.StageResult:
        # Mechanical single-shot JSON extraction: no tools (no file I/O, unlike the other
        # stages), one turn. thinking and effort are both left MODEL-AGNOSTIC: effort unset and
        # thinking chosen per model (_thinking_for). Both used to be hard-400 guards (Haiku 400d
        # on effort, next-gen on thinking=disabled -- no longer on 0.2.110, see bin/sdk-canary),
        # now kept so CLUSTER_EXTRACT_MODEL swaps 4.6<->Haiku<->next-gen on the validated config.
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
    sem = asyncio.Semaphore(_EXTRACT_CONCURRENCY)

    async def _run_batch(n: int, batch: list[str]) -> tuple[list[dict], list[dict], float]:
        """One batch's extraction, semaphore-bounded. Returns (items, usage_dicts, cost).

        Items are already scoped to this batch (:func:`items_for_batch`); an empty list means its
        articles title-fallback -- as before, without failing the whole gather.

        A batch whose response yields ZERO usable items is re-attempted ONCE. That condition is
        invisible to ``with_retry_async``: the call SUCCEEDED, nothing raised, the response was
        merely unusable (prose, a truncated body, renumbered ids). It costs the batch's 40 articles
        their entity extraction, and the archived traces show it hitting roughly 1% of batch calls
        (7 wholesale losses in ~503 archived batch calls -- at batches 1, 2, 6 and 11, so it is not
        first-batch-specific). Re-attempting is safe to do blindly: extraction is a single-turn call
        with ``tools=[]`` that writes nothing and reads nothing, so a repeat is side-effect-free;
        one extra batch is ~1/13th of the stage's ~$0.95; and it is bounded at one so a persistently
        bad batch degrades exactly as it does today instead of looping. Usage from a discarded
        attempt is still returned -- we paid for it, so it belongs in the run's cost.
        """
        prompt = build_extract_prompt(batch, arts)
        usage_dicts: list[dict] = []
        cost = 0.0
        async with sem:
            for attempt in (1, 2):
                try:
                    result = await with_retry_async(
                        partial(_extract, prompt), label="cluster-extract", deadline=deadline
                    )
                except (RuntimeError, ValueError) as e:
                    logger.warning(
                        "extract-join batch %d/%d failed after retries, title-fallback: %s", n, len(batches), e
                    )
                    return [], usage_dicts, cost
                usage_dicts.append(result.usage)
                cost += result.total_cost_usd
                parsed = parse_extract_items(result.text)
                scoped = items_for_batch(parsed, batch)
                # "Usable" must mean what the COVERAGE GATE below means by it, not "an id
                # matched". A response can be perfectly keyed and still carry no entities,
                # keywords or primary_event -- the gate counts every one of those articles as
                # tagless, so the batch is a wholesale loss. Judged by id alone it looks full,
                # so it would be neither re-attempted nor logged: the same incident, silent.
                items = [it for it in scoped if _TOKEN_RE.search(_tag_bag(coerce_tag(it)))]
                if items:
                    if len(items) < len(batch):  # partial: real but far smaller loss, not re-attempted
                        logger.warning(
                            "extract-join batch %d/%d: %d/%d articles extracted "
                            "(%d items parsed, %d duplicate, %d not in this batch, %d empty-content)",
                            n,
                            len(batches),
                            len(items),
                            len(batch),
                            len(parsed),
                            _duplicate_count(parsed, batch),
                            len([it for it in parsed if it.get("article_id") not in batch]),
                            len(scoped) - len(items),
                        )
                    return items, usage_dicts, cost
                # Zero usable. Name WHICH failure it was: unparsed, mis-keyed and empty-content
                # need opposite fixes, so one merged counter is a log nobody can act on -- and
                # carry the response itself, the only record of what the model said.
                if scoped:
                    how = f"empty-content ({len(scoped)} items keyed to this batch, none with usable tags)"
                elif parsed:
                    how = (
                        f"mis-keyed ({len(parsed)} items parsed, none in this batch; "
                        f"ids returned: {[it.get('article_id') for it in parsed[:5]]})"
                    )
                else:
                    how = "unparsed (no JSON items in the response)"
                logger.warning(
                    "extract-join batch %d/%d: 0/%d articles extracted -- %s; %s | response: %s",
                    n,
                    len(batches),
                    len(batch),
                    how,
                    "re-attempting once" if attempt == 1 else "giving up, title-fallback",
                    response_snippet(result.text),
                )
        return [], usage_dicts, cost

    # Batches are independent -> run them concurrently (bounded), then fold results IN batch order so
    # tag assignment stays deterministic. Output (clusters.json) is identical to the serial version;
    # only wall-clock changes. gather (not TaskGroup) on purpose: default return_exceptions=False is
    # the fail-CLOSED choice -- an UNEXPECTED exception (a bug, a non-retryable SDK error, teardown
    # CancelledError) propagates and aborts the stage rather than silently degrading the partition;
    # `_run_batch` only absorbs the same (RuntimeError, ValueError) the serial loop did. Siblings are
    # left to the loop teardown, fine for this one-shot cron run (revisit with TaskGroup only if this
    # is ever wrapped in a longer-lived loop that needs deterministic sibling cancellation).
    results = await asyncio.gather(*(_run_batch(n, b) for n, b in enumerate(batches, 1)))
    # Fold in batch order. Every item is already scoped+deduped to its own batch and the batches
    # partition `ids`, so an id can be written at most once -- no cross-batch precedence to resolve.
    for items, batch_usage, batch_cost in results:
        usage_rows.extend(batch_usage)
        total_cost += batch_cost
        for item in items:
            tags[item["article_id"]] = coerce_tag(item)

    # Gate on USABLE tags, not key-presence. A batch can fail outright (article never keyed) OR a
    # "successful" batch can echo the schema with empty entities/keywords/primary_event (prompt
    # drift, a degraded/mis-swapped model). BOTH yield a tagless article that becomes a unique
    # `notags` sentinel -> a singleton -> an all-singleton degenerate partition if widespread, the
    # exact failure this guard exists to prevent. So "covered" means the extracted tag bag has a
    # join-usable token, not merely that the id came back. Everything else is fallback.
    missing = [a for a in ids if not _TOKEN_RE.search(_tag_bag(tags.get(a, {})))]
    if len(missing) > len(ids) * _MAX_FALLBACK_FRACTION:
        raise RuntimeError(
            f"extract-join: {len(missing)}/{len(ids)} articles ({len(missing) / len(ids):.0%}) fell back "
            f"to title-only (> {_MAX_FALLBACK_FRACTION:.0%}) -- refusing to ship a degenerate partition"
        )
    for aid in missing:  # a minority fall back to title-only so coverage stays 100%
        tags[aid] = {"entities": [], "keywords": [], "primary_event": arts[aid]["title"][:60]}
    if missing:
        # A shipped-but-degraded run (title-only articles cluster worse -> reader-facing dups
        # possible). Log at ERROR so it surfaces in monitoring the same day, not just debug noise.
        logger.error("extract-join: %d/%d articles title-only fallback (degraded clustering)", len(missing), len(ids))
    # Durable counterpart to that log line. A rotating 100 KB log file is not somewhere an
    # invariant can be evaluated from, which is why every one of these runs shipped unnoticed.
    _write_cluster_health(
        claude_input_dir,
        articles=len(ids),
        title_only_fallback=len(missing),
        batches_lost=sum(1 for items, _, _ in results if not items),
    )

    clusters = join_tags(ids, tags, threshold=threshold)
    out = {"clusters": clusters}
    (claude_input_dir / "clusters.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    logger.info(
        "extract-join: %d articles -> %d clusters (thr %.2f, $%.4f)", len(ids), len(clusters), threshold, total_cost
    )

    # Whole-stage wall clock (batched extraction + deterministic join), for run_usage latency.
    duration_ms = int((time.monotonic() - stage_start) * 1000)
    return usage.usage_row_from_sdk("cluster", model, _merge_usage(usage_rows), total_cost, duration_ms=duration_ms)
