"""The SBOM gate's named exceptions: packages installed from a URL rather than a registry.

still_active audits an SBOM by purl. The googlenewsdecoder fork installs from a GitHub archive but
calls itself ``pkg:pypi/googlenewsdecoder@0.2.0``, so the gate looked up PyPI's googlenewsdecoder,
a different package (SSujitX's rewrite), and reported that project's activity for code nobody
ships. ``bin/sbom-unregistered`` reads the SBOM's ``vcs`` references instead: a URL install that is
not a listed exception blocks the deploy, and a listed one is printed as unaudited.

Two canaries keep the exception from outliving its reason:
  - every URL pin must be an exception and every exception a URL pin, so porting to PyPI (which
    deletes the pin) fails here until the exception goes too;
  - the upstream check expires. On 2026-09-23 PyPI 0.2.1 (== SSujitX main d38ddbd) lacked what
    gnews.py needs, listed in ``UPSTREAM_LACKS``. Past ``RECHECK_BY`` this fails until someone
    re-checks upstream and either ports or moves the date.
"""

import importlib.machinery
import importlib.util
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).parent.parent.parent
SCRIPT = REPO / "bin" / "sbom-unregistered"
PYPROJECTS = [REPO / "newsroom" / "pyproject.toml", REPO / "digest" / "python" / "pyproject.toml"]

UPSTREAM_CHECKED_THROUGH = "0.2.1"
RECHECK_BY = date(2026, 12, 23)
UPSTREAM_LACKS = [
    "a structured HTTP status: a 429 on the article page reads 'Failed to fetch data attributes', "
    "on batchexecute only as exception text",
    "a retry of a failed connect (one httpx attempt)",
    "decode(url, transport=, timeout=), default_transport() and TransportError",
]


def _load():
    loader = importlib.machinery.SourceFileLoader("sbom_unregistered", str(SCRIPT))
    spec = importlib.util.spec_from_loader("sbom_unregistered", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _url_pins() -> set[str]:
    pins = set()
    for p in PYPROJECTS:
        pins |= set(re.findall(r'"[A-Za-z0-9_.-]+ @ (https?://[^"]+)"', p.read_text(encoding="utf-8")))
    return pins


def _sbom(tmp_path, *urls):
    components = [
        {
            "name": "googlenewsdecoder",
            "version": "0.2.0",
            "purl": "pkg:pypi/googlenewsdecoder@0.2.0",
            "externalReferences": [{"type": "vcs", "url": u}],
        }
        for u in urls
    ]
    components.append(
        {
            "name": "httpx",
            "version": "0.28.1",
            "purl": "pkg:pypi/httpx@0.28.1",
            "externalReferences": [{"type": "website", "url": "https://www.python-httpx.org"}],
        }
    )
    path = tmp_path / "sbom.json"
    path.write_text(json.dumps({"bomFormat": "CycloneDX", "components": components}))
    return path


def _run(path):
    return subprocess.run([sys.executable, str(SCRIPT), str(path)], capture_output=True, text=True)


def test_a_listed_url_install_passes_and_is_named_as_unaudited(tmp_path):
    (url,) = _load().EXCEPTIONS
    r = _run(_sbom(tmp_path, url))
    assert r.returncode == 0, r.stderr
    assert "googlenewsdecoder" in r.stderr and "NOT audited" in r.stderr


def test_an_unlisted_url_install_blocks(tmp_path):
    # Negative control: the same component from any other URL is not the exception.
    r = _run(_sbom(tmp_path, "https://github.com/someone/else/archive/abc.tar.gz"))
    assert r.returncode == 1
    assert "someone/else" in r.stderr


def test_a_registry_only_sbom_passes_quietly(tmp_path):
    r = _run(_sbom(tmp_path))
    assert r.returncode == 0 and r.stderr == ""


def test_an_unreadable_sbom_is_an_error_not_a_pass(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("")
    assert _run(bad).returncode == 2


def test_every_url_pin_is_an_exception_and_every_exception_a_pin():
    assert _url_pins() == set(_load().EXCEPTIONS)


def test_the_upstream_check_has_not_expired():
    assert date.today() <= RECHECK_BY, (
        f"googlenewsdecoder upstream was last checked through {UPSTREAM_CHECKED_THROUGH}; it lacked: "
        + "; ".join(UPSTREAM_LACKS)
        + ". Re-check PyPI and github.com/SSujitX/google-news-url-decoder: port gnews.py and drop the "
        "fork pin and its exception, or record the new check here and move RECHECK_BY."
    )
