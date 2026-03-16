#!/bin/bash
set -euo pipefail

# If args start with a dash, treat them as flags to run.py
# Otherwise, execute the command as-is (e.g., "claude --version", "bin/migrate")
if [ $# -eq 0 ]; then
  exec .venv/bin/python src/run.py
elif [ "${1:0:1}" = "-" ]; then
  exec .venv/bin/python src/run.py "$@"
else
  exec "$@"
fi
