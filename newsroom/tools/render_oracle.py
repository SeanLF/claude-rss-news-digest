"""The Python render as an oracle for the TypeScript port (digest/src/render): the same renders
replay.py produces, with the clock pinned and Google-News resolution off, so two runs of it are
byte-identical and a port can be held to its bytes.

    render_oracle.py --run 300 --at 2026-09-18T10:42:00Z --out DIR      a run's tail, via replay.replay
    render_oracle.py --fixture F.json --at ... --out DIR                 a resolved selections fixture

Writes DIR/web.html, DIR/email.html and, for a run, DIR/thread_context.json: the context
attach_thread_context gave each story, by cluster_id, which the port takes as input.
Runs in the newsroom image; bin/render-oracle is the entry point.
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import config
import render
import render_email
import replay

import digest


def pin_clock(at: datetime) -> None:
    class Pinned(datetime):
        @classmethod
        def now(cls, tz=None):
            return at.astimezone(tz) if tz else at.replace(tzinfo=None)

    render.datetime = Pinned
    render_email.datetime = Pinned


def run_mode(run_id: int, out: Path) -> None:
    config.THREADS_ENABLED = True
    captured: dict[str, dict] = {}
    attach = digest.attach_thread_context

    def spy(selections: dict, **kw) -> dict:
        result = attach(selections, **kw)
        for tier in ("must_know", "should_know"):
            for item in result.get(tier, []):
                if item.get("thread"):
                    captured[item["cluster_id"]] = item["thread"]
        return result

    digest.attach_thread_context = spy
    report = replay.replay(run_id, out / "artifacts")
    (out / "web.html").write_text(report.web_path.read_text())
    (out / "email.html").write_text(report.email_path.read_text())
    (out / "thread_context.json").write_text(json.dumps(captured, indent=2, ensure_ascii=False))
    print(replay.format_report(report))


def fixture_mode(fixture: Path, out: Path) -> None:
    selections = json.loads(fixture.read_text())
    web = out / "web.html"
    web.write_text(render.render_digest(selections, config.TEMPLATE_FILE))
    render.replace_placeholders(web, selections, config.STYLES_FILE, render.extract_preheader(selections))
    (out / "email.html").write_text(render_email.render_email(selections))


def main() -> int:
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--run", type=int)
    src.add_argument("--fixture", type=Path)
    ap.add_argument("--at", required=True, help="the pinned render time, ISO 8601 in UTC")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    at = datetime.fromisoformat(args.at.replace("Z", "+00:00")).astimezone(UTC)
    config.GNEWS_RESOLVE_ENABLED = False
    pin_clock(at)
    args.out.mkdir(parents=True, exist_ok=True)
    if args.run is not None:
        run_mode(args.run, args.out)
    else:
        fixture_mode(args.fixture, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
