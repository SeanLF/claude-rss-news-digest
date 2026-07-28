"""Thread substrate: persistent identity + carried state for evolving story-threads.

A *thread* is an ongoing news story tracked across daily runs. Each run, the selected
stories are matched against the active threads and each is either continued or started
fresh. The thread's running record IS its per-run installments (the synthesized whats_new
facts) plus an open-question ledger -- both written by the synthesis stage, sub-project B.

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
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field

from utils import strip_article_ids

logger = logging.getLogger(__name__)

HAIKU_MODEL = "claude-haiku-4-5-20251001"

# How many recent installment labels (the story arc) to show the linker per active thread. The
# latest label alone drifts to a narrow facet ("Starmer resigns") and fails to match its own
# continuation ("Burnham to become PM"); the arc keeps the thread recognisable across re-labelling.
RECENT_LABELS_K = 4

LINK_SYSTEM = """You track ongoing news stories across days. You are given ACTIVE THREADS (ongoing stories from prior days, each an id + its recent arc shown as "earlier label -> ... -> latest label", oldest to newest) and TODAY'S STORIES (each an index + label). For each today-story decide whether it CONTINUES one active thread (the same ongoing story/situation, even if reworded or advanced by a new development) or is NEW.

Rules:
- Same thread = same ongoing event/situation. "US-Iran nuclear talks in Switzerland" continues "US-Iran nuclear deal Swiss negotiations". "European heatwave: record temperatures" continues "Europe Heatwave - France Red Alert".
- Judge against the WHOLE arc, not only the latest label: a thread "Makerfield by-election -> Burnham wins -> Starmer resigns" is continued by "Burnham set to become PM" -- the arc shows it is the same UK-leadership story even though the latest label moved on.
- Different sub-stories that merely share an entity are DIFFERENT threads: "Strait of Hormuz shipping disruption" is NOT the same thread as "US-Iran nuclear deal" even though both involve Iran. "Iran attacks cargo ship" (a military strike) is a different thread from "US-Iran nuclear negotiations" (diplomacy).
- Be precise: only link genuine continuations. When unsure, prefer NEW over a wrong link.
- Each today-story maps to at most one thread.

