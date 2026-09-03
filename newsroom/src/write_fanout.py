"""Per-story input branches for the WRITE stage, and the fan-in that follows.

Deterministic Python only: building one input directory per selected story, pointing
write.md at it, validating what a branch wrote, assembling the branches back into a single
``draft_selections.json`` in SELECT's order, and flagging two headlines that ended up worded
the same. The SDK wiring (bounds, retry, usage, concurrency) lives in ``orchestrate.py``.
"""

from __future__ import annotations

import csv
import itertools
import json
import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Directory under claude_input holding the per-story branches (s00, s01, ...).
BRANCH_ROOT_NAME = "write_branches"

# The prod input path the live agent prompts hardcode. Redirected per branch, the same
# way the coherence/repair evals redirect it (eval_coherence._PROD_INPUT_MARKER).
PROD_INPUT_MARKER = "/app/data/claude_input/"

TIERS = ("must_know", "should_know")

# Files a branch inherits unchanged: shared context that is not story-scoped.
SHARED_CONTEXT_FILES = ("recap.txt", "weekly_recap.txt", "recent_digest_headlines.txt")

BRANCH_DRAFT_NAME = "draft_selections.json"
BRANCH_SELECTED_NAME = "selected.json"
BRANCH_ARTICLES_NAME = "articles_1.csv"

# why_it_matters is a must_know field: briefs render headline + summary only (render.py), so
# a should_know branch does not write it, and one written anyway is dropped at fan-in
# before COHERENCE can flag it and repair can spend on it.
_REQUIRED_STORY_FIELDS = {
    "must_know": ("headline", "summary", "why_it_matters", "sources"),
    "should_know": ("headline", "summary", "sources"),
}

# Token-Jaccard above which two headlines are reported as near-duplicate WORDING. This
# measures shared vocabulary, not story identity: two branches describing one event in
# different words score near zero (test_paraphrase_of_one_event_is_not_detected pins six
# such pairs). Calibrated against the 79 archived digests batch WRITE produced: 9,000
# headline pairs, mean 0.009, q99.9 0.188, one pair at 0.538 and the next at 0.263.
HEADLINE_REPETITION_THRESHOLD = 0.30

_WORD_RE = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class Branch:
    """One selected story's isolated input directory."""

    name: str
    tier: str
    index: int
    dir: Path
    cluster_index: int | None
    story_article_ids: tuple[str, ...]
    context_article_ids: tuple[str, ...]


@dataclass(frozen=True)
class DroppedStory:
    """A selected story that could not be given a branch, and why."""

    name: str
    tier: str
    index: int
    cluster_index: int | None
    story_article_ids: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class FanOut:
    """What ``build_branches`` produced: the branches to run, and what fell out."""

    branches: list[Branch]
    dropped: list[DroppedStory] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# The branch prompt: write.md pointed at one branch's input directory.
# --------------------------------------------------------------------------- #


def branch_body(body: str, branch_dir: Path) -> str:
    """write.md as ONE branch runs it: the hardcoded prod input path redirected.

    Raises rather than returning the body untouched: a prompt whose paths drifted would
    otherwise run against the full run directory and silently undo the isolation.
    """
    if PROD_INPUT_MARKER not in body:
        raise ValueError(f"expected {PROD_INPUT_MARKER!r} in the agent body to redirect; prompt drifted")
    return body.replace(PROD_INPUT_MARKER, f"{branch_dir}/")


# --------------------------------------------------------------------------- #
# Fan out.
# --------------------------------------------------------------------------- #


def _load_json(path: Path) -> object:
    if not path.exists():
        raise ValueError(f"{path.name} missing")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ValueError(f"{path.name} unreadable/invalid JSON: {e}") from e


def _read_article_rows(claude_input_dir: Path) -> tuple[list[str], list[list[str]]]:
    """Header plus every row across the run's ``articles_*.csv``, in file order."""
    header: list[str] = []
    rows: list[list[str]] = []
    for path in sorted(claude_input_dir.glob("articles_*.csv")):
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            file_header = next(reader, None)
            if file_header is None:
                continue
            if not header:
                header = file_header
            elif file_header != header:
                raise ValueError(f"{path.name}: header {file_header} differs from {header}")
            rows.extend(reader)
    if not header:
        raise ValueError("no articles_*.csv found")
    return header, rows


def _cluster_ids(clusters: list, cluster_index: object, story_ids: list[str], branch_name: str) -> list[str]:
    """The cluster's article_ids for this story, or none if it has no usable cluster.

    A drifted or out-of-range index narrows the branch's evidence to the cited articles,
    which is a real change in what WRITE sees -- so it is logged."""
    if isinstance(cluster_index, int) and 0 <= cluster_index < len(clusters):
        entry = clusters[cluster_index]
        ids = entry.get("article_ids") if isinstance(entry, dict) else None
        if isinstance(ids, list):
            return [i for i in ids if isinstance(i, str)]
    logger.warning(
        "%s: cluster_index %r does not resolve against %d clusters -- branch sees only its %d cited article(s)",
        branch_name,
        cluster_index,
        len(clusters),
        len(story_ids),
    )
    return []


