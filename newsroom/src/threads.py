"""Thread substrate: persistent identity + carried state for evolving story-threads.

A *thread* is an ongoing news story tracked across daily runs. Each run, the selected
stories are matched against the active threads and each is either continued or started
fresh. The thread carries a running narrative + an open-question ledger (written by the
synthesis stage, sub-project B).

Matcher: a cheap **Haiku semantic linker**. Deterministic token matching was built and
validated on the replay first and FAILED (caught 1 of ~9 obvious multi-day threads):
the shared signal drowns under facet/article-specific tokens and synonyms/rewording
never match. "Is this the same ongoing story?" is a semantic judgment, and the candidate
set per run is small (~16 stories x a few dozen active threads), so one Haiku call/run is
both cheap and far more accurate (replay: 94 threads, 2 over-merges, ~98% separation
precision; every unambiguous multi-day story collapsed to one thread). The pipeline
already runs Haiku for RECAP/COHERENCE, so this adds no new dependency.

This module is the foundation sub-projects B (synthesis) and C (rendering) consume; it
produces no reader-facing output on its own.
"""

from __future__ import annotations

import json
import logging
import re
from contextlib import contextmanager
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

HAIKU_MODEL = "claude-haiku-4-5-20251001"

LINK_SYSTEM = """You track ongoing news stories across days. You are given ACTIVE THREADS (ongoing stories from prior days, each an id + description) and TODAY'S STORIES (each an index + label). For each today-story decide whether it CONTINUES one active thread (the same ongoing story/situation, even if reworded or advanced by a new development) or is NEW.

Rules:
- Same thread = same ongoing event/situation. "US-Iran nuclear talks in Switzerland" continues "US-Iran nuclear deal Swiss negotiations". "European heatwave: record temperatures" continues "Europe Heatwave - France Red Alert".
- Different sub-stories that merely share an entity are DIFFERENT threads: "Strait of Hormuz shipping disruption" is NOT the same thread as "US-Iran nuclear deal" even though both involve Iran. "Iran attacks cargo ship" (a military strike) is a different thread from "US-Iran nuclear negotiations" (diplomacy).
- Be precise: only link genuine continuations. When unsure, prefer NEW over a wrong link.
- Each today-story maps to at most one thread.

Respond with ONLY JSON, no prose: {"links": [{"story": 0, "thread": 3}, {"story": 1, "thread": "NEW"}]} -- one entry per today-story, thread is an active-thread id or "NEW"."""


@dataclass
class ActiveThread:
    """A thread eligible to continue this run -- what the linker sees."""

    thread_id: int
    label: str
    narrative: str | None = None


@dataclass
class ThreadAssignment:
    """The result of matching one selected story to a thread -- the interface B consumes."""

    thread_id: int
    is_new: bool
    cluster_story: str
    article_ids: list[str] = field(default_factory=list)
    prior_narrative: str | None = None
    open_questions: list[str] = field(default_factory=list)


def selected_labels(clusters_doc: dict, selected_doc: dict) -> list[dict]:
    """Map the SELECT output (cluster_index references) + the CLUSTER output to this run's
    selected story labels: `[{"story": label, "tier": tier}, ...]` in must_know-then-should_know
    order. Pure; the integration adapter reads the two JSON files and passes them here."""
    clusters = clusters_doc.get("clusters", [])
    out: list[dict] = []
    for tier in ("must_know", "should_know"):
        for entry in selected_doc.get(tier, []) or []:
            idx = entry.get("cluster_index")
            if isinstance(idx, int) and 0 <= idx < len(clusters):
                cluster = clusters[idx]
                out.append(
                    {
                        "story": cluster.get("story", ""),
                        "tier": tier,
                        "article_ids": entry.get("article_ids") or cluster.get("article_ids", []),
                    }
                )
    return out


def _slugify(label: str, *, max_len: int = 60) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (label or "").lower()).strip("-")
    return slug[:max_len] or "thread"


def _parse_links(text: str) -> list[dict]:
    """Pull the {"links": [...]} array out of the model's response (tolerant of fences/prose)."""
    s, e = text.find("{"), text.rfind("}")
    if 0 <= s < e:
        try:
            obj = json.loads(text[s : e + 1])
            if isinstance(obj.get("links"), list):
                return obj["links"]
        except ValueError, AttributeError:
            pass
    return []


