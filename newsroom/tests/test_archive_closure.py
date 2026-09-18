"""The run archive must be CLOSED under stage inputs.

``run_artifacts`` is what ``replay.py`` and every eval harness read to reproduce a run. A
stage input that is not archived and not derivable makes that reproduction quietly
unfaithful: the harness runs, prints a number, and the number is about inputs the stage
never had.

Probe 0 (docs/2026-09-17-probe-0-stage-purity.md) found three such leaks at once --
``sources.csv``, ``recent_rss_titles.csv`` and ``weekly_recap.txt`` -- all introduced by
prompt edits long after ``_TRACE_ARTIFACTS`` was written. Nothing connected the two lists,
so nothing could notice. This test is that connection: every file an agent prompt names is
either archived or on an explicit derived-in-process list, with a reason.
"""

from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import db

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENTS_DIR = REPO_ROOT / ".claude" / "agents"

# Files a stage reads that are NOT archived because Python REBUILDS them from artifacts that
# are. Each entry names the code that rebuilds it, and the name is checked below -- the first
# version of this list cited `orchestrate._build_repair_requests`, which does not exist.
DERIVED_FROM_ARCHIVED = {
    "repair_requests.json": ("repair", "build_repair_requests"),
    "preheader.txt": ("orchestrate", "read_preheader"),
}

# Model OUTPUTS on the repair path. These are NOT derivable -- re-deriving one means paying
# for the model call again, against inputs that are themselves unarchived -- so this is a
# KNOWN, OPEN gap in the closure guarantee, not an exemption from it. `repair_resolution.json`
# is the one merge.assemble_selections reads. Only `repaired_fields.json` is named by a prompt
# (repair.md), so the scan below sees one of the four and the rest are listed by hand -- which
# is the point: the scan's universe is "filenames spelled in a prompt", and the real input set
# is strictly larger. Closing this changes what production records, so it is a separate decision.
UNARCHIVED_REPAIR_OUTPUTS = {
    "repaired_fields.json": "repair stage output; the re-check consumes it in-process",
    "recheck_report.json": "re-check output; build_repair_resolution consumes it in-process",
    "recheck_draft.json": "re-check input, patched from repaired_fields.json -- transitively lost",
    "repair_resolution.json": "read by merge.assemble_selections; built from the two outputs above",
}

DERIVED_IN_PROCESS = {**{k: v[1] for k, v in DERIVED_FROM_ARCHIVED.items()}, **UNARCHIVED_REPAIR_OUTPUTS}

# articles_*.csv is archived by glob rather than by name (db.archive_run_artifacts).
_GLOBBED = re.compile(r"^articles_\d+\.csv$")

# Word-anchored so a hyphenated or capitalised name is reported as itself rather than as the
# substring that happened to match, and widened past json/csv/txt: the guard's whole purpose is
# to notice a NEW input, and the day a prompt is pointed at a .md or .jsonl the old grammar was
# silently off. Still a filename scan -- a path built from a variable, a whole directory, or
# prose ("the sources file") is invisible to it, which is why UNARCHIVED_REPAIR_OUTPUTS above
# is maintained by hand.
_FILENAME = re.compile(r"\b[A-Za-z0-9_-]+\.(?:json|jsonl|csv|tsv|txt|md|ya?ml)\b")


def _referenced_files() -> dict[str, set[str]]:
    """{filename: {agent names that mention it}} across every shipped agent prompt."""
    refs: dict[str, set[str]] = {}
    for agent in sorted(AGENTS_DIR.glob("*.md")):
        for name in _FILENAME.findall(agent.read_text(encoding="utf-8")):
            refs.setdefault(name, set()).add(agent.stem)
    return refs


def test_every_file_an_agent_prompt_names_is_archived_or_derived():
    unarchived = {
        name: sorted(agents)
        for name, agents in _referenced_files().items()
        if name not in db._TRACE_ARTIFACTS and name not in DERIVED_IN_PROCESS and not _GLOBBED.match(name)
    }
    assert not unarchived, (
        "these files are read by a stage but never archived, so a replay or eval feeds the "
        f"stage inputs it never had: {unarchived}. Add them to db._TRACE_ARTIFACTS, or to "
        "DERIVED_IN_PROCESS with the code that rebuilds them."
    )


def test_the_prompts_still_name_files_at_all():
    # Negative control: if a refactor stopped the prompts naming files, the test above
    # would pass vacuously and the closure guarantee would evaporate silently.
    refs = _referenced_files()
    assert len(refs) >= 8, f"only {len(refs)} filenames found across the agent prompts -- parser drifted?"
    assert "draft_selections.json" in refs


