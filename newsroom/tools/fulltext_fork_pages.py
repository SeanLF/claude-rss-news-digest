"""Fetch the fulltext fork's corpus once (docs/2026-09-23-fulltext-extractor-fork.md).

For every run from FIRST_RUN with archived selected.json and article_index.json: the candidates
production fetches, fetched by trafilatura's own fetch_url under the production config, the bytes
saved, and the production extract recorded as the reference every other arm is scored against.

Usage (newsroom image): python3 tools/fulltext_fork_pages.py DB_PATH OUT_DIR [FIRST_RUN]
"""

import json
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import config
import trafilatura
from fulltext import _candidate_article_ids, _trafilatura_config


def main() -> None:
    db_path, out = sys.argv[1], Path(sys.argv[2])
    first = int(sys.argv[3]) if len(sys.argv) > 3 else 300
    (out / "html").mkdir(parents=True, exist_ok=False)  # a fresh directory, never a refill
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)

    def artifact(run: int, name: str):
        row = db.execute(
            "SELECT content FROM run_artifacts WHERE run_id=? AND artifact_name=? ORDER BY id DESC LIMIT 1", (run, name)
        ).fetchone()
        return json.loads(row[0]) if row else None

    tasks = []
    for (run,) in db.execute("SELECT id FROM digest_runs WHERE id>=? ORDER BY id", (first,)).fetchall():
        selected, index = artifact(run, "selected.json"), artifact(run, "article_index.json")
        if not selected or not index:
            continue
        for aid in _candidate_article_ids(selected, config.FULLTEXT_PER_STORY):
            if aid in index:
                tasks.append((run, aid, index[aid]["url"]))

    def one(task):
        run, aid, url = task
        key = f"{run}-{aid}"
        try:
            html = trafilatura.fetch_url(url, config=_trafilatura_config())
        except Exception as e:  # recorded as a failed fetch, like production
            return {"key": key, "run": run, "id": aid, "url": url, "fetched": False, "error": type(e).__name__}
        if not html:
            return {"key": key, "run": run, "id": aid, "url": url, "fetched": False}
        if config.FULLTEXT_MAX_DOC_CHARS > 0 and len(html) > config.FULLTEXT_MAX_DOC_CHARS:
            return {
                "key": key,
                "run": run,
                "id": aid,
                "url": url,
                "fetched": False,
                "error": "too_large",
            }  # as _fetch_one
        (out / "html" / f"{key}.html").write_text(html, encoding="utf-8")
        try:
            text = trafilatura.extract(html, include_comments=False, include_tables=False) or ""
        except Exception as e:  # recorded per page, like production
            return {
                "key": key,
                "run": run,
                "id": aid,
                "url": url,
                "fetched": True,
                "trafilatura": "",
                "error": type(e).__name__,
            }
        # Raw: every arm, this one included, is scrubbed and truncated the same way on the TypeScript side.
        return {"key": key, "run": run, "id": aid, "url": url, "fetched": True, "trafilatura": text.strip()}

    with ThreadPoolExecutor(max_workers=6) as pool:
        rows = list(pool.map(one, tasks))
    (out / "config.json").write_text(
        json.dumps({"max_chars": config.FULLTEXT_MAX_CHARS, "per_story": config.FULLTEXT_PER_STORY})
    )
    with (out / "pages.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    fetched = sum(r["fetched"] for r in rows)
    print(f"{len(rows)} candidates from runs >= {first}; {fetched} fetched; {out}")


if __name__ == "__main__":
    main()
