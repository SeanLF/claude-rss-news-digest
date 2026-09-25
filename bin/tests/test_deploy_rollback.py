"""The box keeps the last three images of each repository, and bin/deploy --rollback deploys one.

The box's weekly cleanup (seanfloyd.dev scripts/server/cleanup.sh) runs `docker image prune -af`,
which removes every image no container references, stopped or not, so a replaced worker's or site's
image never survives a week. bin/deploy leaves a stopped container per pushed image, labelled by repository, and removes all
but the newest three, so a rollback by digest finds its image on the box. Here the box is played by
a bash that runs the command bin/ssh is given, against a docker stub that keeps its containers in a
file, newest first, as `docker ps` lists them.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent.parent
DEPLOY = REPO / "bin" / "deploy"

DOCKER = r"""#!/bin/bash
db="$DOCKER_DB"; touch "$db"
case $1 in
  create)
    shift
    while [ $# -gt 0 ]; do
      case $1 in --name) n=$2; shift 2 ;; --label) l=$2; shift 2 ;; *) ref=$1; shift ;; esac
    done
    if awk -v n="$n" '$1 == n { f = 1 } END { exit !f }' "$db"; then echo "name in use: $n" >&2; exit 1; fi
    { echo "$n $l $ref"; cat "$db"; } > "$db.tmp" && mv "$db.tmp" "$db"
    ;;
  ps)
    lbl=${4#label=}
    awk -v l="$lbl" '$2 == l { print $1 }' "$db"
    ;;
  rm)
    shift; [ "$1" = -f ] && shift
    for n in "$@"; do awk -v n="$n" '$1 != n' "$db" > "$db.tmp" && mv "$db.tmp" "$db"; done
    ;;
esac
"""


def digest(c):
    return "sha256:" + c * 64


def box(tmp_path):
    """bin/ssh stub that runs its command locally, with the docker stub first on PATH."""
    stubs = tmp_path / "stubs"
    stubs.mkdir(exist_ok=True)
    (stubs / "docker").write_text(DOCKER)
    (stubs / "docker").chmod(0o755)
    (tmp_path / "ssh").write_text('#!/bin/bash\nbash -c "$*"\n')
    (tmp_path / "ssh").chmod(0o755)
    return {"PATH": f"{stubs}:{os.environ['PATH']}", "DOCKER_DB": str(tmp_path / "containers")}


def keep(tmp_path, digests, *, dry_run=False, ssh_fails=False):
    """Run keep_images_on_box once with DIGEST_DIR holding {name: digest}; return (rc, output)."""
    d = tmp_path / "digests"
    if d.exists():
        for f in d.iterdir():
            f.unlink()
    d.mkdir(exist_ok=True)
    for name, dg in digests.items():
        (d / f"{name}.digest").write_text(dg + "\n")
    script = f"""
source {DEPLOY}
trap - EXIT
set +e
SCRIPT_DIR={tmp_path}
REGISTRY=reg.example:5000
DIGEST_DIR={d}
DRY_RUN={"true" if dry_run else "false"}
keep_images_on_box
"""
    env = {**os.environ, "CLAUDECODE": "", **box(tmp_path)}
    if ssh_fails:
        (tmp_path / "ssh").write_text("#!/bin/bash\nexit 255\n")
    p = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, timeout=60)
    return p.returncode, p.stdout + p.stderr


def kept(tmp_path):
    db = tmp_path / "containers"
    return [line.split() for line in db.read_text().splitlines()] if db.exists() else []


def test_each_pushed_image_is_held_by_a_labelled_stopped_container(tmp_path):
    rc, out = keep(tmp_path, {"digest-worker": digest("a"), "digest-site": digest("b")})
    assert rc == 0, out
    rows = {r[1]: r for r in kept(tmp_path)}
    assert rows["news-digest.keep=digest-worker"][2] == "reg.example:5000/digest-worker@" + digest("a")
    assert rows["news-digest.keep=digest-site"][2] == "reg.example:5000/digest-site@" + digest("b")


def test_only_the_newest_three_per_repository_are_kept(tmp_path):
    for c in "abcd":
        rc, out = keep(tmp_path, {"digest-worker": digest(c), "digest-site": digest("f")})
        assert rc == 0, out
    worker = [r[2] for r in kept(tmp_path) if r[1] == "news-digest.keep=digest-worker"]
    assert worker == ["reg.example:5000/digest-worker@" + digest(c) for c in "dcb"]
    # Another repository's count is its own: redeploying one digest four times keeps one container.
    assert [r[2] for r in kept(tmp_path) if r[1] == "news-digest.keep=digest-site"] == [
        "reg.example:5000/digest-site@" + digest("f")
    ]


def test_redeploying_an_old_digest_makes_it_the_newest(tmp_path):
    for c in "abca":
        keep(tmp_path, {"digest-worker": digest(c)})
    keep(tmp_path, {"digest-worker": digest("d")})
    worker = [r[2] for r in kept(tmp_path) if r[1] == "news-digest.keep=digest-worker"]
    # a was redeployed after c, so b is the one that ages out.
    assert worker == ["reg.example:5000/digest-worker@" + digest(c) for c in "dac"]


def test_a_failure_on_the_box_is_loud_and_not_fatal(tmp_path):
    rc, out = keep(tmp_path, {"digest-worker": digest("b")}, ssh_fails=True)
    assert rc == 0
    assert "NOT kept" in out


def test_nothing_pushed_leaves_the_box_alone(tmp_path):
    rc, out = keep(tmp_path, {})
    assert rc == 0, out
    assert kept(tmp_path) == []


def test_a_dry_run_names_the_step_and_touches_nothing(tmp_path):
    rc, out = keep(tmp_path, {"digest-worker": digest("a")}, dry_run=True)
    assert rc == 0, out
    assert kept(tmp_path) == []
    assert "Would run" in out


# ---- --rollback deploy/<ts> ----


# The user's own git config (hooks, signing) stays out of the scratch repository.
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def tagged_repo(tmp_path, message):
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
    }
    for cmd in (
        ["init", "-q"],
        ["commit", "-q", "--no-verify", "--allow-empty", "-m", "x"],
        ["tag", "-a", "deploy/2026-09-20-120000Z", "-m", message],
    ):
        subprocess.run(["git", *cmd], cwd=repo, check=True, env=env, capture_output=True)
    return repo


def rollback(tmp_path, message, tag="deploy/2026-09-20-120000Z", fn="load_rollback"):
    repo = tagged_repo(tmp_path, message)
    d = tmp_path / "digests"
    d.mkdir()
    script = f"""
