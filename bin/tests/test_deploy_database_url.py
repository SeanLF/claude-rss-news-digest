"""bin/deploy refuses a deploy whose terraform gives the worker no DIGEST_DATABASE_URL.

The worker dies at startup without it (digest/src/worker.ts), so the deploy would apply, and the
day's run would never start. Terraform writes the worker's env (seanfloyd-infra tofu/news-digest-temporal.tf,
local.news_digest_worker_env_content); bin/deploy asks `bin/tf console` for it, stubbed here.
"""

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent.parent
DEPLOY = REPO / "bin" / "deploy"


def check(tmp_path, *, tf_out, tf_rc=0, tf_err=""):
    """Source bin/deploy with bin/tf stubbed; return (rc, output, what console was asked)."""
    infra = tmp_path / "infra"
    (infra / "bin").mkdir(parents=True)
    asked = tmp_path / "tf-asked"
    tf = infra / "bin" / "tf"
    tf.write_text(
        f'#!/bin/bash\necho "$*" >> {asked}\ncat >> {asked}\nprintf "%s" "$STUB_ERR" >&2\nprintf "%s" "$STUB_OUT"\nexit "$STUB_RC"\n'
    )
    tf.chmod(0o755)
    script = f"""
source {DEPLOY}
trap - EXIT
set +e
INFRA_DIR={infra}
DRY_RUN=false
check_database_url
"""
    env = {**os.environ, "STUB_OUT": tf_out, "STUB_ERR": tf_err, "STUB_RC": str(tf_rc), "CLAUDECODE": ""}
    p = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, timeout=60)
    return p.returncode, p.stdout + p.stderr, asked.read_text() if asked.exists() else ""


def test_a_url_with_a_password_passes(tmp_path):
    rc, out, asked = check(tmp_path, tf_out="true\n")
    assert rc == 0, out
    assert "console" in asked
    assert "DIGEST_DATABASE_URL" in asked
    assert "news_digest_worker_env_content" in asked
    # The password must be present, not just the line: an empty one renders as "digest:@".
    assert ":[^@]+@" in asked


def test_a_warning_from_bin_tf_does_not_refuse(tmp_path):
    # bin/tf warns on stderr and exits 0 when an optional secret is unreadable (OpenRouter's key).
    rc, out, _ = check(tmp_path, tf_out="true\n", tf_err="warn: OPENROUTER_API_KEY unreadable -- /ask stays dark\n")
    assert rc == 0, out


def test_the_refusal_names_terraforms_error_without_its_colour_codes(tmp_path):
    err = "\x1b[31m\u2577\x1b[0m\n\x1b[31m\u2502\x1b[0m \x1b[1m\x1b[31mError: \x1b[0m\x1b[1mReference to undeclared local value\x1b[0m\n"
    rc, out, _ = check(tmp_path, tf_out="", tf_rc=1, tf_err=err)
    assert rc == 1
    assert "Error: Reference to undeclared local value" in out


@pytest.mark.parametrize(
    ("tf_out", "tf_rc"),
    [
        ("false\n", 0),  # no line, or an empty password (bin/tf could not read 1Password)
        ("", 1),  # the local does not exist: infra that predates the product database
        ("(sensitive value)\n", 0),  # anything but a plain true
    ],
)
def test_anything_but_a_url_refuses(tmp_path, tf_out, tf_rc):
    rc, out, _ = check(tmp_path, tf_out=tf_out, tf_rc=tf_rc)
    assert rc == 1, out
    assert "DIGEST_DATABASE_URL" in out
