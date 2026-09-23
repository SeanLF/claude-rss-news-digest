"""db.prepare_for_web as an oracle for the TypeScript record (digest/src/activities/record.parity.test.ts):
for each DIR given, reads DIR/web.html and writes DIR/web.archive.html, the web copy save_digest stores.
Runs in the newsroom image; bin/record-oracle is the entry point."""

import sys
from pathlib import Path

import db


def main() -> int:
    for d in map(Path, sys.argv[1:]):
        (d / "web.archive.html").write_text(db.prepare_for_web((d / "web.html").read_text()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
