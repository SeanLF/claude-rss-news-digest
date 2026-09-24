"""The Python thread layer as an oracle for the TypeScript port (digest/src/threads, activities/threads.ts).

    threads_oracle.py --db CLONE.db --run N --out DIR

CLONE.db, a production clone, is only read: it is copied into memory, its thread tables are rolled
back to just before run N, and everything the thread layer does not read is emptied. That state is
saved as DIR/pre.db, the TypeScript's starting point. Then threads.resolve_threads and
thread_synthesis.synthesize_threads run on the copy exactly as run.py calls them (late binding on, as
production runs), with the three model calls answered from the run's archive: the linker with the
proposals thread_links.json recorded, each synthesis with the installment stored for its thread
and run, and each audit supporting every claim but the second. DIR/expected.json holds what each
model call was asked, the answers, the artifacts, the render contexts and the thread tables after.

Runs in the newsroom image; bin/threads-oracle is the entry point.
"""

import argparse
import csv
import io
import json
import sqlite3
import sys
from pathlib import Path

import claude_cli
import thread_synthesis
import threads

import digest

DORMANT_AFTER = 3  # config.THREAD_DORMANT_AFTER's default, production's value
LATEBIND = {"latebind_threshold": 0.35, "latebind_max_extra": 12}  # production: THREAD_LATEBIND=true
ARTIFACTS = ("thread_assignments.json", "thread_links.json")


def rollback(conn: sqlite3.Connection, run: int) -> None:
    """The thread tables as they stood when run N started, as far as the archive can say.

    Rows created at or after N go; questions resolved at or after N reopen; a surviving thread's
    label and last_run_id return to its latest earlier installment; and its status is the decay
    rule's verdict at the start of run N, which run N's own decay would set before it links. A
    merge made after N cannot be undone and is left as it is.

    Runs after N go, every table's id sequence returns to its highest surviving id, and the
    issues of runs before N stay: bin/import-legacy derives a thread's
    status from the runs before the newest one and its label from its published updates, and
    refuses a file whose stored columns disagree with that.
    """
    c = conn.execute
    c("DELETE FROM thread_installments WHERE run_id >= ?", (run,))
    c("DELETE FROM thread_questions WHERE raised_run_id >= ?", (run,))
    c(
        "UPDATE thread_questions SET status = 'open', resolved_run_id = NULL, resolved_how = NULL WHERE resolved_run_id >= ?",
        (run,),
    )
    c("DELETE FROM thread_runs WHERE run_id >= ?", (run,))
    c("DELETE FROM threads WHERE first_run_id >= ?", (run,))
    c(
        """UPDATE threads SET
             last_run_id = (SELECT MAX(run_id) FROM thread_installments i WHERE i.thread_id = threads.id),
             label = COALESCE((SELECT cluster_story FROM thread_installments i WHERE i.thread_id = threads.id
                               ORDER BY run_id DESC LIMIT 1), label)
           WHERE last_run_id >= ?""",
        (run,),
    )
    c(
        """UPDATE threads SET status = CASE WHEN
             (SELECT COUNT(*) FROM digest_runs WHERE id > threads.last_run_id AND id < ? AND completed_at IS NOT NULL) > ?
             THEN 'dormant' ELSE 'active' END
           WHERE status IN ('active', 'dormant') AND last_run_id IS NOT NULL""",
        (run, DORMANT_AFTER),
    )
    c(
        f"DELETE FROM run_artifacts WHERE run_id <> ? OR artifact_name IN ({','.join('?' * len(ARTIFACTS))})",
        (run, *ARTIFACTS),
    )
    for table in ("source_health", "run_usage", "digests"):
        c(f"DELETE FROM {table} WHERE run_id >= ?", (run,))
    c("DELETE FROM digest_runs WHERE id > ?", (run,))
    for table in ("fetched_articles", "selections", "dedup_log", "shown_narratives", "cluster_runs"):
        c(f"DELETE FROM {table}")
    # Else run N's new threads take ids after the deleted ones here but after the survivors in the
    # import, whose identities restart after the highest id present.
    for (table,) in c("SELECT name FROM sqlite_sequence").fetchall():
        c(f"UPDATE sqlite_sequence SET seq = (SELECT COALESCE(MAX(id), 0) FROM {table}) WHERE name = ?", (table,))
    conn.commit()
    conn.execute("VACUUM")


def artifact(conn: sqlite3.Connection, run: int, name: str) -> str:
    row = conn.execute(
        "SELECT content FROM run_artifacts WHERE run_id = ? AND artifact_name = ?", (run, name)
    ).fetchone()
    if row is None:
        raise SystemExit(f"run {run} has no {name}")
    return row[0]


def run_articles(conn: sqlite3.Connection, run: int) -> dict:
    """run._load_run_articles over the archived CSVs, in the glob's sorted order."""
    arts: dict = {}
    names = conn.execute(
        "SELECT artifact_name FROM run_artifacts WHERE run_id = ? AND artifact_name GLOB 'articles_*.csv' ORDER BY artifact_name",
        (run,),
    ).fetchall()
    for (name,) in names:
        for row in csv.DictReader(io.StringIO(artifact(conn, run, name))):
            aid = row.get("article_id")
            if aid:
                arts[aid] = {"title": row.get("title", ""), "summary": row.get("summary", "")}
    return arts


