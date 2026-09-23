"""The Python side of the run-health parity check: one JSON line per run, in the shape
digest/src/cli/run-health.ts prints. usage: run_health_oracle.py DB FIRST LAST [--broadcasting]
Run inside the newsroom image with newsroom/src on the path (digest/scripts/run-health-parity.sh)."""

import json
import sys
from pathlib import Path

import db
import run_health

args = [a for a in sys.argv[1:] if not a.startswith("--")]
db.init(Path(args[0]), Path("/nonexistent"), apply_migrations=False)
db._state.broadcasting = "--broadcasting" in sys.argv
for run in range(int(args[1]), int(args[2]) + 1):
    health = db.get_run_health(run)
    report = db._connect(args[0]).execute(
        "SELECT content FROM run_artifacts WHERE run_id=? AND artifact_name='coherence_report.json'", (run,)
    ).fetchone()
    print(
        json.dumps(
            {
                "run": run,
                "violations": run_health.violations(health),
                "kinds": run_health.coherence_kind_counts(report[0] if report else None),
            },
            separators=(",", ":"),
            ensure_ascii=False,
        )
    )