def link_threads(active: list[ActiveThread], today_labels: list[str], *, model: str = HAIKU_MODEL) -> list[int | None]:
    """Map each of today's story labels to an active thread_id (continuation) or None (new).

    One cheap Haiku call. Returns all-None (no threading this run) on any LLM/parse failure so
    the digest proceeds and recovers next run -- thread identity must never crash the pipeline.
    """
    if not active or not today_labels:
        return [None] * len(today_labels)

    valid_ids = {t.thread_id for t in active}
    threads_block = "\n".join(f"  [{t.thread_id}] {t.narrative or t.label}" for t in active)
    today_block = "\n".join(f"  ({i}) {lab}" for i, lab in enumerate(today_labels))
    user = (
        f"ACTIVE THREADS:\n{threads_block}\n\nTODAY'S STORIES:\n{today_block}\n\n"
        "Map each today-story to a thread id or NEW."
    )

    try:
        import claude_cli

        text = claude_cli.run_sync(
            user,
            model=model,
            system_prompt=LINK_SYSTEM,
            tools=[],
            timeout=300,
            idle_timeout=180.0,
            thinking={"type": "disabled"},
        )
        links = _parse_links(text)
    except Exception:  # never let thread-linking break the digest
        logger.warning("thread linker failed; treating all stories as new threads", exc_info=True)
        return [None] * len(today_labels)

    out: list[int | None] = [None] * len(today_labels)
    for ln in links:
        si, tid = ln.get("story"), ln.get("thread")
        if isinstance(si, int) and 0 <= si < len(today_labels) and isinstance(tid, int) and tid in valid_ids:
            out[si] = tid
    return out


class ThreadStore:
    """Thin SQL wrapper over the thread tables. Holds no global state; takes a connection.

    Sub-project A uses the identity/installment/aging methods; the narrative + question
    methods are the seam sub-project B writes through.
    """

    def __init__(self, conn):
        self.conn = conn
        self._defer_commit = False

    def _commit(self) -> None:
        if not self._defer_commit:
            self.conn.commit()

    @contextmanager
    def transaction(self):
        """Group several writes into one atomic unit. Inside, the per-method commits are
        suppressed; the whole block commits once on success or rolls back on any error -- so a
        partial multi-step update (e.g. narrative advanced but the installment row left NULL)
        can't be left behind."""
        self._defer_commit = True
        try:
            yield
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        finally:
            self._defer_commit = False

    # --- reads ---

    def active_threads(self, *, before_run_id: int, dormant_after: int) -> list[ActiveThread]:
        """Threads still active and seen within the last `dormant_after` COMPLETED runs. Counting
        completed runs (not raw run-id distance) keeps failed-run id gaps -- e.g. the 4-failure
        2026-06-16 incident -- from prematurely retiring a live thread."""
        rows = self.conn.execute(
            """
            SELECT id, label, narrative FROM threads
            WHERE status = 'active'
              AND last_run_id IS NOT NULL
              AND (SELECT COUNT(*) FROM digest_runs
                   WHERE id > threads.last_run_id AND id < ? AND completed_at IS NOT NULL) <= ?
            ORDER BY last_run_id DESC, id
            """,
            (before_run_id, dormant_after),
        ).fetchall()
        return [ActiveThread(r[0], r[1], r[2]) for r in rows]

    def open_questions(self, thread_id: int) -> list[str]:
        rows = self.conn.execute(
            "SELECT question FROM thread_questions WHERE thread_id = ? AND status = 'open' ORDER BY id",
            (thread_id,),
        ).fetchall()
        return [r[0] for r in rows]

    def render_context(self, thread_id: int, run_id: int) -> dict:
        """The thread facts the renderer (sub-project C) needs: how many days the thread has run,
        the questions still open, and any answered THIS run."""
        day = self.conn.execute(
            "SELECT COUNT(*) FROM thread_installments WHERE thread_id = ?", (thread_id,)
        ).fetchone()[0]
        resolved = [
            r[0]
            for r in self.conn.execute(
                "SELECT question FROM thread_questions WHERE thread_id = ? AND status = 'resolved' AND resolved_run_id = ? ORDER BY id",
                (thread_id, run_id),
            )
        ]
        return {"day": day, "open_questions": self.open_questions(thread_id), "resolved": resolved}

    # --- writes (identity / aging) ---

    def create_thread(self, label: str, run_id: int) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO threads (slug, label, status, first_run_id, last_run_id)
            VALUES (?, ?, 'active', ?, ?)
            """,
            (_slugify(label), label, run_id, run_id),
        )
        self._commit()
        return cur.lastrowid

    def touch_thread(self, thread_id: int, label: str, run_id: int) -> None:
        """Continue a thread: advance last_run_id and refresh its label to the latest installment."""
        self.conn.execute(
            """
            UPDATE threads
            SET last_run_id = ?, label = ?, status = 'active', updated_at = datetime('now', 'utc')
            WHERE id = ?
            """,
            (run_id, label, thread_id),
        )
        self._commit()

    def record_installment(self, thread_id: int, run_id: int, cluster_story: str, is_new: bool) -> None:
        # matched_score column is retained for forensics; NULL for a new thread, 1.0 for a
        # linker continuation (the semantic linker is a binary decision, not a score).
        self.conn.execute(
            "INSERT INTO thread_installments (thread_id, run_id, cluster_story, matched_score) VALUES (?, ?, ?, ?)",
            (thread_id, run_id, cluster_story, None if is_new else 1.0),
        )
        self._commit()

    def decay_threads(self, current_run_id: int, dormant_after: int) -> None:
        """Mark active threads not seen within `dormant_after` COMPLETED runs as dormant so they
        no longer match. (A returning story starts fresh -- editorially "back in the news".)"""
        self.conn.execute(
            """
            UPDATE threads SET status = 'dormant', updated_at = datetime('now', 'utc')
            WHERE status = 'active'
              AND last_run_id IS NOT NULL
              AND (SELECT COUNT(*) FROM digest_runs
                   WHERE id > threads.last_run_id AND id < ? AND completed_at IS NOT NULL) > ?
            """,
            (current_run_id, dormant_after),
        )
        self._commit()

    # --- writes (narrative / ledger) -- the seam sub-project B fills ---

    def set_installment_content(self, thread_id: int, run_id: int, content: str) -> None:
        """Attach the synthesized installment JSON to this run's installment row (sub-project B)."""
        self.conn.execute(
            "UPDATE thread_installments SET content = ? WHERE thread_id = ? AND run_id = ?",
            (content, thread_id, run_id),
        )
        self._commit()

    def set_narrative(self, thread_id: int, narrative: str) -> None:
        self.conn.execute(
            "UPDATE threads SET narrative = ?, updated_at = datetime('now', 'utc') WHERE id = ?",
            (narrative, thread_id),
        )
        self._commit()

    def add_questions(self, thread_id: int, questions: list[str], run_id: int) -> None:
        for q in questions:
            self.conn.execute(
                "INSERT INTO thread_questions (thread_id, question, status, raised_run_id) VALUES (?, ?, 'open', ?)",
                (thread_id, q, run_id),
            )
        self._commit()

    def resolve_question(self, thread_id: int, question: str, run_id: int, how: str) -> None:
        self.conn.execute(
            """
            UPDATE thread_questions
            SET status = 'resolved', resolved_run_id = ?, resolved_how = ?
            WHERE thread_id = ? AND question = ? AND status = 'open'
            """,
            (run_id, how, thread_id, question),
        )
        self._commit()