def audit_answer(n: int) -> dict:
    return {"verdicts": [{"id": i, "supported": i != 2} for i in range(1, n + 1)]}


def tables(conn: sqlite3.Connection, run: int) -> dict:
    def rows(sql: str, *args) -> list[dict]:
        cur = conn.execute(sql, args)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]

    installments = rows(
        "SELECT thread_id, run_id, cluster_story, matched_score, content FROM thread_installments WHERE run_id = ? ORDER BY id",
        run,
    )
    for i in installments:
        i["content"] = json.loads(i["content"]) if i["content"] else None
    return {
        "threads": rows(
            "SELECT id, slug, label, status, first_run_id, last_run_id, merged_into FROM threads ORDER BY id"
        ),
        "installments": installments,
        "questions": rows(
            """SELECT thread_id, question, status, raised_run_id, resolved_run_id, resolved_how FROM thread_questions
               WHERE raised_run_id = ? OR resolved_run_id = ? ORDER BY thread_id, question, raised_run_id""",
            run,
            run,
        ),
        "thread_runs": rows(
            "SELECT run_id, threads_synthesized, audit_failures FROM thread_runs WHERE run_id = ?", run
        ),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, required=True)
    ap.add_argument("--run", type=int, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    run, out = args.run, args.out
    out.mkdir(parents=True, exist_ok=True)

    source = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    conn = sqlite3.connect(":memory:")
    source.backup(conn)
    source.close()
    archived_trace = json.loads(artifact(conn, run, "thread_links.json"))
    archived_assignments = json.loads(artifact(conn, run, "thread_assignments.json"))
    archived_content = {
        tid: json.loads(content)
        for tid, content in conn.execute(
            "SELECT thread_id, content FROM thread_installments WHERE run_id = ? AND content IS NOT NULL", (run,)
        )
    }
    rollback(conn, run)
    pre = sqlite3.connect(out / "pre.db")
    conn.backup(pre)
    pre.close()

    link_answer = {
        "links": [{"story": i, "thread": s["proposed_thread"]} for i, s in enumerate(archived_trace["stories"])]
    }
    empty = {"whats_new": [], "resolved": [], "new_questions": [], "still_open": []}
    synth_answers = {
        tid: {k: v for k, v in content.items() if k != "cited_ids"} for tid, content in archived_content.items()
    }
    prompts: dict = {"link": None, "synthesis": {}, "audit": {}}
    current: dict = {}

    def fake_run_sync(user, **_kw):
        prompts["link"] = user
        return json.dumps(link_answer)

    def fake_run_sonnet(user, _system, *, subagent, **_kw):
        tid = current["thread"]
        if subagent == "thread_synthesis":
            prompts["synthesis"][str(tid)] = user
            return json.dumps(synth_answers.get(tid, empty))
        prompts["audit"].setdefault(str(tid), []).append(user)
        return json.dumps(audit_answer(user.count("\nCITED SOURCE(S):\n")))

    claude_cli.run_sync = fake_run_sync
    thread_synthesis._run_sonnet = fake_run_sonnet

    clusters_doc = json.loads(artifact(conn, run, "clusters.json"))
    selected_doc = json.loads(artifact(conn, run, "selected.json"))
    stories = threads.selected_labels(clusters_doc, selected_doc)
    store = threads.ThreadStore(conn)
    trace: dict = {}
    assignments = threads.resolve_threads(stories, run, store, dormant_after=DORMANT_AFTER, trace=trace)
    by_seed = {tuple(a.article_ids): a.thread_id for a in assignments}

    def synth(recent_updates, open_questions, article_ids, arts, **kw):
        current["thread"] = next(t for seed, t in by_seed.items() if tuple(article_ids[: len(seed)]) == seed)
        return thread_synthesis.synthesize_installment(recent_updates, open_questions, article_ids, arts, **kw)

    installments, audit_failures = thread_synthesis.synthesize_threads(
        assignments, run_articles(conn, run), run, store, synth_fn=synth, **LATEBIND
    )
    contexts = {
        a.cluster_story: {**store.render_context(a.thread_id, run), "url": digest.thread_url(a.thread_id)}
        for a in assignments
        if not a.is_new
    }
    expected = {
        "run": run,
        "config": {"dormant_after": DORMANT_AFTER, **LATEBIND},
        "answers": {"link": link_answer, "synthesis": {str(t): a for t, a in synth_answers.items()}},
        "prompts": prompts,
        "assignments": [{"thread_id": a.thread_id, "is_new": a.is_new, "story": a.cluster_story} for a in assignments],
        "trace": trace,
        "installments": installments,
        "audit_failures": audit_failures,
        "contexts": contexts,
        "tables": tables(conn, run),
        "archive": {"trace": archived_trace, "assignments": archived_assignments},
    }
    (out / "expected.json").write_text(json.dumps(expected, indent=2, ensure_ascii=False))
    continued = sum(1 for a in assignments if not a.is_new)
    print(f"run {run}: {len(assignments)} stories, {continued} continued, {len(installments)} synthesized")
    return 0


if __name__ == "__main__":
    sys.exit(main())
