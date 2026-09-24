"""bin/deploy refuses while a DigestWorkflow is running, and when it cannot tell.

A run is pinned to the worker build that started it, and a deploy stops the box's only worker of that
build, so the run sits with no alert (digest/src/deployment.test.ts). The guard asks the box's
Temporal, through a stub of bin/ssh here, so each answer the box can give is played.

Only "temporal" refuses. In "staged" the TypeScript runs are rehearsals on a scratch database and
Python is live, so a Temporal problem there warns and must not block Python's deploy.
"""

import base64
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


def _keyword_list(values):
    return {
        "metadata": {
            "encoding": base64.b64encode(b"json/plain").decode(),
            "type": base64.b64encode(b"KeywordList").decode(),
        },
        "data": base64.b64encode(json.dumps(values).encode()).decode(),
    }


# As `workflow list -o json` shows a run whose workflow task keeps failing (server 1.32, observed on a
# run stuck on a nondeterminism error): the TemporalReportedProblems search attribute.
STUCK = [
    {
        "execution": {"workflowId": "digest-2026-09-22", "runId": "r0"},
        "type": {"name": "DigestWorkflow"},
        "status": "WORKFLOW_EXECUTION_STATUS_RUNNING",
        "searchAttributes": {
            "indexedFields": {
                "BuildIds": _keyword_list(["unversioned"]),
                "TemporalReportedProblems": _keyword_list(
                    ["category=WorkflowTaskFailed", "cause=WorkflowTaskFailedCauseNonDeterministicError"]
                ),
            }
        },
    }
]


