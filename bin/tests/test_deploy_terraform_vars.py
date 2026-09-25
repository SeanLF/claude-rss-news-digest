"""bin/deploy hands terraform the importer from its own checkout.

Terraform ships bin/import-legacy to the box for the staged refresh and the cut-over import
(seanfloyd.dev news-digest-temporal.tf, var.news_digest_import_legacy_path). Left empty, terraform
reads a sibling checkout, which may be on another commit than the worker image this deploy built.
"""

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent.parent
DEPLOY = REPO / "bin" / "deploy"


def plan_args(tmp_path, mode):
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
PIPELINE_MODE={mode}
DRY_RUN=false
SHA=0123456789
apply_terraform
"""
    p = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env={**os.environ, "CLAUDECODE": ""}, timeout=60
    )
    assert p.returncode == 0, p.stdout + p.stderr
    return asked.read_text().splitlines()


@pytest.mark.parametrize("mode", ["staged", "temporal"])
def test_the_importer_is_this_checkouts(tmp_path, mode):
    args = plan_args(tmp_path, mode)
    assert args[0] == "plan"
    assert f"-var=news_digest_import_legacy_path={REPO.resolve()}/bin/import-legacy" in args


def test_the_site_image_is_pinned_by_the_digest_it_was_pushed_at(tmp_path):
    # seanfloyd.dev news-digest-temporal.tf runs var.news_digest_site_image_name ("digest-site") at
    # var.news_digest_site_digest, and :latest when it is empty.
    digests = tmp_path / "digests"
    digests.mkdir()
    (digests / "digest-site.digest").write_text("sha256:" + "a" * 64 + "\n")
    args = plan_args(tmp_path, "temporal")
    assert "-var=news_digest_site_digest=sha256:" + "a" * 64 in args


def test_python_mode_passes_no_importer(tmp_path):
    # The variable exists only in infra that carries the product database; python-mode infra may not
    # declare it, and terraform refuses a value for an undeclared variable.
    args = plan_args(tmp_path, "python")
    assert not [a for a in args if "news_digest_import_legacy_path" in a]


def test_a_failed_plan_never_applies_a_plan_file_left_behind(tmp_path):
    # Terraform's exit 1 is read as "changes" when a plan file exists (the -target warning bug), so
    # a file left by an interrupted deploy must not survive into the next plan.
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
PIPELINE_MODE=temporal
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