Respond with ONLY JSON, no prose: {"links": [{"story": 0, "thread": 3}, {"story": 1, "thread": null}]} -- one entry per today-story. `thread` is an active-thread id as a bare JSON number, or null for NEW. Never quote the id."""


@dataclass
class ActiveThread:
    """A thread eligible to continue this run -- what the linker sees.

    `recent_labels` is the thread's recent story arc (last few installment labels, oldest->newest);
    the linker matches on the arc, not just `label` (the latest), so a thread whose label has
    drifted still recognises its own continuation. Falls back to `[label]` for a brand-new thread.
    """

    thread_id: int
    label: str
    recent_labels: list[str] = field(default_factory=list)


@dataclass
class ThreadAssignment:
    """The result of matching one selected story to a thread -- the interface B consumes.

    `recent_updates` is the thread's MEMORY: the last few days' delta lines (what was already
    reported), so the synthesis can compute what's genuinely new today. There is no separately
    maintained narrative -- the deltas ARE the running record.
    """

    thread_id: int
    is_new: bool
    cluster_story: str
    article_ids: list[str] = field(default_factory=list)
    recent_updates: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)


def _tidy(text: str) -> str:
    """Collapse whitespace runs and trim -- what stripping a citation leaves behind."""
    return re.sub(r"\s{2,}", " ", text).strip()


def delta_from_facts(facts: list[dict], *, top_n: int = 3) -> str:
    """The thread's delta = the top-N verified whats_new facts joined as prose. Faithful by
    construction: each fact already passed the per-fact audit, so there's nothing to re-gate.
    Facts are ordered most-important-first by the synthesis, so the top N lead with what matters.

    This is the single funnel for both the rendered delta and the carried memory (recent_deltas),
    so it strips any inline ``[A123]`` / ``(A123)`` citations the synthesis leaked into fact prose
    -- the thread path's reader-facing leak guard, resilient to already-stored facts that still
    carry them.

    Tidying is done HERE, not in ``strip_article_ids``, because the two callers want opposite
    things: merge compares before/after to decide whether a leak occurred, so it needs the
    no-op case byte-identical, while these facts are joined with a space and a stray trailing
    or doubled space would show up in the rendered delta (and in tomorrow's carried memory).
    The Rust mirror in ``circulation/src/thread.rs`` collapses unconditionally for the same
    reason -- the same stored fact must render identically on the archive page."""
    return " ".join(_tidy(strip_article_ids(f.get("fact", ""))) for f in facts[:top_n] if f.get("fact"))


def _whats_new(content: str | None) -> list[dict]:
    """Parse a stored installment's whats_new facts ([] if missing/corrupt)."""
    if not content:
        return []
    with suppress(ValueError, TypeError, AttributeError):
        return json.loads(content).get("whats_new", []) or []
    return []


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


def _as_index(value: object) -> int | None:
    """Coerce a link field to an int index, tolerating a digit string.

    Whether the model writes `261` or `"261"` is JSON formatting drift, not a different
    answer, and a strict isinstance(int) check turns that drift into silent data loss
    (run 244, 2026-07-25: all 16 correct links dropped). `bool` is excluded because it
    is an int subclass; "NEW" and null fall through to None == a new thread.

    Must never raise: the call site is OUTSIDE link_threads' try/except, so a ValueError
    here would escape to run.py's blanket handler and drop the whole thread stage -- the
    failure this helper exists to prevent. Hence `isdecimal()`, not `isdigit()`: both
    accept non-ASCII decimal digits (Arabic-Indic, fullwidth), which int() parses, but
    isdigit() ALSO accepts superscripts, which int() rejects. No sign handling either --
    ids and indices are never negative, callers range-check anyway, and `lstrip("-")`
    would let "--5" through the guard and into int(), where it raises. See the
    parametrized cases in test_threads.py for the exact inputs.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and (v := value.strip()).isdecimal():
        return int(v)
    return None


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
    threads_block = "\n".join(f"  [{t.thread_id}] {' -> '.join(t.recent_labels or [t.label])}" for t in active)
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

    if not links:
        # Nothing parsed at all: prose/refusal, an empty completion, or the next shape drift
        # ({"links": {...}}, a renamed key). Identical outcome to the exception path but with
        # no traceback, so without this it is the quietest total-continuity loss there is.
        logger.error(
            "thread linker returned no parseable links; all %d stories treated as new. Response: %r",
            len(today_labels),
            text[:300],
        )
        return [None] * len(today_labels)

    out: list[int | None] = [None] * len(today_labels)
    for ln in links:
        si, tid = _as_index(ln.get("story")), _as_index(ln.get("thread"))
        if si is not None and 0 <= si < len(today_labels) and tid in valid_ids:
            out[si] = tid

    # Judge against links that PROPOSED a thread, not every parsed entry: the prompt asks for
    # `null` on a new story, so a legitimately all-new day returns a full array of nulls and
    # must stay silent, or this detector cries wolf on normal output.
    #
    # Any rejection is a bug -- drift is usually PARTIAL (some ids quoted, an index off by one,
    # a hallucinated id), and each one silently demotes a real continuation to a new thread, so
    # a reader sees "day 1" on a week-old story. Run 244 was total only because the model quoted
    # every id at once.
    proposed = [ln for ln in links if _as_index(ln.get("thread")) is not None]
    linked = sum(1 for v in out if v is not None)
    if len(proposed) > linked:
        emit = logger.warning if linked else logger.error
        emit(
            "thread linker validated %d of %d proposed link(s) against %d active thread(s); "
            "the rest were treated as new. First proposal: %r",
            linked,
            len(proposed),
            len(active),
            proposed[0],
        )
    return out


class ThreadStore:
    """Thin SQL wrapper over the thread tables. Holds no global state; takes a connection.

    Sub-project A uses the identity/installment/aging methods; the installment-content + question
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
        partial multi-step update (e.g. a question resolved but the installment row left NULL)
        can't be left behind.

        RE-ENTRANT: a nested block joins the outer one instead of committing. Methods that open
        a transaction internally (merge_thread) would otherwise commit mid-block AND clear the
        defer flag, so every later write in the outer block would self-commit and the rollback
        would silently protect nothing -- the trap being that the calling code reads as atomic.
        """
        if self._defer_commit:
            yield  # already inside a transaction: the outermost block owns commit/rollback
            return
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
            SELECT id, label FROM threads
            WHERE status = 'active'
              AND last_run_id IS NOT NULL
              AND (SELECT COUNT(*) FROM digest_runs
                   WHERE id > threads.last_run_id AND id < ? AND completed_at IS NOT NULL) <= ?
            ORDER BY last_run_id DESC, id
            """,
            (before_run_id, dormant_after),
        ).fetchall()
        if not rows:
            return []
        history = self._recent_labels(ids=[r[0] for r in rows], before_run_id=before_run_id)
        return [ActiveThread(tid, label, recent_labels=history.get(tid) or [label]) for tid, label in rows]

    def _recent_labels(self, *, ids: list[int], before_run_id: int) -> dict[int, list[str]]:
        """The last RECENT_LABELS_K installment labels per thread (oldest->newest) -- the story arc
        the linker matches on. One windowed query rather than a per-thread fetch."""
        placeholders = ",".join("?" * len(ids))
        rows = self.conn.execute(
            f"""
            SELECT thread_id, cluster_story FROM (
                SELECT thread_id, run_id, cluster_story,
                       ROW_NUMBER() OVER (PARTITION BY thread_id ORDER BY run_id DESC) AS rn
                FROM thread_installments
                WHERE run_id < ? AND cluster_story IS NOT NULL AND thread_id IN ({placeholders})
            ) WHERE rn <= ? ORDER BY thread_id, run_id
            """,
            (before_run_id, *ids, RECENT_LABELS_K),
        ).fetchall()
        history: dict[int, list[str]] = {}
        for tid, story in rows:
            history.setdefault(tid, []).append(story)
        return history

    def open_questions(self, thread_id: int) -> list[str]:
        rows = self.conn.execute(
            "SELECT question FROM thread_questions WHERE thread_id = ? AND status = 'open' ORDER BY id",
            (thread_id,),
        ).fetchall()
        return [r[0] for r in rows]

    def _installment_facts(self, thread_id: int, run_id: int) -> list[dict]:
        row = self.conn.execute(
            "SELECT content FROM thread_installments WHERE thread_id = ? AND run_id = ?", (thread_id, run_id)
        ).fetchone()
        return _whats_new(row[0] if row else None)

    def recent_deltas(self, thread_id: int, *, limit: int = 3) -> list[str]:
        """The thread's MEMORY: the last `limit` runs' delta lines (verified whats_new facts
        joined), oldest-first, for feeding the next synthesis as "what's already been reported"."""
        rows = self.conn.execute(
            """
            SELECT content FROM thread_installments
            WHERE thread_id = ? AND content IS NOT NULL
            ORDER BY run_id DESC LIMIT ?
            """,
            (thread_id, limit),
        ).fetchall()
        deltas = [delta_from_facts(_whats_new(content)) for (content,) in reversed(rows)]  # oldest-first
        return [d for d in deltas if d]

    def render_context(self, thread_id: int, run_id: int) -> dict:
        """The thread facts the renderer (sub-project C) needs: the day count (for the
        "Ongoing · day N" badge) and `delta` -- this run's verified whats_new facts joined as the
        "what's new today" summary that REPLACES the generic summary for a returning reader (blank
        on a quiet day, so the renderer falls back to the WRITE summary)."""
        day = self.conn.execute(
            "SELECT COUNT(*) FROM thread_installments WHERE thread_id = ?", (thread_id,)
        ).fetchone()[0]
        return {"day": day, "delta": delta_from_facts(self._installment_facts(thread_id, run_id))}

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

    def merge_thread(self, source_id: int, target_id: int) -> dict | None:
        """Fold a duplicate thread's history into the thread it should have continued.

        When the linker fails to recognise a continuation it opens a SECOND thread for a story
        already being tracked (run 244, 2026-07-25: the quoted-id bug split 5 stories). Retiring
        the duplicate is not enough -- `render_context` derives the "Ongoing · day N" badge from
        `COUNT(thread_installments)`, so the arc keeps a hole and the badge under-counts, and the
        duplicate holds the NEWEST label, which is what the linker matches on next run.

        Moves the source's installments and open questions onto the target, advances the target's
        label/last_run_id if the source is newer, backdates first_run_id to the earlier origin,
        and deletes the source. Atomic (partial merges are worse than none) and idempotent, so a
        repair script can be re-run. Returns what moved, or None if the source is already gone.

        Does NOT touch `digests` -- published issues are immutable HTML blobs and stay exactly as
        they were sent. This reconciles story IDENTITY going forward, it does not rewrite history.
        """
        if source_id == target_id:
            raise ValueError(f"cannot merge thread {source_id} into itself")
        src = self.conn.execute(
            "SELECT label, first_run_id, last_run_id FROM threads WHERE id = ?", (source_id,)
        ).fetchone()
        if src is None:
            return None  # already merged
        tgt = self.conn.execute(
            "SELECT label, first_run_id, last_run_id FROM threads WHERE id = ?", (target_id,)
        ).fetchone()
        if tgt is None:
            raise ValueError(f"merge target thread {target_id} does not exist")

        src_label, src_first, src_last = src
        tgt_label, tgt_first, tgt_last = tgt

        with self.transaction():
            moved = self.conn.execute(
                "UPDATE thread_installments SET thread_id = ? WHERE thread_id = ?",
                (target_id, source_id),
            ).rowcount

            # Then collapse to one installment per (thread, run) or the day count inflates --
            # render_context derives the reader-visible badge from COUNT(*). Done AFTER the move
            # rather than by pairing rows up front because there is no UNIQUE(thread_id, run_id)
            # constraint, so either side may already hold a stray pair; deduping the merged set
            # is correct no matter how many rows arrive. The row carrying synthesized content
            # wins (it is the real delta); ties break on lowest id for determinism.
            dropped = self.conn.execute(
                """
                DELETE FROM thread_installments WHERE id IN (
                    SELECT id FROM (
                        SELECT id, ROW_NUMBER() OVER (
                            PARTITION BY run_id ORDER BY (content IS NULL), id
                        ) AS rn
                        FROM thread_installments WHERE thread_id = ?
                    ) WHERE rn > 1
                )
                """,
                (target_id,),
            ).rowcount

            # Move only questions the target has not already ANSWERED. A duplicate thread asks
            # the same questions as the real one, so re-parenting its open copy over a resolved
            # one puts a settled question back into the synthesis prompt's OPEN QUESTIONS list
            # -- telling the model to treat answered material as still-open.
            self.conn.execute(
                """
                DELETE FROM thread_questions
                WHERE thread_id = ? AND question IN (
                    SELECT question FROM thread_questions WHERE thread_id = ? AND status = 'resolved'
                )
                """,
                (source_id, target_id),
            )
            questions = self.conn.execute(
                "UPDATE thread_questions SET thread_id = ? WHERE thread_id = ?",
                (target_id, source_id),
            ).rowcount

            # The newer side owns the label the linker will see next run. Either run id may be
            # NULL (a thread seeded outside a run), so span the merged thread across whichever
            # bounds actually exist rather than treating a missing one as run 0.
            source_is_newer = (src_last or 0) > (tgt_last or 0)
            self.conn.execute(
                """
                UPDATE threads
                SET label = ?, last_run_id = ?, first_run_id = ?, status = 'active',
                    updated_at = datetime('now', 'utc')
                WHERE id = ?
                """,
                (
                    src_label if source_is_newer else tgt_label,
                    max((r for r in (src_last, tgt_last) if r is not None), default=None),
                    min((r for r in (src_first, tgt_first) if r is not None), default=None),
                    target_id,
                ),
            )
            self.conn.execute("DELETE FROM threads WHERE id = ?", (source_id,))

        logger.info(
            "merged thread %d into %d: %d installment(s) moved, %d dropped, %d question(s) moved",
            source_id,
            target_id,
            moved,
            dropped,
            questions,
        )
        return {"installments_moved": moved, "installments_dropped": dropped, "questions_moved": questions}

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

    # --- writes (installment content / ledger) -- the seam sub-project B fills ---

    def record_run_health(self, run_id: int, *, synthesized: int, audit_failures: int) -> None:
        """Record this run's thread-processing health (gate signal). audit_failures > 0 means
        the faithfulness audit failed-open and unsupported facts went unchecked -- alertable."""
        self.conn.execute(
            "INSERT INTO thread_runs (run_id, threads_synthesized, audit_failures) VALUES (?, ?, ?)",
            (run_id, synthesized, audit_failures),
        )
        self._commit()

    def set_installment_content(self, thread_id: int, run_id: int, content: str) -> None:
        """Attach the synthesized installment JSON to this run's installment row (sub-project B)."""
        self.conn.execute(
            "UPDATE thread_installments SET content = ? WHERE thread_id = ? AND run_id = ?",
            (content, thread_id, run_id),
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
    linker=None,
) -> list[ThreadAssignment]:
    """Assign each selected story to a thread (continuing or new) and persist identity.

    `stories` is a list of `{"story": label, ...}` for the SELECTED stories this run. The linker
    is injectable (resolved at call time, so it stays overridable) so tests can supply a
    deterministic fake (no LLM in CI).
    """
    if linker is None:
        linker = link_threads
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
            assignments.append(
                ThreadAssignment(
                    thread_id=tid,
                    is_new=False,
                    cluster_story=label,
                    article_ids=article_ids,
                    recent_updates=store.recent_deltas(tid),
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
