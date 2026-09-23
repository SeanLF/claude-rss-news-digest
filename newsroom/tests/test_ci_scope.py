"""bin/ci --staged: which suites a commit's staged paths reach.

The property under test is that a staged path can never skip a suite that reads it. Each suite's
inputs are fixed by its container: ci-ts and ci-python COPY theirs in, ci-rust bind-mounts its
own, and the newsroom ci container sees the whole tree. The tests below read those lists from the
Dockerfiles and compose file themselves, so a new COPY or mount that bin/ci does not route fails
here rather than in a commit that skipped it.
"""

import re
import subprocess
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load():
    loader = SourceFileLoader("ci", str(ROOT / "bin" / "ci"))
    spec = spec_from_loader("ci", loader)
    mod = module_from_spec(spec)
    loader.exec_module(mod)
    return mod


ci = _load()
ALL = ci.ALL_SUITES
PY, RUST, TS, WORKER = ci.PYTHON, ci.RUST, ci.TS, ci.WORKER


@pytest.mark.parametrize(
    ("path", "suites"),
    [
        ("newsroom/src/db.py", {PY}),
        ("newsroom/tests/test_db.py", {PY}),
        ("newsroom/pyproject.toml", {PY}),
        ("circulation/src/main.rs", {RUST}),
        ("circulation/Cargo.toml", {RUST}),
        ("digest/src/workflow/digest.ts", {TS}),
        ("digest/package.json", {TS}),
        ("digest/python/worker.py", {WORKER, TS}),
        ("newsroom/src/fulltext.py", {PY, WORKER}),
        ("newsroom/src/gnews.py", {PY}),
        ("newsroom/src/config.py", {PY, WORKER}),
        ("circulation/src/markdown.rs", {RUST, PY}),
    ],
)
def test_a_path_one_suite_owns_runs_only_that_suite(path, suites):
    assert ci.suites_for([path]) == suites


@pytest.mark.parametrize(
    "path",
    [
        # Shared inputs: more than one container reads each.
        "migrations/20260101000000_x.sql",
        "newsroom/templates/digest-template.html",
        "newsroom/templates/digest.css",
        "newsroom/tests/fixtures/kitchensink_selections.json",
        "design/tokens.css",
        "newsroom/sources.json",
        # The CI machinery itself.
        "docker-compose.yml",
        "bin/ci",
        "lefthook.yml",
        "Makefile",
        "newsroom/Dockerfile.ci",
        # Container definitions, which the routing tests below parse: under a narrow prefix too.
        "digest/Dockerfile.ci",
        "digest/Dockerfile",
        "digest/python/Dockerfile",
        "circulation/Dockerfile.ci",
        "circulation/Dockerfile",
        "newsroom/Dockerfile",
        "docker-compose.override.yml",
        ".dockerignore",
        # Anything no rule names.
        ".claude/agents/write.md",
        "docs/operations.md",
        "README.md",
        # A sibling that only shares a prefix's spelling is not inside it.
        "digestion/x.ts",
        "newsroom-old/x.py",
        "circulation",
    ],
)
def test_a_shared_or_unknown_path_runs_everything(path):
    assert ci.suites_for([path]) == ALL


def test_suites_union_across_paths():
    assert ci.suites_for(["newsroom/src/db.py", "circulation/src/main.rs"]) == {PY, RUST}


def test_one_unknown_path_among_narrow_ones_runs_everything():
    assert ci.suites_for(["digest/src/a.ts", "docs/x.md"]) == ALL


def test_nothing_staged_reaches_no_suite():
    assert ci.suites_for([]) == frozenset()


def test_every_suite_has_a_command():
    assert set(ci.suite_commands(False, False, [])) == ALL


def _files_under(rel: str) -> list[str]:
    """Every file a COPY or mount source names, as repo-relative paths."""
    path = ROOT / rel
    if path.is_file():
        return [rel]
    skip = {"node_modules", "target", "dist", "__pycache__"}
    return [
        str(p.relative_to(ROOT))
        for p in path.rglob("*")
        if p.is_file() and not skip.intersection(p.relative_to(ROOT).parts)
    ]


