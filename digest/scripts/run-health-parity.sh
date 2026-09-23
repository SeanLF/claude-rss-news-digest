#!/usr/bin/env bash
# Parity of the TypeScript run-health port with the Python it replaces, over archived runs.
# usage: digest/scripts/run-health-parity.sh DB FIRST LAST [NEWSROOM_IMAGE]
# Both sides read a scratch copy of DB, so the source is never opened for writing. Exits 1 on any
# difference. Needs `npm run build` in digest/ first.
set -euo pipefail
cd "$(dirname "$0")/../.."
db=$1 first=$2 last=$3
image=${4:-digest-newsroom:latest}
work=$(mktemp -d)
cp "$db" "$work/digest.db"
# Two planted runs, because the archive never exercised most rules: 900001 trips every DB-derived
# rule at once, 900002 holds malformed artifacts that must read as "cannot judge".
sqlite3 "$work/digest.db" <<'SQL'
INSERT INTO digest_runs (id, run_at) VALUES (900001, '2099-01-01 10:25:00'), (900002, '2099-01-02 10:25:00');
INSERT INTO digests (date, html, run_id, broadcast_recipients) VALUES ('2099-01-01', '', 900001, 0);
INSERT INTO run_artifacts (run_id, artifact_name, content) VALUES
  (900001, 'cluster_health.json', '{"batches_lost": 2, "title_only_fallback": 41}'),
  (900001, 'fulltext_health.json', '{"tasks": 43, "extracted": 0, "outcome": "timeout"}'),
  (900001, 'selections.json', '{"must_know": [{"why_it_matters": ""}, {"why_it_matters": " "}, {}, {"why_it_matters": "x"}, {"why_it_matters": ""}, {"why_it_matters": "y"}, {"why_it_matters": "z"}, {"why_it_matters": "w"}]}'),
  (900001, 'write_branches.json', '{"dropped": [{"branch": "s03"}, {"branch": "s07"}]}'),
  (900001, 'thread_links.json', '{"linker_ok": false, "stories": [{"refused": "already_claimed"}, {}]}'),
  (900001, 'repair_health.json', '{"outcome": "spec_error", "detail": "coherence.md: section missing"}'),
  (900001, 'coherence_report.json', '{"results": [{"pass": false, "failed_fields": ["summary"], "failure_kinds": {"summary": "contradicted"}}, {"pass": false}]}'),
  (900002, 'cluster_health.json', '{truncated'),
  (900002, 'selections.json', '{"must_know": {}}'),
  (900002, 'thread_links.json', '{"linker_ok": "yes", "stories": ["already_claimed"]}'),
  (900002, 'write_branches.json', '{"dropped": 3}'),
  (900002, 'coherence_report.json', '[1, 2]');
SQL
ts() { THREADS_ENABLED=true DIGEST_DB_PATH="$work/digest.db" node digest/dist/cli/run-health.js "$@"; }
py() {
  docker run --rm --network none -e THREADS_ENABLED=true -e PYTHONPATH=/oracle/src \
    -v "$PWD/newsroom/src:/oracle/src:ro" -v "$PWD/digest/scripts/run_health_oracle.py:/oracle/oracle.py:ro" -v "$work:/work" \
    --entrypoint /app/.venv/bin/python "$image" /oracle/oracle.py /work/digest.db "$@"
}
for flag in "" --broadcasting; do
  { ts "$first" "$last" $flag; ts 900001 900002 $flag; } >"$work/ts$flag.jsonl"
  { py "$first" "$last" $flag; py 900001 900002 $flag; } >"$work/py$flag.jsonl"
  echo "--- ${flag:-not broadcasting}: $(wc -l <"$work/ts$flag.jsonl") runs, $(grep -c '"violations":\[\]' "$work/ts$flag.jsonl" || true) clean"
  grep -v '"violations":\[\]' "$work/ts$flag.jsonl" || true
  diff "$work/py$flag.jsonl" "$work/ts$flag.jsonl"
done
echo "parity: identical"
