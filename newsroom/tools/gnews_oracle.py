"""The Python's Google-News decode plan, as an oracle for the TypeScript port (digest/src/activities/gnews.ts).

    gnews_oracle.py DB RUN...     writes JSON to stdout, one entry per run

For each archived run: `render`, the links digest._resolve_gnews_links hands gnews.resolve at render,
in order, captured with the network stubbed out; `prefetch`, the links gnews.prefetch_selected
decoded after SELECT (every article id of every selected story); and `shipped_raw`, the rendered
links that the published issue still carries as news.google.com. Reads the database, never writes it.
Runs in the newsroom image, with newsroom/src on PYTHONPATH.
"""

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

import config
import gnews

import digest


def artifact(db: sqlite3.Connection, run: int, name: str) -> str:
    row = db.execute("SELECT content FROM run_artifacts WHERE run_id=? AND artifact_name=?", (run, name)).fetchone()
    if not row:
        raise SystemExit(f"run {run} has no {name}")
    return row[0]


def render_plan(selections: dict, index_json: str) -> list[str]:
    seen: list[str] = []

    def spy(url: str, **kw) -> None:
        seen.append(url)

    gnews.resolve, gnews.wait_for_prefetch = spy, lambda timeout: True
    config.GNEWS_RESOLVE_ENABLED = True
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "article_index.json").write_text(index_json)
        digest.resolve_article_ids(selections, claude_input_dir=Path(d))
    return seen


def prefetch_plan(selected: dict, index: dict) -> list[str]:
    urls: list[str] = []
    for tier in ("must_know", "should_know"):
        for story in selected.get(tier) or []:
            for aid in story.get("article_ids") or []:
                url = (index.get(aid) or {}).get("url")
                if url and gnews.is_gnews_url(url) and url not in urls:
                    urls.append(url)
    return urls


def main() -> int:
    db = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
    out = {}
    for run in map(int, sys.argv[2:]):
        index_json = artifact(db, run, "article_index.json")
        render = render_plan(json.loads(artifact(db, run, "selections.json")), index_json)
        html = (db.execute("SELECT html FROM digests WHERE run_id=?", (run,)).fetchone() or [""])[0]
        out[run] = {
            "render": render,
            "prefetch": prefetch_plan(json.loads(artifact(db, run, "selected.json")), json.loads(index_json)),
            "shipped_raw": [u for u in dict.fromkeys(render) if u.replace("&", "&amp;") in html or u in html],
        }
    json.dump(out, sys.stdout, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
