"""bin/deploy's terraform plan: which resources it targets and which variables it passes.

seanfloyd-infra's tofu/news-digest.tf and tofu/news-digest-temporal.tf describe the Temporal pipeline alone since
the cut-over. Terraform refuses a -var for a variable it does not declare, so the Python pipeline's
and circulation's digests and the importer's path must not be passed, and a -target of a resource it
no longer has would only plan its destroy.
"""

import os
import subprocess
from pathlib import Path

REPO = Path(__file__).parent.parent.parent
DEPLOY = REPO / "bin" / "deploy"


def plan_args(tmp_path):
    """Run apply_terraform with bin/tf stubbed to 'no changes'; return the plan's arguments."""
    infra = tmp_path / "infra"
    (infra / "bin").mkdir(parents=True)
    asked = tmp_path / "tf-args"
    tf = infra / "bin" / "tf"
    tf.write_text(f'#!/bin/bash\nprintf "%s\\n" "$@" >> {asked}\nexit 0\n')
    tf.chmod(0o755)
    digests = tmp_path / "digests"
    digests.mkdir(exist_ok=True)
    script = f"""
source {DEPLOY}
trap - EXIT
set +e
INFRA_DIR={infra}
DIGEST_DIR={digests}
DRY_RUN=false
SHA=0123456789
apply_terraform
"""
    p = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env={**os.environ, "CLAUDECODE": ""}, timeout=60
    )
    assert p.returncode == 0, p.stdout + p.stderr
    return asked.read_text().splitlines()


def test_no_retired_variable_is_passed(tmp_path):
    args = plan_args(tmp_path)
    assert args[0] == "plan"
    for gone in ("news_digest_newsroom_digest", "news_digest_circulation_digest", "news_digest_import_legacy_path"):
        assert not [a for a in args if gone in a], gone
    # Negative control: the variables that stay are passed.
    assert "-var=news_digest_deploy_sha=0123456789" in args


def test_the_targets_are_the_temporal_pipeline_and_the_site_swap(tmp_path):
    targets = {a.removeprefix("-target=") for a in plan_args(tmp_path) if a.startswith("-target=")}
    assert {
        "null_resource.news_digest_temporal_db",
        "null_resource.news_digest_workers",
        "null_resource.digest_server",
        "null_resource.news_digest_site",
    } <= targets
    for gone in (
        "news_digest_service",
        "news_digest_timer",
        "news_digest_health_check",
        "news_digest_deadman",
        "news_digest_retire_python",
    ):
        assert f"null_resource.{gone}" not in targets


def test_each_image_is_pinned_by_the_digest_it_was_pushed_at(tmp_path):
    # seanfloyd-infra tofu/news-digest-temporal.tf runs each image at its digest, and :latest when it is empty.
    digests = tmp_path / "digests"
    digests.mkdir()
    for name, c in (("digest-site", "a"), ("digest-worker", "b"), ("digest-python", "c")):
        (digests / f"{name}.digest").write_text("sha256:" + c * 64 + "\n")
    args = plan_args(tmp_path)
    assert "-var=news_digest_site_digest=sha256:" + "a" * 64 in args
    assert "-var=news_digest_worker_digest=sha256:" + "b" * 64 in args
    assert "-var=news_digest_python_digest=sha256:" + "c" * 64 in args


def pipeline_mode(tmp_path, answer):
    infra = tmp_path / "infra"
    (infra / "bin").mkdir(parents=True, exist_ok=True)
    tf = infra / "bin" / "tf"
    tf.write_text(f"#!/bin/bash\ncat >/dev/null\nprintf '%s' '{answer}'\n")
    tf.chmod(0o755)
    script = f"""
source {DEPLOY}
trap - EXIT
set +e
INFRA_DIR={infra}
read_pipeline_mode
"""
    p = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env={**os.environ, "CLAUDECODE": ""}, timeout=60
    )
    return p.returncode, p.stdout + p.stderr


def test_the_pipeline_mode_must_be_temporal(tmp_path):
    assert pipeline_mode(tmp_path, '"temporal"')[0] == 0
    for other in ('"python"', '"staged"', "", "Warning: value for undeclared variable"):
        rc, out = pipeline_mode(tmp_path, other)
        assert rc == 1, (other, out)
        assert "not temporal" in out


def test_a_failed_plan_never_applies_a_plan_file_left_behind(tmp_path):
    # A file left by an interrupted deploy must not survive into the next plan.
    stale = Path("/tmp/news-digest-tfplan")
    stale.write_text("stale plan from an interrupted deploy\n")
    infra = tmp_path / "infra"
    (infra / "bin").mkdir(parents=True)
    asked = tmp_path / "tf-args"
    tf = infra / "bin" / "tf"
    tf.write_text(
        f'#!/bin/bash\nprintf "%s\\n" "$1" >> {asked}\n[ "$1" = plan ] && {{ echo "Error: undeclared variable" >&2; exit 1; }}\nexit 0\n'
    )
    tf.chmod(0o755)
    digests = tmp_path / "digests"
    digests.mkdir()
    script = f"""
source {DEPLOY}
trap - EXIT
INFRA_DIR={infra}
DIGEST_DIR={digests}
DRY_RUN=false
SHA=0123456789
apply_terraform
"""
    try:
        p = subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True, env={**os.environ, "CLAUDECODE": ""}, timeout=60
        )
    finally:
        stale.unlink(missing_ok=True)
    assert p.returncode != 0, p.stdout + p.stderr
    assert "apply" not in asked.read_text().splitlines()


def test_a_plan_that_exits_1_is_never_applied_even_with_a_plan_file(tmp_path):
    # Exit 1 is an error. bin/tf passes -detailed-exitcode's 2 through, so nothing reads a 1 as
    # "changes" any more, whatever file the failed plan left.
    infra = tmp_path / "infra"
    (infra / "bin").mkdir(parents=True)
    asked = tmp_path / "tf-args"
    tf = infra / "bin" / "tf"
    tf.write_text(
        f'#!/bin/bash\nprintf "%s\\n" "$1" >> {asked}\n'
        'if [ "$1" = plan ]; then for a; do case $a in -out=*) : > "${a#-out=}";; esac; done; exit 1; fi\nexit 0\n'
    )
    tf.chmod(0o755)
    digests = tmp_path / "digests"
    digests.mkdir()
    script = f"""
source {DEPLOY}
trap - EXIT
INFRA_DIR={infra}
DIGEST_DIR={digests}
DRY_RUN=false
SHA=0123456789
apply_terraform
"""
    p = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env={**os.environ, "CLAUDECODE": ""}, timeout=60
    )
    assert p.returncode != 0, p.stdout + p.stderr
    assert "apply" not in asked.read_text().splitlines()