source {DEPLOY}
trap - EXIT
set +e
cd {repo}
DIGEST_DIR={d}
SHA=ffffffffffffffffffffffffffffffffffffffff
ROLLBACK_TAG={tag}
{fn}
echo "rc=$? SHA=$SHA SKIP_BUILD=$SKIP_BUILD"
"""
    p = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env={**os.environ, "CLAUDECODE": ""}, timeout=60
    )
    files = {f.stem: f.read_text().strip() for f in d.iterdir()}
    return p.stdout + p.stderr, files


SHIPPED = "0123456789abcdef0123456789abcdef01234567"


@needs_git
def test_a_rollback_pins_the_tags_digests_and_its_commit(tmp_path):
    out, files = rollback(tmp_path, f"deploy {SHIPPED}\ndigest-site {digest('b')}\ndigest-worker {digest('a')}")
    assert f"rc=0 SHA={SHIPPED} SKIP_BUILD=true" in out, out
    assert files == {"digest-site": digest("b"), "digest-worker": digest("a")}


@needs_git
def test_a_rollback_ignores_the_retired_services_a_tag_pins(tmp_path):
    # Tags from before the cut-over pin digest-newsroom and digest-circulation too.
    out, files = rollback(
        tmp_path,
        f"deploy {SHIPPED}\ndigest-newsroom {digest('c')}\ndigest-circulation {digest('d')}\ndigest-worker {digest('a')}",
    )
    assert "rc=0" in out, out
    assert files == {"digest-worker": digest("a")}
    assert "digest-newsroom, which is no longer deployed" in out


@needs_git
@pytest.mark.parametrize(
    "message",
    [
        f"deploy {SHIPPED}\n(no images built this run -- deployed whatever :latest pointed at)",
        "not a deploy tag",
        f"deploy {SHIPPED}\ndigest-worker sha256:short",
    ],
)
def test_a_tag_with_no_digests_to_return_to_refuses(tmp_path, message):
    out, files = rollback(tmp_path, message)
    assert "rc=1" in out, out
    assert files == {}


@needs_git
def test_a_missing_tag_refuses(tmp_path):
    out, files = rollback(tmp_path, f"deploy {SHIPPED}\ndigest-worker {digest('a')}", tag="deploy/nope")
    assert "rc=1" in out, out
    assert "deploy/nope" in out
    assert files == {}


@needs_git
def test_a_rollback_says_which_services_stay_on_latest(tmp_path):
    out, _ = rollback(tmp_path, f"deploy {SHIPPED}\ndigest-worker {digest('a')}")
    assert "rc=0" in out
    assert "digest-python" in out and "digest-site" in out and ":latest" in out


@needs_git
def test_a_rollback_does_not_check_local_provenance(tmp_path):
    out, _ = rollback(
        tmp_path,
        f"deploy {SHIPPED}\ndigest-worker {digest('b')}",
        fn="load_rollback; SCRIPT_DIR=/nonexistent; verify_provenance",
    )
    assert "rc=0" in out, out
