"""Records the Python `decodeLinks` activity over archived runs' Google-News links, live, as the oracle
for digest/src/activities/gnews-decode.parity.test.ts.

    decode_oracle.py PLAN.json > ORACLE.json

PLAN is newsroom/tools/gnews_oracle.py's output; each run's `render` links are what production
decoded. One activity call per run, as production makes, so each pass keeps its own deadline.
"""

import json
import sys

from temporalio.testing import ActivityEnvironment

import worker


def main() -> int:
    with open(sys.argv[1], encoding="utf-8") as f:
        plan = json.load(f)
    out = {}
    for run, entry in plan.items():
        urls = list(dict.fromkeys(entry["render"]))
        out[run] = {"links": urls, "python": ActivityEnvironment().run(worker.decode_links, urls)}
        print(f"run {run}: {out[run]['python']['outcome']}, {len(out[run]['python']['decoded'])}/{len(urls)}", file=sys.stderr)
    json.dump(out, sys.stdout, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