def resolve_threads(
    stories: list[dict],
    run_id: int,
    store: ThreadStore,
    *,
    dormant_after: int = 3,
    linker=link_threads,
) -> list[ThreadAssignment]:
    """Assign each selected story to a thread (continuing or new) and persist identity.

    `stories` is a list of `{"story": label, ...}` for the SELECTED stories this run. The
    linker is injectable so tests can supply a deterministic fake (no LLM in CI).
    """
    store.decay_threads(run_id, dormant_after)
    active = store.active_threads(before_run_id=run_id, dormant_after=dormant_after)
    active_by_id = {t.thread_id: t for t in active}

    labels = [st.get("story", "") for st in stories]
    mapping = linker(active, labels)

    assignments: list[ThreadAssignment] = []
    claimed: set[int] = set()  # one thread continues at most once per run
    for i, label in enumerate(labels):
        article_ids = list(stories[i].get("article_ids", []))
        tid = mapping[i] if i < len(mapping) else None
        if tid is not None and tid in active_by_id and tid not in claimed:
            claimed.add(tid)
            store.touch_thread(tid, label, run_id)
            store.record_installment(tid, run_id, label, is_new=False)
            prior = active_by_id[tid]
            assignments.append(
                ThreadAssignment(
                    thread_id=tid,
                    is_new=False,
                    cluster_story=label,
                    article_ids=article_ids,
                    prior_narrative=prior.narrative,
                    open_questions=store.open_questions(tid),
                )
            )
        else:
            new_id = store.create_thread(label, run_id)
            store.record_installment(new_id, run_id, label, is_new=True)
            assignments.append(
                ThreadAssignment(thread_id=new_id, is_new=True, cluster_story=label, article_ids=article_ids)
            )
    return assignments