def _copy_sources(dockerfile: str) -> list[str]:
    sources = []
    for line in (ROOT / dockerfile).read_text().splitlines():
        if not line.startswith("COPY ") or "--from=" in line:
            continue
        tokens = [t for t in line.split()[1:] if not t.startswith("--")]
        sources.extend(t.rstrip("/") or "." for t in tokens[:-1])
    assert sources, f"no COPY sources parsed from {dockerfile}"
    return sources


def _rust_mounts() -> list[str]:
    compose = (ROOT / "docker-compose.yml").read_text()
    block = compose.split("\n  ci-rust:\n", 1)[1].split("\n  digest-", 1)[0]
    mounts = [m.removeprefix("./") for m in re.findall(r"- (\./[^:]+):", block)]
    assert "circulation" in mounts, mounts
    return mounts


@pytest.mark.parametrize(
    ("suite", "sources"),
    [
        (TS, ["digest/Dockerfile.ci", *_copy_sources("digest/Dockerfile.ci")]),
        (WORKER, ["digest/python/Dockerfile", *_copy_sources("digest/python/Dockerfile")]),
        (RUST, _rust_mounts()),
    ],
)
def test_every_file_a_container_reads_routes_to_its_suite(suite, sources):
    unrouted = [f for src in sources for f in _files_under(src) if suite not in ci.suites_for([f])]
    assert not unrouted, f"{suite} reads these but a staged change to them would skip it: {unrouted[:10]}"


def test_the_copy_parser_sees_the_shared_inputs():
    """Negative control for the parser above: if it read nothing, the test would pass vacuously."""
    ts = _copy_sources("digest/Dockerfile.ci")
    assert {"design/tokens.css", "digest"} <= set(ts)
    assert "newsroom/src/fulltext.py" in _copy_sources("digest/python/Dockerfile")
    assert "newsroom/sources.json" in _rust_mounts()


_CROSS_REF = re.compile(
    r"""["'](circulation|digest)["']((?:\s*/\s*["'][^"']+["'])+)|["']((?:circulation|digest)/[\w./-]+)["']"""
)


def _newsroom_reads_into_other_suites() -> list[str]:
    """Paths under circulation/ or digest/ that newsroom code builds, as Path joins or literals."""
    found = []
    # The newsroom's own code only: a local .venv holds third-party files in other encodings.
    files = [py for d in ("src", "tests", "tools") for py in (ROOT / "newsroom" / d).rglob("*.py")]
    for py in files:
        if py.resolve() == Path(__file__).resolve():  # this file's own example paths
            continue
        for m in _CROSS_REF.finditer(py.read_text(encoding="utf-8")):
            if m.group(3):
                found.append(m.group(3))
            else:
                parts = re.findall(r"""["']([^"']+)["']""", m.group(2))
                found.append("/".join([m.group(1), *parts]))
    return found


def test_a_file_the_newsroom_tests_read_elsewhere_routes_to_python():
    refs = _newsroom_reads_into_other_suites()
    assert "circulation/src/markdown.rs" in refs  # the parser's negative control
    unrouted = [r for r in refs if PY not in ci.suites_for([r])]
    assert not unrouted, f"newsroom reads these, so a change to them must run the Python suite: {unrouted}"


def test_staged_paths_splits_renames_and_reads_nul_separated(monkeypatch):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout=b"old name.py\0digest/new.ts\0", stderr=b"")

    monkeypatch.setattr(ci.subprocess, "run", fake_run)
    assert ci.staged_paths() == ["old name.py", "digest/new.ts"]
    assert {"--cached", "--no-renames", "-z"} <= set(seen["cmd"])


def test_when_git_cannot_say_every_suite_runs(monkeypatch):
    monkeypatch.setattr(
        ci.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 128, stdout=b"", stderr=b"")
    )
    monkeypatch.setattr(ci.sys, "argv", ["bin/ci", "--staged"])
    started = []

    class FakePopen:
        def __init__(self, cmd, **kwargs):
            started.append(cmd)
            self.returncode = 0

        def communicate(self):
            return (b"", None)

    monkeypatch.setattr(ci.subprocess, "Popen", FakePopen)
    assert ci.run_in_docker() == 0
    assert len(started) == len(ALL)