def _reusable_draft(branch_dir: Path, expected_selected: str, expected_ids: list[str], id_column: int) -> bool:
    """True if this branch already holds a valid draft written for THIS exact story from
    THIS exact evidence.

    The stage-level ``--resume`` pattern one level down: a re-run after a mid-phase failure
    must not re-pay for branches that already finished. Identity is both files a branch was
    given, not just ``selected.json``: the cluster a story points at can be repartitioned
    while its ``cluster_index`` and ``article_ids`` stay put, and reusing the draft then
    would leave ``context_article_ids`` in the run artifact describing evidence the draft
    was never written from.
    """
    try:
        if (branch_dir / BRANCH_SELECTED_NAME).read_text(encoding="utf-8") != expected_selected:
            return False
        with open(branch_dir / BRANCH_ARTICLES_NAME, newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            next(reader, None)
            present = [r[id_column] for r in reader if len(r) > id_column]
    except OSError:
        return False
    if present != expected_ids:
        return False
    try:
        branch_story(branch_dir)
    except ValueError:
        return False
    return True


def build_branches(claude_input_dir: Path) -> FanOut:
    """Write one input directory per selected story and return them in SELECT's order.

    Each branch carries: ``selected.json`` holding only that story under its own tier
    (plus the shared ``not_covered_blurb``), ``articles_1.csv`` filtered to the story's
    cluster unioned with its cited ids, ``article_fulltext.json`` filtered the same way,
    and unchanged copies of the shared context files.

    A story that resolves to zero articles is DROPPED rather than run: a WRITE call with
    no evidence is the highest-probability fabrication input in the pipeline. The drop is
    loud (ERROR, and recorded in the run artifact) but does not kill the run. A malformed
    entry in ``selected.json`` still raises -- that is SELECT drift, not a thin story.
    """
    selected = _load_json(claude_input_dir / "selected.json")
    if not isinstance(selected, dict):
        raise ValueError("selected.json: not an object")
    clusters_doc = _load_json(claude_input_dir / "clusters.json")
    clusters = clusters_doc.get("clusters") if isinstance(clusters_doc, dict) else None
    if not isinstance(clusters, list):
        raise ValueError("clusters.json: 'clusters' missing")

    header, rows = _read_article_rows(claude_input_dir)
    id_column = header.index("article_id")
    known_ids = {r[id_column] for r in rows if len(r) > id_column}
    fulltext_path = claude_input_dir / "article_fulltext.json"
    fulltext = _load_json(fulltext_path) if fulltext_path.exists() else None
    blurb = selected.get("not_covered_blurb")

    root = claude_input_dir / BRANCH_ROOT_NAME
    root.mkdir(parents=True, exist_ok=True)

    branches: list[Branch] = []
    dropped: list[DroppedStory] = []
    index = 0
    for tier in TIERS:
        for position, story in enumerate(selected.get(tier) or []):
            if not isinstance(story, dict):
                raise ValueError(f"selected.json: {tier}[{position}] is {type(story).__name__}, not an object")
            name = f"s{index:02d}"
            index += 1
            story_ids = [i for i in (story.get("article_ids") or []) if isinstance(i, str)]
            cluster_index = story.get("cluster_index") if isinstance(story.get("cluster_index"), int) else None
            context = _cluster_ids(clusters, story.get("cluster_index"), story_ids, name)
            context_ids = [i for i in dict.fromkeys([*context, *story_ids]) if i in known_ids]
            if not context_ids:
                logger.error(
                    "%s: %s[%d] resolves to no article in this run's CSVs (cluster_index=%r, article_ids=%s) "
                    "-- dropping the story rather than asking WRITE to write it from nothing",
                    name,
                    tier,
                    position,
                    story.get("cluster_index"),
                    story_ids,
                )
                dropped.append(
                    DroppedStory(
                        name=name,
                        tier=tier,
                        index=index - 1,
                        cluster_index=cluster_index,
                        story_article_ids=tuple(story_ids),
                        reason="no article in this run's CSVs",
                    )
                )
                continue

            one: dict = {t: [] for t in TIERS}
            one[tier] = [story]
            if isinstance(blurb, str):
                one["not_covered_blurb"] = blurb
            expected_selected = json.dumps(one, indent=2)

            # The branch CSV keeps the run's file order, not context_ids order, so this is
            # the id set a reused draft has to match.
            keep = set(context_ids)
            csv_ids = [r[id_column] for r in rows if len(r) > id_column and r[id_column] in keep]

            branch_dir = root / name
            if _reusable_draft(branch_dir, expected_selected, csv_ids, id_column):
                logger.info("%s: valid draft present, reusing", name)
            else:
                if branch_dir.exists():
                    shutil.rmtree(branch_dir)
                branch_dir.mkdir(parents=True)
                (branch_dir / BRANCH_SELECTED_NAME).write_text(expected_selected, encoding="utf-8")

                with open(branch_dir / BRANCH_ARTICLES_NAME, "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow(header)
                    writer.writerows(r for r in rows if len(r) > id_column and r[id_column] in keep)

                if isinstance(fulltext, dict):
                    (branch_dir / "article_fulltext.json").write_text(
                        json.dumps({k: v for k, v in fulltext.items() if k in keep}, indent=2), encoding="utf-8"
                    )

                for filename in SHARED_CONTEXT_FILES:
                    source = claude_input_dir / filename
                    if source.exists():
                        shutil.copy2(source, branch_dir / filename)

            branches.append(
                Branch(
                    name=name,
                    tier=tier,
                    index=index - 1,
                    dir=branch_dir,
                    cluster_index=cluster_index,
                    story_article_ids=tuple(story_ids),
                    context_article_ids=tuple(context_ids),
                )
            )

    if not branches:
        raise ValueError("selected.json: no selected stories to write")

    live = {b.name for b in branches}
    for stale in root.iterdir():
        if stale.is_dir() and stale.name not in live:
            shutil.rmtree(stale)

    logger.info("write: %d branch input dir(s) under %s, %d dropped", len(branches), root, len(dropped))
    return FanOut(branches=branches, dropped=dropped)


# --------------------------------------------------------------------------- #
# Fan in.
# --------------------------------------------------------------------------- #


def _branch_tier(branch_dir: Path) -> str:
    """SELECT's tier for the branch's one story, from the selected.json Python wrote there.

    The authority for what the story must carry -- not the key the model filed it under,
    which assemble_draft already refuses to trust for the digest tier. Keyed on the model's
    choice, a must_know story filed under should_know without its why_it_matters would pass
    here and kill the run at assembly, after COHERENCE and repair had been paid for.
    """
    selected = _load_json(branch_dir / BRANCH_SELECTED_NAME)
    if not isinstance(selected, dict):
        raise ValueError(f"{BRANCH_SELECTED_NAME}: not an object")
    tiers = [tier for tier in TIERS if selected.get(tier)]
    if len(tiers) != 1:
        raise ValueError(f"{BRANCH_SELECTED_NAME}: expected exactly 1 populated tier, found {tiers}")
    return tiers[0]


def branch_story(branch_dir: Path) -> dict:
    """The single story a branch wrote. Raises ValueError if the branch's draft is not
    exactly one well-formed story -- the branch then retries from a clean slate rather
    than letting the fan-in silently ship a story with a missing field or drop one."""
    tier = _branch_tier(branch_dir)
    draft = _load_json(branch_dir / BRANCH_DRAFT_NAME)
    if not isinstance(draft, dict):
        raise ValueError(f"{BRANCH_DRAFT_NAME}: not an object")
    stories = [s for t in TIERS for s in (draft.get(t) or []) if isinstance(s, dict)]
    if len(stories) != 1:
        raise ValueError(f"{BRANCH_DRAFT_NAME}: expected exactly 1 story, found {len(stories)}")
    story = stories[0]
    for field_name in _REQUIRED_STORY_FIELDS[tier]:
        if not story.get(field_name):
            raise ValueError(f"{BRANCH_DRAFT_NAME}: story is missing {field_name}")
    if not isinstance(story["sources"], list):
        raise ValueError(f"{BRANCH_DRAFT_NAME}: 'sources' is not a list")
    return story


def validate_branch_draft(branch_dir: Path) -> None:
    """run_stage-shaped validator for one branch (see :func:`branch_story`)."""
    branch_story(branch_dir)


def assemble_draft(branches: list[Branch]) -> dict:
    """Fan in: one story per branch, in SELECT's order, onto SELECT's tier.

    The tier comes from the branch (SELECT decided it), not from the key the branch
    happened to file its story under -- a branch writing to the other key must not move
    a story between digest tiers. ``preheader`` is filled by its own stage afterwards.
    """
    draft: dict = {tier: [] for tier in TIERS}
    draft["preheader"] = ""
    for branch in branches:
        story = branch_story(branch.dir)
        if branch.tier == "should_know":
            story.pop("why_it_matters", None)
        draft[branch.tier].append(story)
    return draft


def _tokens(headline: str) -> set[str]:
    return {w for w in _WORD_RE.findall((headline or "").lower()) if len(w) > 2}


def repetition_warnings(draft: dict, threshold: float = HEADLINE_REPETITION_THRESHOLD) -> list[tuple[str, str, float]]:
    """Headline pairs whose WORDING is near-duplicate, worst first.

    Independent branches share no context, so nothing stops two of them reaching for the
    same phrasing -- which the one batch call implicitly prevented. This catches the
    lexical case only; a paraphrase of the same event scores near zero. Advisory: both
    stories still ship.
    """
    headlines = [s.get("headline", "") for tier in TIERS for s in (draft.get(tier) or []) if isinstance(s, dict)]
    out: list[tuple[str, str, float]] = []
    for a, b in itertools.combinations(headlines, 2):
        ta, tb = _tokens(a), _tokens(b)
        if not ta or not tb:
            continue
        score = len(ta & tb) / len(ta | tb)
        if score >= threshold:
            out.append((a, b, score))
    return sorted(out, key=lambda p: -p[2])
