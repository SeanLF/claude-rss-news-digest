"""bin/deploy's pre-migration snapshot asks the box for a dump taken now.

The infra repo's bin/backup-volumes copies the box's newest nightly pg_dump by default, which can be
a day old; a migration needs the dump from just before it (docs/2026-09-24-web-tier-and-ops-decisions.md,
"Backups: one producer"). bin/backup-volumes is stubbed here, so each answer it can give is played.
"""

import os
import subprocess
from pathlib import Path

REPO = Path(__file__).parent.parent.parent
DEPLOY = REPO / "bin" / "deploy"


def backup(tmp_path, *, rc_fresh=0, dry_run=False):
    """Run backup_data against a stub backup-volumes; return (rc, output, each call's arguments)."""
    infra = tmp_path / "infra"
    (infra / "bin").mkdir(parents=True)
    calls = tmp_path / "calls"
    stub = infra / "bin" / "backup-volumes"
    # Exits rc_fresh when given --fresh-digest-dump, 0 otherwise.
    stub.write_text(
        f'#!/bin/bash\necho "[$*]" >> {calls}\n'
        f'case " $* " in *" --fresh-digest-dump "*) exit {rc_fresh} ;; esac\nexit 0\n'
    )
    stub.chmod(0o755)
    script = f"""
source {DEPLOY}
trap - EXIT
set +e
INFRA_DIR={infra}
DRY_RUN={"true" if dry_run else "false"}
backup_data
"""
    p = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env={**os.environ, "CLAUDECODE": ""}, timeout=60
    )
    made = calls.read_text().splitlines() if calls.exists() else []
    return p.returncode, p.stdout + p.stderr, made


def test_the_snapshot_asks_for_a_fresh_digest_dump(tmp_path):
    rc, out, calls = backup(tmp_path)
    assert rc == 0, out
    assert calls == ["[--fresh-digest-dump]"]


def test_the_dry_run_names_the_flag_and_runs_nothing(tmp_path):
    rc, out, calls = backup(tmp_path, dry_run=True)
    assert rc == 0, out
    assert calls == []
    assert "backup-volumes --fresh-digest-dump" in out


def test_a_failed_fresh_dump_is_loud_and_does_not_stop_the_deploy(tmp_path):
    rc, out, calls = backup(tmp_path, rc_fresh=1)
    assert rc == 0, out
    assert "FAILED" in out
    assert calls == ["[--fresh-digest-dump]", "[--status]"]


def test_an_infra_checkout_without_the_flag_is_named_and_still_backed_up(tmp_path):
    # Exit 64 is backup-volumes' usage error: a checkout older than --fresh-digest-dump. That version
    # snapshots digest.db on every run and copies no Postgres dump, so a plain run still saves the
    # SQLite side, and the output must say the Postgres dump is missing.
    rc, out, calls = backup(tmp_path, rc_fresh=64)
    assert rc == 0, out
    assert "--fresh-digest-dump" in out
    assert "NO Postgres dump" in out
    assert calls == ["[--fresh-digest-dump]", "[]"]
    assert "Volumes backed up" in out