@pytest.mark.parametrize("name,reason", sorted(DERIVED_IN_PROCESS.items()))
def test_exempted_files_are_not_also_archived(name, reason):
    # A file that IS archived does not belong on an exemption list: the lists are the set of
    # documented exceptions, and a stale exception hides a real regression.
    assert name not in db._TRACE_ARTIFACTS, f"{name} is archived; drop it from the exemption list ({reason})"


@pytest.mark.parametrize("name,target", sorted(DERIVED_FROM_ARCHIVED.items()))
def test_the_code_that_rebuilds_a_derived_file_exists(name, target):
    # The reason strings are the only evidence that "derived" is true rather than hopeful, and
    # a reason naming a function that was renamed or never existed is worse than none.
    module_name, attr = target
    module = importlib.import_module(module_name)
    assert hasattr(module, attr), f"{name}: {module_name}.{attr} does not exist -- the exemption is unfounded"


# The guard in claude_cli catches an unrendered token at the model seam; these catch it in CI,
# where a prompt edit meets it instead of a production run at 10:25Z.


@pytest.mark.parametrize("agent_path", sorted(AGENTS_DIR.glob("*.md")), ids=lambda p: p.stem)
def test_every_shipped_agent_prompt_renders_with_no_token_left(agent_path):
    import claude_cli
    import orchestrate

    spec = orchestrate.parse_agent_spec(agent_path)
    claude_cli.assert_prompt_fully_rendered(orchestrate.render_body(spec.body))


def test_the_render_check_would_catch_a_typo():
    # Negative control: the check above is only worth running if a near-miss token fails it.
    # {{CURRENT-DATE}} is the realistic typo -- render_body does not substitute it, and the
    # first version of the guard's grammar (a token-name charset) let it through silently.
    import claude_cli
    import orchestrate

    with pytest.raises(ValueError):
        claude_cli.assert_prompt_fully_rendered(orchestrate.render_body("Today is {{CURRENT-DATE}}."))


def test_a_whitespaced_token_is_substituted_not_rejected():
    # The inverse failure: render_body used an exact-string replace, so `{{ CURRENT_DATE }}`
    # went unsubstituted and was then REJECTED at the seam -- a prompt typo becoming an outage.
    import claude_cli
    import orchestrate

    rendered = orchestrate.render_body("Today is {{ CURRENT_DATE }}.")
    claude_cli.assert_prompt_fully_rendered(rendered)
    assert "{{" not in rendered


# Prompts production sends that are NOT .md files. cluster.md is dead -- orchestrate routes
# label == "cluster" to cluster_extractjoin, which uses EXTRACT_SYSTEM -- so the glob above
# covers a prompt nothing runs and missed the one that does. Until they move into
# .claude/agents/, they are named here so the guard still reaches them.
INLINE_SYSTEM_PROMPTS = (
    ("cluster_extractjoin", "EXTRACT_SYSTEM"),
    ("cohesion", "JUDGE_SYSTEM"),
    ("threads", "LINK_SYSTEM"),
    ("thread_synthesis", "EVOLVE_SYSTEM"),
    ("thread_synthesis", "AUDIT_SYSTEM"),
    ("eval_why_judge", "WHY_JUDGE_PROMPT"),
    ("eval_recap_judge", "RECAP_JUDGE_PROMPT"),
    ("eval_recap_ab", "RECAP_SYSTEM_PROMPT"),
)


@pytest.mark.parametrize("module_name,attr", INLINE_SYSTEM_PROMPTS, ids=lambda v: str(v))
def test_inline_system_prompts_render_clean(module_name, attr):
    import claude_cli
    import orchestrate

    module = importlib.import_module(module_name)
    body = getattr(module, attr)
    claude_cli.assert_prompt_fully_rendered(orchestrate.render_body(body))


def test_no_inline_system_prompt_is_unlisted():
    """A constant passed as system_prompt= must be on the list above.

    The list is hand-maintained, so without this a new one added tomorrow is silently
    outside every prompt-level guarantee -- which is how cluster.md's replacement escaped
    in the first place.

    Bounded, and the bound matters: this sees `system_prompt=CONSTANT` only. A constant
    assigned to a local first (thread_synthesis passes `system_prompt=system`) is invisible
    here, which is why EVOLVE_SYSTEM and AUDIT_SYSTEM are on the list but not in `found`.
    The render check above covers them; this one cannot.
    """
    listed = {name for _module, name in INLINE_SYSTEM_PROMPTS}
    found: dict[str, str] = {}
    for path in sorted((REPO_ROOT / "newsroom" / "src").glob("*.py")):
        for const in re.findall(r"system_prompt=([A-Z][A-Z0-9_]+)", path.read_text(encoding="utf-8")):
            found[const] = path.name
    assert found, "no inline system_prompt constants found at all -- the scan drifted"
    unlisted = {c: f for c, f in found.items() if c not in listed}
    assert not unlisted, f"inline system prompts outside INLINE_SYSTEM_PROMPTS: {unlisted}"
