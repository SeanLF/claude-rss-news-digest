"""bin/deploy refuses while a DigestWorkflow is running, and when it cannot tell.

A restart under a live run can fail it, park it, or (a workflow-code change it cannot replay)
leave it stuck with no alert: digest/src/workflow/deploy-safety.test.ts. The guard asks the box's
Temporal, through a stub of bin/ssh here, so each answer the box can give is played.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent.parent
DEPLOY = REPO / "bin" / "deploy"

RUNNING = [
    {
        "execution": {"workflowId": "digest-2026-09-23", "runId": "r1"},
        "type": {"name": "DigestWorkflow"},
        "status": "WORKFLOW_EXECUTION_STATUS_RUNNING",
    }
]


def run_guard(
    tmp_path, *, mode="staged", ssh_out="[]\n", ssh_rc=0, force=False, dry_run=False, fn="check_no_run_in_flight"
):
    """Source bin/deploy with bin/ssh stubbed; return (rc, output, the commands ssh was given)."""
    calls = tmp_path / "ssh-calls"
    stub = tmp_path / "ssh"
    stub.write_text(f'#!/bin/bash\nprintf "%s\\n" "$*" >> {calls}\nprintf "%s" "$STUB_OUT"\nexit "$STUB_RC"\n')
    stub.chmod(0o755)
    script = f"""
source {DEPLOY}
trap - EXIT
set +e
SCRIPT_DIR={tmp_path}
PIPELINE_MODE={mode}
FORCE={"true" if force else "false"}
DRY_RUN={"true" if dry_run else "false"}
SHA=0123456789
{fn}
"""
    env = {**os.environ, "STUB_OUT": ssh_out, "STUB_RC": str(ssh_rc), "CLAUDECODE": ""}
    p = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, timeout=60)
    made = calls.read_text().splitlines() if calls.exists() else []
    return p.returncode, p.stdout + p.stderr, made


def test_no_run_passes_and_asks_for_running_digest_workflows(tmp_path):
    rc, out, calls = run_guard(tmp_path)
    assert rc == 0, out
    assert len(calls) == 1
    assert "workflow list" in calls[0]
    assert 'WorkflowType="DigestWorkflow" AND ExecutionStatus="Running"' in calls[0]


def test_a_running_digest_refuses_and_names_it(tmp_path):
    rc, out, _ = run_guard(tmp_path, ssh_out=json.dumps(RUNNING))
    assert rc == 1
    assert "digest-2026-09-23" in out
    assert "--force" in out


def test_force_deploys_under_a_running_digest_loudly(tmp_path):
    rc, out, _ = run_guard(tmp_path, ssh_out=json.dumps(RUNNING), force=True)
    assert rc == 0
    assert "digest-2026-09-23" in out


def test_temporal_mode_is_guarded_too(tmp_path):
    rc, _, _ = run_guard(tmp_path, mode="temporal", ssh_out=json.dumps(RUNNING))
    assert rc == 1


@pytest.mark.parametrize(
    ("ssh_out", "ssh_rc"),
    [
        ("", 255),  # ssh could not reach the box
        ("", 1),  # bin/ssh failed before ssh ran
        # The CLI prints an empty array and then fails (seen: a namespace that is not found).
        ("[\n]\n", 1),
        ("", 0),  # nothing printed
        ('[\n{"execution": {"workflowId": "dige', 0),  # truncated
        ('{"not": "a list"}', 0),
    ],
)
def test_an_answer_that_is_not_a_complete_list_refuses(tmp_path, ssh_out, ssh_rc):
    rc, out, _ = run_guard(tmp_path, ssh_out=ssh_out, ssh_rc=ssh_rc)
    assert rc == 1, out


def test_force_deploys_past_an_unreachable_temporal(tmp_path):
    rc, _, _ = run_guard(tmp_path, ssh_out="", ssh_rc=255, force=True)
    assert rc == 0


def test_a_box_without_temporal_yet_has_no_run_to_wait_for(tmp_path):
    rc, out, _ = run_guard(tmp_path, ssh_rc=3)
    assert rc == 0, out


def test_python_mode_never_asks(tmp_path):
    rc, _, calls = run_guard(tmp_path, mode="python", ssh_out=json.dumps(RUNNING))
    assert rc == 0
    assert calls == []


def test_dry_run_never_asks(tmp_path):
    rc, _, calls = run_guard(tmp_path, dry_run=True, ssh_out=json.dumps(RUNNING))
    assert rc == 0
    assert calls == []


# The pause and the guard share one stub answer here, so each case is played through pause_schedule.
def test_a_pause_that_fails_refuses(tmp_path):
    rc, out, _ = run_guard(tmp_path, fn="pause_schedule", ssh_out="connection refused", ssh_rc=1)
    assert rc == 1, out


def test_a_pause_that_fails_deploys_under_force(tmp_path):
    rc, _, _ = run_guard(tmp_path, fn="pause_schedule", ssh_out="connection refused", ssh_rc=1, force=True)
    assert rc == 0


def test_the_pause_is_followed_by_the_guard(tmp_path):
    rc, _, calls = run_guard(tmp_path, fn="pause_schedule", ssh_out=json.dumps(RUNNING))
    assert rc == 1
    assert "digest-schedule pause" in calls[0]
    assert "workflow list" in calls[1]
