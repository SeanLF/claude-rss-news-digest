"""bin/deploy builds the worker and the site from digest/Dockerfile's targets, and gates the site's SBOM.

digest/Dockerfile has `worker`, `site` and `dev` targets. The site image ships an esbuild bundle and
no node_modules, so scanning it finds no npm packages: its SBOM is the one the build wrote from the
lockfile and the bundle's metafile, at /app/digest/site.cdx.json, and that file is what still_active
audits. docker is stubbed here.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent.parent
DEPLOY = REPO / "bin" / "deploy"

SITE_SBOM = {
    "bomFormat": "CycloneDX",
    "specVersion": "1.6",
    "components": [{"type": "library", "name": "pg", "version": "8.16.0", "purl": "pkg:npm/pg@8.16.0"}],
}


def run(tmp_path, body, *, docker_stub="exit 0\n", env_extra=None):
    """Source bin/deploy with docker stubbed on PATH; return (rc, output, docker's calls)."""
    stubs = tmp_path / "stubs"
    stubs.mkdir(exist_ok=True)
    calls = tmp_path / "docker-calls"
    docker = stubs / "docker"
    docker.write_text(f'#!/bin/bash\nprintf "%s\\n" "$*" >> {calls}\n{docker_stub}')
    docker.chmod(0o755)
    script = f"""
source {DEPLOY}
trap - EXIT
set +e
SCRIPT_DIR={REPO / "bin"}
REGISTRY=reg.example:5000
SHA=0123456789abcdef
SBOM_DIR={tmp_path / "sbom"}
DIGEST_DIR={tmp_path / "digests"}
mkdir -p "$DIGEST_DIR"
{body}
"""
    env = {**os.environ, "PATH": f"{stubs}:{os.environ['PATH']}", "CLAUDECODE": "", **(env_extra or {})}
    p = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, timeout=60)
    made = calls.read_text().splitlines() if calls.exists() else []
    return p.returncode, p.stdout + p.stderr, made


def builds(tmp_path):
    """build_and_push's image calls, with each build replaced by a line naming its arguments."""
    rc, out, _ = run(
        tmp_path,
        f"""
DRY_RUN=true
build_and_push_image() {{ printf '%s|%s|%s|%s|%s\\n' "$@" >> {tmp_path / "builds"}; }}
build_and_push
""",
    )
    assert rc == 0, out
    lines = (tmp_path / "builds").read_text().splitlines()
    return {line.split("|")[0]: line.split("|")[1:] for line in lines}


def test_the_worker_and_the_site_build_from_their_own_targets(tmp_path):
    b = builds(tmp_path)
    worker, site = b["digest-worker"], b["digest-site"]
    assert worker[1] == site[1] == "digest/Dockerfile"
    assert "--target worker" in worker[2]
    assert "--target site" in site[2]
    # Same revision args as the worker, so one commit's images say the same thing.
    assert "--build-arg GIT_SHA=0123456" in worker[2]
    assert "--build-arg GIT_SHA=0123456" in site[2]
    assert worker[3] == "image"
    assert site[3] == "bundled"


def test_only_the_three_temporal_images_are_built(tmp_path):
    # The Python pipeline (digest-newsroom) and circulation (digest-circulation) retired at the cut-over.
    b = builds(tmp_path)
    assert set(b) == {"digest-worker", "digest-python", "digest-site"}
    assert b["digest-python"] == [".", "digest/python/Dockerfile", "", "image"]


def test_the_image_build_passes_the_revision_label(tmp_path):
    rc, out, calls = run(
        tmp_path,
        """
DRY_RUN=false
SKIP_SBOM_AUDIT=false
generate_sbom() { return 0; }
capture_digest() { return 0; }
run_quiet() { "$@"; }
build_and_push_image digest-site . digest/Dockerfile "--target site --build-arg GIT_SHA=0123456" bundled
""",
    )
    assert rc == 0, out
    build = next(c for c in calls if c.startswith("buildx build"))
    assert "--target site" in build
    assert "--build-arg OCI_REVISION=0123456789abcdef" in build
    assert "-t reg.example:5000/digest-site:0123456" in build