def run_guard(
    tmp_path, *, mode="temporal", ssh_out="[]\n", ssh_rc=0, force=False, dry_run=False, fn="check_no_run_in_flight"
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


def test_the_refusal_says_how_to_clear_a_held_run_and_a_stuck_one(tmp_path):
    rc, out, _ = run_guard(tmp_path, ssh_out=json.dumps(RUNNING))
    assert rc == 1
    assert "approve or reject" in out
    assert "workflow terminate" in out


def test_a_stuck_run_is_named_as_stuck(tmp_path):
    rc, out, _ = run_guard(tmp_path, ssh_out=json.dumps(RUNNING + STUCK))
    assert rc == 1
    assert "digest-2026-09-22 (stuck" in out
    assert "digest-2026-09-23 (stuck" not in out


def test_staged_warns_under_a_running_digest_and_deploys(tmp_path):
    rc, out, calls = run_guard(tmp_path, mode="staged", ssh_out=json.dumps(RUNNING))
    assert rc == 0
    assert "digest-2026-09-23" in out
    assert len(calls) == 1


def test_staged_warns_past_an_unreachable_temporal(tmp_path):
    rc, out, _ = run_guard(tmp_path, mode="staged", ssh_out="", ssh_rc=255)
    assert rc == 0
    assert "could not list" in out


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


def test_a_pause_that_fails_in_staged_warns_and_deploys(tmp_path):
    rc, out, _ = run_guard(tmp_path, mode="staged", fn="pause_schedule", ssh_out="connection refused", ssh_rc=1)
    assert rc == 0
    assert "could not pause" in out


def test_a_pause_that_fails_deploys_under_force(tmp_path):
    rc, _, _ = run_guard(tmp_path, fn="pause_schedule", ssh_out="connection refused", ssh_rc=1, force=True)
    assert rc == 0


def test_the_pause_is_followed_by_the_guard(tmp_path):
    rc, _, calls = run_guard(tmp_path, fn="pause_schedule", ssh_out=json.dumps(RUNNING))
    assert rc == 1
    assert "digest-schedule pause" in calls[0]
    assert "workflow list" in calls[1]


# set_current_version runs digest/src/cli/set-current.ts inside the new worker container after the
# apply; its exit code and lines are played here.
CURRENT = "current version: digest:abc1234\n"
STRANDED = CURRENT + "stranded: digest-2026-09-23 pinned to 0ld0000\n"


def test_set_current_runs_in_the_worker_container(tmp_path):
    rc, out, calls = run_guard(tmp_path, fn="set_current_version", ssh_out=CURRENT)
    assert rc == 0, out
    assert len(calls) == 1
    assert "docker exec news-digest-worker node dist/cli/set-current.js" in calls[0]
    assert "digest:abc1234" in out


@pytest.mark.parametrize("force", [False, True])
def test_a_build_that_cannot_be_made_current_fails_the_deploy(tmp_path, force):
    rc, out, _ = run_guard(tmp_path, fn="set_current_version", ssh_out="no pollers", ssh_rc=1, force=force)
    assert rc == 1
    assert "NOT the current version" in out


def test_a_run_waiting_on_a_build_with_no_worker_is_named(tmp_path):
    # set-current's failure output when the apply's bootstrap started a run that no worker has taken;
    # digest/src/deployment.test.ts holds waitingLine to this exact text.
    waiting = (
        "not current: Error: no worker of digest:abc1234 polled digest within 120 s\n"
        "waiting: digest-2026-10-05 -- no worker has taken these; they start once a polling build is current\n"
    )
    rc, out, _ = run_guard(tmp_path, fn="set_current_version", ssh_out=waiting, ssh_rc=1)
    assert rc == 1
    assert "no worker has taken digest run(s) digest-2026-10-05" in out


def test_a_waiting_list_that_failed_claims_no_waiting_run(tmp_path):
    out_ = "not current: Error: x\ncould not list the runs no worker has taken: Error: y\n"
    rc, out, _ = run_guard(tmp_path, fn="set_current_version", ssh_out=out_, ssh_rc=1)
    assert rc == 1
    assert "no worker has taken digest run" not in out


def test_a_stranded_run_fails_a_temporal_deploy_and_says_how_to_move_it(tmp_path):
    rc, out, _ = run_guard(tmp_path, fn="set_current_version", ssh_out=STRANDED, ssh_rc=2)
    assert rc == 1
    assert "digest-2026-09-23 pinned to 0ld0000" in out
    assert "update-options -w <id> --versioning-override-behavior pinned" in out
    assert "--versioning-override-build-id abc1234" in out


def test_a_stranded_rehearsal_warns_in_staged(tmp_path):
    rc, out, _ = run_guard(tmp_path, mode="staged", fn="set_current_version", ssh_out=STRANDED, ssh_rc=2)
    assert rc == 0
    assert "digest-2026-09-23" in out


@pytest.mark.parametrize(("mode", "dry_run"), [("python", False), ("temporal", True)])
def test_set_current_never_asks_in_python_or_a_dry_run(tmp_path, mode, dry_run):
    rc, _, calls = run_guard(tmp_path, mode=mode, dry_run=dry_run, fn="set_current_version", ssh_out=CURRENT)
    assert rc == 0
    assert calls == []


def test_a_listing_failure_after_set_current_says_current_but_unchecked(tmp_path):
    rc, out, _ = run_guard(
        tmp_path, fn="set_current_version", ssh_out=CURRENT + "could not list the running digests: boom\n", ssh_rc=3
    )
    assert rc == 0
    assert "NOT the current version" not in out
    assert "could not be checked" in out


# Before the apply, current is pointed at the build being shipped, so a run the apply's bootstrap
# starts waits for the new worker instead of pinning to the old one.
def test_current_is_pointed_at_the_shipped_build_before_its_worker_exists(tmp_path):
    rc, out, calls = run_guard(tmp_path, fn="point_current_at_new_build; echo MOVED=$CURRENT_MOVED")
    assert rc == 0, out
    assert (
        "set-current-version --deployment-name digest --build-id 0123456 --allow-no-pollers --ignore-missing-task-queues --yes"
        in calls[0]
    )
    assert "MOVED=true" in out


def test_pointing_current_is_skipped_under_skip_build(tmp_path):
    rc, out, calls = run_guard(tmp_path, fn="SKIP_BUILD=true; point_current_at_new_build; echo MOVED=$CURRENT_MOVED")
    assert rc == 0
    assert calls == []
    assert "MOVED=false" in out


@pytest.mark.parametrize(("ssh_rc", "moved"), [(3, "false"), (1, "false")])
def test_pointing_current_never_fails_the_deploy(tmp_path, ssh_rc, moved):
    rc, out, _ = run_guard(
        tmp_path, fn="point_current_at_new_build; echo MOVED=$CURRENT_MOVED", ssh_out="nope", ssh_rc=ssh_rc
    )
    assert rc == 0
    assert f"MOVED={moved}" in out


def _cleanup(tmp_path, ssh_rc):
    return run_guard(
        tmp_path,
        fn="update_deployment_status() { :; }; DEPLOYMENT_SUCCEEDED=true; CURRENT_MOVED=true; SCHEDULE_PAUSED=true; true; cleanup",
        ssh_out=CURRENT,
        ssh_rc=ssh_rc,
    )


def test_the_exit_trap_points_current_at_the_running_worker_then_resumes(tmp_path):
    _, out, calls = _cleanup(tmp_path, 0)
    assert "set-current.js" in calls[0]
    assert any("systemctl restart news-digest-temporal-bootstrap.service" in c for c in calls), out


def test_the_exit_trap_leaves_the_schedule_paused_when_no_build_can_be_made_current(tmp_path):
    _, out, calls = _cleanup(tmp_path, 1)
    assert "set-current.js" in calls[0]
    assert not any("systemctl restart" in c for c in calls)
    assert "stays PAUSED" in out


def test_main_makes_the_build_current_before_it_restores_the_schedule():
    main = DEPLOY.read_text().split("\nmain() {", 1)[1]
    order = [
        main.index(step)
        for step in (
            "pause_schedule ||",
            "point_current_at_new_build",
            "apply_terraform",
            "set_current_version ||",
            "resume_schedule ||",
        )
    ]
    assert order == sorted(order)
