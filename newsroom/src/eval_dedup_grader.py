"""Cross-story semantic dedup grader (eval-floor / GEPA metric, offline only).

The digest can select two "different" stories that are really the same event,
or two stories with heavy overlap -- redundant from the reader's seat. This
grader screens a digest's selected stories for that redundancy using MiniLM
(all-MiniLM-L6-v2) cosine similarity.

WHAT THE LABELS SHOWED (validated against 60 human-labelled production pairs,
``dedup_golden.json``):
  * MiniLM cosine cleanly separates DISTINCT stories (label ``n``: cosine
    <= 0.568) from OVERLAPPING ones (``y`` same-event or ``partial`` overlap:
    cosine >= 0.556) at a threshold of ~0.57.
  * It CANNOT separate strict same-event (``y``) from partial overlap -- both
    are "semantically overlapping" and cosine can't grade the degree. Tuning a
    threshold to isolate ``y`` alone is hopeless (best F1 ~0.56, precision 0.42).
So this is a REDUNDANCY SCREEN, not a strict same-event detector: positive =
``y`` OR ``partial``. At 0.57 it scores precision 1.0 / recall 0.95 on the
golden. (The original plan's 0.70 was tuned for the unachievable y-only target.)

OFFLINE ONLY. sentence-transformers/torch is an optional ``eval`` dependency,
not in the prod pipeline (it never runs on the Hetzner box) and not in CI. The
pure-code scoring path (``score_threshold``, ``load_golden_cases``) needs no
embedding model -- it works off the committed cosines -- so the regression test
runs in CI without torch.

Live-embedding caveat: the committed cosines are the error-analysis PoC's
precomputed values that the labels were assigned against. ``embed`` here uses
``headline + ". " + summary``; re-validate that the live cosines reproduce the
golden cosines closely before trusting the live grader's absolute threshold.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

# Validated against dedup_golden.json: redundant (y+partial) vs distinct (n).
DEDUP_THRESHOLD = 0.57
MINILM_MODEL = "all-MiniLM-L6-v2"

# The redundancy screen's positive class: same-event ("y") OR partial overlap.
# Single source of truth for what counts as redundant (asserted in the tests).
REDUNDANT_LABELS = frozenset({"y", "partial"})


# --------------------------------------------------------------------------- #
# Similarity primitives (pure code -- no numpy/torch)
# --------------------------------------------------------------------------- #


def cosine_sim(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two equal-length vectors (pure Python)."""
    if len(a) != len(b):
        raise ValueError(f"vector length mismatch: {len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def story_text(story: dict) -> str:
    """The text the grader embeds for a story (headline + summary)."""
    headline = (story.get("headline") or "").strip()
    summary = (story.get("summary") or "").strip()
    return f"{headline}. {summary}".strip()


# --------------------------------------------------------------------------- #
# Live grader (lazy MiniLM -- only imported when actually embedding)
# --------------------------------------------------------------------------- #


def embed(texts: list[str], *, model_name: str = MINILM_MODEL) -> list[list[float]]:
    """Embed texts with MiniLM. Requires the optional ``eval`` dependency.

    Lazy import so the pure-code scoring path (and CI) never needs torch.
    """
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as e:  # pragma: no cover - exercised only without the optional dep
        raise ImportError(
            "eval_dedup_grader.embed needs sentence-transformers (optional 'eval' "
            "dependency). Install with: uv sync --extra eval. It is intentionally "
            "absent from the prod pipeline and CI."
        ) from e
    model = SentenceTransformer(model_name)
    return [v.tolist() for v in model.encode(texts, normalize_embeddings=False)]


@dataclass(frozen=True)
class RedundantPair:
    """Two selected stories flagged as a cross-story redundancy."""

    index_a: int
    index_b: int
    cosine: float


def find_redundant_pairs(stories: list[dict], *, threshold: float = DEDUP_THRESHOLD) -> list[RedundantPair]:
    """Flag every story pair whose MiniLM cosine >= ``threshold`` (live embed).

    The GEPA-facing grader: returns the redundant pairs in a digest's selected
    stories. Penalising the count drives down cross-story redundancy.
    """
    vectors = embed([story_text(s) for s in stories])
    pairs: list[RedundantPair] = []
    for i in range(len(vectors)):
        for j in range(i + 1, len(vectors)):
            cos = cosine_sim(vectors[i], vectors[j])
            if cos >= threshold:
                pairs.append(RedundantPair(i, j, cos))
    return pairs


# --------------------------------------------------------------------------- #
# Offline scoring against the golden (pure code, no model calls)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DedupCase:
    """One labelled story pair with its precomputed cosine.

    ``redundant`` is the binary target (same-event OR partial overlap).
    """

    cosine: float
    label_same_event: str  # y | partial | n
    redundant: bool
    label_note: str = ""


@dataclass
class DedupReport:
    """Confusion matrix for the redundancy screen (redundant = positive)."""

    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0
    misclassified: list[str] = field(default_factory=list)

    @property
    def n(self) -> int:
        return self.tp + self.fp + self.fn + self.tn

    @property
    def precision(self) -> float | None:
        flagged = self.tp + self.fp
        return self.tp / flagged if flagged else None

    @property
    def recall(self) -> float | None:
        actual = self.tp + self.fn
        return self.tp / actual if actual else None

    @property
    def accuracy(self) -> float:
        return (self.tp + self.tn) / self.n if self.n else 1.0


def score_threshold(cases: list[DedupCase], threshold: float = DEDUP_THRESHOLD) -> DedupReport:
    """Score the redundancy screen at ``threshold`` against the labels."""
    report = DedupReport()
    for c in cases:
        flagged = c.cosine >= threshold
        if flagged and c.redundant:
            report.tp += 1
        elif flagged and not c.redundant:
            report.fp += 1
            report.misclassified.append(f"FP cos={c.cosine:.3f} label={c.label_same_event}")
        elif not flagged and c.redundant:
            report.fn += 1
            report.misclassified.append(f"FN cos={c.cosine:.3f} label={c.label_same_event}")
        else:
            report.tn += 1
    return report


def load_golden_cases(golden_path: str | Path) -> list[DedupCase]:
    """Load ``dedup_golden.json`` into DedupCase rows."""
    data = json.loads(Path(golden_path).read_text(encoding="utf-8"))
    return [
        DedupCase(
            cosine=float(c["cosine"]),
            label_same_event=c["label_same_event"],
            redundant=bool(c["redundant"]),
            label_note=c.get("label_note", ""),
        )
        for c in data.get("cases", [])
    ]