def bundled_sbom(tmp_path, *, cp_rc=0, sbom=SITE_SBOM, real_check=False):
    """Run generate_sbom's bundled path; audit_sbom (and, unless real_check, the jq check) stubbed."""
    fixture = tmp_path / "fixture.cdx.json"
    fixture.write_text(json.dumps(sbom) if isinstance(sbom, dict) else sbom)
    # `docker create` prints a container id; `docker cp CID:SRC DEST` copies the fixture to DEST.
    stub = f"""case $1 in
  create) echo cid123 ;;
  cp) [ {cp_rc} = 0 ] || exit {cp_rc}; cp {fixture} "$3" ;;
esac
exit 0
"""
    return run(
        tmp_path,
        """
DRY_RUN=false
SKIP_SBOM_AUDIT=false
audit_sbom() { echo "AUDITED $1 $2"; }
"""
        + ("" if real_check else 'check_sbom_and_audit() { echo "AUDITED $1 $2"; }\n')
        + """generate_sbom digest-site digest/Dockerfile bundled
""",
        docker_stub=stub,
    )


def test_the_site_sbom_is_copied_out_of_the_built_image_and_audited(tmp_path):
    rc, out, calls = bundled_sbom(tmp_path)
    assert rc == 0, out
    assert "create reg.example:5000/digest-site:0123456" in calls
    assert "cp cid123:/app/digest/site.cdx.json " + str(tmp_path / "sbom" / "digest-site-0123456.cdx.json") in calls
    assert "rm cid123" in calls
    assert f"AUDITED digest-site {tmp_path / 'sbom' / 'digest-site-0123456.cdx.json'}" in out


def test_the_site_sbom_never_needs_syft(tmp_path):
    # syft would scan the image, which is exactly what must not stand in for the bundle's SBOM.
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    (stubs / "syft").write_text("#!/bin/bash\necho SYFT-RAN\nexit 1\n")
    (stubs / "syft").chmod(0o755)
    rc, out, _ = bundled_sbom(tmp_path)
    assert rc == 0, out
    assert "SYFT-RAN" not in out


def test_a_site_image_without_its_sbom_blocks(tmp_path):
    rc, out, calls = bundled_sbom(tmp_path, cp_rc=1)
    assert rc == 1
    assert "site.cdx.json" in out
    assert "AUDITED" not in out
    assert "rm cid123" in calls


# The purl count reads the SBOM with jq, a deploy dependency the CI image does not carry.
needs_jq = pytest.mark.skipif(shutil.which("jq") is None, reason="jq not installed")


@needs_jq
def test_a_site_sbom_with_packages_reaches_the_audit(tmp_path):
    rc, out, _ = bundled_sbom(tmp_path, real_check=True)
    assert rc == 0, out
    assert "(1 packages)" in out
    assert "AUDITED digest-site" in out


@needs_jq
@pytest.mark.parametrize("sbom", ["", "{}", json.dumps({**SITE_SBOM, "components": []})])
def test_an_empty_site_sbom_blocks(tmp_path, sbom):
    rc, out, _ = bundled_sbom(tmp_path, sbom=sbom, real_check=True)
    assert rc == 1
    assert "AUDITED" not in out


def test_provenance_covers_the_site(tmp_path):
    rc, out, calls = run(
        tmp_path,
        """
DRY_RUN=false
echo sha256:aa > "$DIGEST_DIR/digest-site.digest"
echo sha256:bb > "$DIGEST_DIR/digest-worker.digest"
verify_provenance
""",
        docker_stub="echo 0123456789abcdef\n",
    )
    assert rc == 0, out
    assert "digest-site provenance verified" in out
    assert any("reg.example:5000/digest-site:0123456" in c for c in calls)
