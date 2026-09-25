"""How the two worker images start and report health, which is what Kamal's deploy waits on.

Kamal counts a worker container deployed once Docker reports it healthy. Each worker touches
/tmp/worker-alive every few seconds while its Temporal Worker runs (digest/src/worker.ts,
digest/python/worker.py; worker.test.ts holds the path to both); the check reads the file's age,
with no network call. The TypeScript worker migrates the product schema first, and a failed
migration kills the container before the worker polls, so the deploy stops with the old one running.
"""

import json
import os
import re
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ALIVE = "/tmp/worker-alive"
# The worker target, and the python image's last (shipped) stage.
IMAGES = {"digest/Dockerfile": "worker", "digest/python/Dockerfile": None}


def _stage(dockerfile: str, target: str | None) -> str:
    text = (ROOT / dockerfile).read_text().replace("\\\n", " ")
    stages = re.split(r"^FROM ", text, flags=re.M)
    if target is None:
        return stages[-1]
    return next(s for s in stages if re.match(rf"\S+ AS {target}\b", s))


def _healthcheck(dockerfile: str) -> tuple[str, str]:
    m = re.search(r"^HEALTHCHECK (.*?) CMD (.*)$", _stage(dockerfile, IMAGES[dockerfile]), re.M)
    assert m, f"no HEALTHCHECK in {dockerfile}'s shipped stage"
    return m.group(1), m.group(2)


@pytest.mark.parametrize("dockerfile", IMAGES)
def test_the_check_passes_on_a_fresh_file_and_fails_on_a_stale_or_missing_one(dockerfile, tmp_path):
    cmd = _healthcheck(dockerfile)[1]
    assert ALIVE in cmd
    cmd = cmd.replace(ALIVE, str(tmp_path / "alive"))

    def check() -> int:
        return subprocess.run(["sh", "-c", cmd], capture_output=True).returncode

    assert check() != 0
    (tmp_path / "alive").touch()
    assert check() == 0
    stale = time.time() - 70
    os.utime(tmp_path / "alive", (stale, stale))
    assert check() != 0


@pytest.mark.parametrize("dockerfile", IMAGES)
def test_a_deploy_learns_of_a_healthy_worker_within_seconds(dockerfile):
    # Kamal waits for "healthy"; the default 30 s interval would hold every deploy that long.
    assert "--start-interval=2s" in _healthcheck(dockerfile)[0]


def _start(tmp_path: Path, migrate_rc: int) -> tuple[int, list[str]]:
    """Runs the worker target's CMD with a stand-in `node` that logs its arguments."""
    m = re.search(r"^CMD (\[.*\])$", _stage("digest/Dockerfile", "worker"), re.M)
    assert m, "the worker target sets its own CMD"
    log = tmp_path / "calls"
    node = tmp_path / "node"
    node.write_text(f'#!/bin/sh\necho "$1" >> {log}\n[ "$1" = dist/cli/migrate.js ] && exit {migrate_rc}\nexit 0\n')
    node.chmod(0o755)
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"}
    rc = subprocess.run(json.loads(m.group(1)), env=env).returncode
    return rc, log.read_text().split() if log.exists() else []


def test_the_worker_migrates_the_schema_then_polls(tmp_path):
    assert _start(tmp_path, 0) == (0, ["dist/cli/migrate.js", "dist/worker.js"])


def test_a_failed_migration_exits_non_zero_and_never_starts_the_worker(tmp_path):
    assert _start(tmp_path, 3) == (3, ["dist/cli/migrate.js"])
