"""`make deploy` hands HEAD's full SHA to seanfloyd-infra's bin/deploy-digest, found through INFRA_DIR.

Run in a scratch git repository with the repo's Makefile, so HEAD and .env are the test's own.
"""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def checkout(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t.invalid"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "x"], check=True)
    head = subprocess.run([*git, "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    return repo, head


def _deploy(repo: Path) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "INFRA_DIR"}
    return subprocess.run(
        ["make", "-s", "-f", str(ROOT / "Makefile"), "-C", str(repo), "deploy"], env=env, capture_output=True, text=True
    )


def test_runs_deploy_digest_with_heads_full_sha(checkout, tmp_path):
    repo, head = checkout
    script = tmp_path / "infra" / "bin" / "deploy-digest"
    script.parent.mkdir(parents=True)
    script.write_text(f'#!/bin/sh\necho "$@" > {tmp_path}/args\n')
    script.chmod(0o755)
    (repo / ".env").write_text(f"INFRA_DIR={tmp_path / 'infra'}\n")
    r = _deploy(repo)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "args").read_text().split() == [head]


def test_refuses_without_infra_dir(checkout):
    r = _deploy(checkout[0])
    assert r.returncode != 0
    assert "INFRA_DIR is unset" in r.stderr


def test_refuses_when_deploy_digest_is_missing(checkout, tmp_path):
    repo, _ = checkout
    (repo / ".env").write_text(f"INFRA_DIR={tmp_path / 'nowhere'}\n")
    r = _deploy(repo)
    assert r.returncode != 0
    assert f"{tmp_path / 'nowhere'}/bin/deploy-digest" in r.stderr
