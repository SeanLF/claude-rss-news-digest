"""After the cut-over the post-deploy smoke checks the TypeScript site, which has no MCP surface.

The first temporal deploy (2026-09-25) failed its smoke on a 404 for /.well-known/mcp.json that the
site returns by design; the smoke must test what the site serves instead.
"""

import os
import subprocess
from pathlib import Path

DEPLOY = Path(__file__).parent.parent.parent / "bin" / "deploy"

FEED = "<feed><link href='https://x.test/issues/2026-09-24'/></feed>"


def smoke(mode, missing=""):
    # curl answers like the site: 404 for MCP and for any path named in `missing`, 200 otherwise.
    script = f"""
source {DEPLOY}
trap - EXIT
PIPELINE_MODE={mode}
DRY_RUN=false
curl() {{
  local u="${{@: -1}}"
  case "$u" in *mcp*) return 22;; esac
  [ -n "{missing}" ] && case "$u" in *"{missing}") return 22;; esac
  case "$u" in *feed.xml) echo "{FEED}";; esac
  return 0
}}
mcp_smoke https://x.test
"""
    p = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env={**os.environ, "CLAUDECODE": ""}, timeout=60
    )
    return p.returncode, p.stdout + p.stderr


def test_temporal_mode_smokes_the_site_not_mcp():
    rc, out = smoke("temporal")
    assert rc == 0, out
    assert "/issues/2026-09-24" in out


def test_a_missing_issue_markdown_fails_the_smoke():
    rc, out = smoke("temporal", missing="2026-09-24.md")
    assert rc == 1, out


def test_python_mode_still_smokes_mcp():
    rc, out = smoke("python")
    assert rc == 1, out
    assert "Smoking the MCP contract" in out
