"""Orchestrator wiring for the per-story WRITE fan-out.

Measured on run 284: one WRITE call over every selected story and every article
fabricates unsupported specifics in ~40% of stories, and a replay through the
production SDK path cut coherence flags from 6,6,7,6 to 2,3,2,1 per 16 stories
when each story was written from its own cluster alone. This file pins the
wiring that ships that: every branch keeps run_stage's bounds, the assembled
order is SELECT's, and run_usage still gets exactly one `write` row.

No SDK call is ever made -- ``claude_cli.run_agent`` is mocked throughout.
"""

import asyncio
import json
import math
import re
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import orchestrate
import schema
from claude_cli import StageResult

REPO_ROOT = Path(__file__).parent.parent.parent
AGENTS_DIR = REPO_ROOT / ".claude" / "agents"

_BRANCH_DIR_RE = re.compile(r"([^\s`]+)/selected\.json")


def _result(cost=0.05, duration_ms=1000, usage=None):
    return StageResult(
        subtype="success",
        text="",
        usage=usage
        or {
            "input_tokens": 100,
            "output_tokens": 20,
            "cache_creation_input_tokens": 50,
            "cache_read_input_tokens": 800,
        },
        total_cost_usd=cost,
        duration_ms=duration_ms,
        is_error=False,
        api_error_status=None,
        files_read=(),
    )


def _story(headline):
    return {
        "headline": headline,
        "summary": "A summary.",
        "why_it_matters": "Because.",
        "sources": [{"article_id": "A1"}],
    }


def _seed(root, *, n_must=2, n_should=1):
    """A claude_input dir sitting where the orchestrator's write stage begins."""
    ids = [f"A{i}" for i in range(1, n_must + n_should + 2)]
    with open(root / "articles_1.csv", "w", newline="") as f:
        f.write("article_id,source_id,title,published,summary\n")
        for i in ids:
            f.write(f"{i},src,Title {i},2026-09-01,Body {i}\n")
    clusters = [{"story": f"c{n}", "article_ids": [ids[n]]} for n in range(len(ids))]
    (root / "clusters.json").write_text(json.dumps({"clusters": clusters}))
    (root / "selected.json").write_text(
        json.dumps(
            {
                "must_know": [{"cluster_index": n, "article_ids": [ids[n]]} for n in range(n_must)],
                "should_know": [
                    {"cluster_index": n_must + n, "article_ids": [ids[n_must + n]]} for n in range(n_should)
                ],
                "not_covered_blurb": "Left out the weather.",
            }
        )
    )
    (root / "recap.txt").write_text("A recap.")
    return root


class _Fake:
    """A run_agent stand-in that plays every agent the write phase drives."""

    def __init__(self, root, *, fail_branch=None, preheader="Three stories today."):
        self.root = root
        self.fail_branch = fail_branch
        self.preheader = preheader
        self.write_calls: list[Path] = []
        self.preheader_calls = 0
        self.live = 0
        self.peak = 0

    async def __call__(self, _prompt, *, system_prompt, **_k):
        if "news writer" in system_prompt:
            return await self._write(system_prompt)
        if "preheader" in system_prompt.lower():
            self.preheader_calls += 1
            (self.root / "preheader.txt").write_text(self.preheader)
            return _result(cost=0.01, duration_ms=500)
        if "fact-checking editor" in system_prompt:
            (self.root / "coherence_report.json").write_text(json.dumps({"results": []}))
        return _result()

    async def _write(self, system_prompt):
        self.live += 1
        self.peak = max(self.peak, self.live)
        try:
            await asyncio.sleep(0)
            branch = Path(_BRANCH_DIR_RE.search(system_prompt).group(1))
            self.write_calls.append(branch)
            if branch.name != self.fail_branch:
                (branch / "draft_selections.json").write_text(
                    json.dumps({"must_know": [_story(f"Headline {branch.name}")], "should_know": [], "preheader": "x"})
                )
            return _result(cost=0.1, duration_ms=2000)
        finally:
            self.live -= 1


def _run_write(root, fake, monkeypatch, *, rows=None):
    """Drive the phase and return the usage rows it emitted, in emission order.

    ``rows`` lets a caller keep the list across a raising call, which is the point of the
    callback: spend already billed must survive a later failure."""
    monkeypatch.setattr(orchestrate.claude_cli, "run_agent", fake)
    monkeypatch.setattr(orchestrate, "_AGENTS_DIR", AGENTS_DIR)
    out = rows if rows is not None else []
    asyncio.run(
        orchestrate.run_write_phase(
            claude_input_dir=root, model_override=None, cwd=None, run_deadline=None, on_usage=out.append
        )
    )
    return out


# --------------------------------------------------------------------------- #
# Fan out / fan in.
# --------------------------------------------------------------------------- #


class TestFanOut:
    def test_one_write_call_per_selected_story(self, tmp_path, monkeypatch):
        _seed(tmp_path, n_must=2, n_should=1)
        fake = _Fake(tmp_path)
        _run_write(tmp_path, fake, monkeypatch)
        assert sorted(p.name for p in fake.write_calls) == ["s00", "s01", "s02"]

    def test_each_branch_sees_only_its_own_story(self, tmp_path, monkeypatch):
        _seed(tmp_path, n_must=2, n_should=1)
        fake = _Fake(tmp_path)
        _run_write(tmp_path, fake, monkeypatch)
        for branch in fake.write_calls:
            selected = json.loads((branch / "selected.json").read_text())
            assert len(selected["must_know"]) + len(selected["should_know"]) == 1

    def test_concurrency_is_bounded(self, tmp_path, monkeypatch):
        _seed(tmp_path, n_must=8, n_should=4)
        fake = _Fake(tmp_path)
        _run_write(tmp_path, fake, monkeypatch)
        assert fake.peak <= orchestrate._WRITE_BRANCH_CONCURRENCY

    def test_assembled_order_is_selects_order_when_branches_finish_backwards(self, tmp_path, monkeypatch):
        _seed(tmp_path, n_must=2, n_should=1)

        class Backwards(_Fake):
            async def _write(self, system_prompt):
                # Later branches finish first: s02 returns immediately, s00 last.
                branch = Path(_BRANCH_DIR_RE.search(system_prompt).group(1))
                await asyncio.sleep(0.02 * (2 - int(branch.name[1:])))
                return await super()._write(system_prompt)

        fake = Backwards(tmp_path)
        _run_write(tmp_path, fake, monkeypatch)
        draft = json.loads((tmp_path / "draft_selections.json").read_text())
        assert [s["headline"] for s in draft["must_know"]] == ["Headline s00", "Headline s01"]
        assert [s["headline"] for s in draft["should_know"]] == ["Headline s02"]

    def test_a_failing_branch_fails_the_stage_rather_than_dropping_a_story(self, tmp_path, monkeypatch):
        _seed(tmp_path, n_must=2, n_should=1)
        fake = _Fake(tmp_path, fail_branch="s01")
        with pytest.raises(RuntimeError, match="s01"):
            _run_write(tmp_path, fake, monkeypatch)
        assert not (tmp_path / "draft_selections.json").exists()

    def test_each_branch_gets_a_branch_sized_spend_cap(self, tmp_path, monkeypatch):
        """run_stage's cap bounds ONE stage. Handed to every branch unchanged it would let
        the write phase bill N x $8 -- 25x a normal run -- before anything tripped."""
        seen: list[dict] = []
        _seed(tmp_path, n_must=8, n_should=4)

        class Recording(_Fake):
            async def __call__(self, prompt, *, system_prompt, **k):
                if "news writer" in system_prompt:
                    seen.append(k)
                return await super().__call__(prompt, system_prompt=system_prompt, **k)

        _run_write(tmp_path, Recording(tmp_path), monkeypatch)
        assert len(seen) == 12
        assert all(k["max_budget_usd"] == orchestrate._WRITE_BRANCH_BUDGET_USD for k in seen)
        assert orchestrate._WRITE_BRANCH_BUDGET_USD < orchestrate._STAGE_BUDGET_USD

    def test_the_phase_stops_once_the_branches_have_billed_the_stage_cap(self, tmp_path, monkeypatch):
        """A per-branch cap alone does not bound the phase: 20 branches at $1.50 is $30.
        The running total over what the branches actually billed is the phase's bound."""
        _seed(tmp_path, n_must=6, n_should=6)

        class Expensive(_Fake):
            async def _write(self, system_prompt):
                branch = Path(_BRANCH_DIR_RE.search(system_prompt).group(1))
                self.write_calls.append(branch)
                (branch / "draft_selections.json").write_text(
                    json.dumps({"must_know": [_story(f"H {branch.name}")], "should_know": [], "preheader": "x"})
                )
                return _result(cost=1.4)

        rows: list[dict] = []
        fake = Expensive(tmp_path)
        with pytest.raises(RuntimeError, match="phase cap"):
            _run_write(tmp_path, fake, monkeypatch, rows=rows)
        assert len(fake.write_calls) < 12
        # Spend already billed still reaches run_usage.
        assert rows[0]["api_cost_usd"] >= orchestrate._STAGE_BUDGET_USD - 1.4

    def test_a_branch_does_not_start_after_the_run_deadline(self, tmp_path, monkeypatch):
        """with_retry_async only consults the deadline once fn() raises, so a queued wave
        would otherwise open a fresh 15-minute attempt well past the run's budget."""
        _seed(tmp_path, n_must=2, n_should=0)
        monkeypatch.setattr(orchestrate.claude_cli, "run_agent", _Fake(tmp_path))
        monkeypatch.setattr(orchestrate, "_AGENTS_DIR", AGENTS_DIR)
        with pytest.raises(RuntimeError, match="run deadline"):
            asyncio.run(
                orchestrate.run_write_phase(
                    claude_input_dir=tmp_path,
                    model_override=None,
                    cwd=None,
                    run_deadline=time.monotonic() - 1,
                    on_usage=lambda _row: None,
                )
            )

    def test_the_worst_case_wall_clock_fits_the_run_budget(self):
        """select.md's hard max is 6 must_know + 14 should_know. At the whole-stage 45-min
        attempt timeout that is 7.5h of waves, past _RUN_RETRY_BUDGET_S and past the
        systemd start-timeout whose kill leaves digest_runs stuck at 'running'."""
        max_stories = 6 + 14
        waves = math.ceil(max_stories / orchestrate._WRITE_BRANCH_CONCURRENCY)
        attempts = 2  # run_stage retries once from a clean slate
        worst = waves * attempts * orchestrate._WRITE_BRANCH_ATTEMPT_TIMEOUT_S
        worst += attempts * orchestrate._PREHEADER_ATTEMPT_TIMEOUT_S
        assert worst < orchestrate._RUN_RETRY_BUDGET_S, f"write phase worst case {worst}s"

    def test_branches_run_under_run_stages_attempt_bounds(self, tmp_path, monkeypatch):
        """Every branch must inherit run_stage's machinery, not just the semaphore -- a
        fan-out that bypassed it would be N unbounded calls."""
        _seen: list[dict] = []
        _seed(tmp_path, n_must=1, n_should=0)

        async def fake(_prompt, *, system_prompt, **k):
            _seen.append(k)
            branch = Path(_BRANCH_DIR_RE.search(system_prompt).group(1)) if "news writer" in system_prompt else None
            if branch:
                (branch / "draft_selections.json").write_text(
                    json.dumps({"must_know": [_story("H")], "should_know": [], "preheader": "x"})
                )
            else:
                (tmp_path / "preheader.txt").write_text("P.")
            return _result()

        _run_write(tmp_path, fake, monkeypatch)
        assert _seen[0]["max_budget_usd"] == orchestrate._WRITE_BRANCH_BUDGET_USD
        assert all(k["max_budget_usd"] is not None for k in _seen)

    def test_branch_prompts_carry_no_preheader_request(self, tmp_path, monkeypatch):
        """A branch sees one story; asking it for "the 2-3 biggest stories" is an
        unsatisfiable instruction on the same call that must not fabricate."""
        _seed(tmp_path, n_must=1, n_should=1)
        seen: list[str] = []

        class Recording(_Fake):
            async def __call__(self, prompt, *, system_prompt, **k):
                if "news writer" in system_prompt:
                    seen.append(system_prompt)
                return await super().__call__(prompt, system_prompt=system_prompt, **k)

        _run_write(tmp_path, Recording(tmp_path), monkeypatch)
        assert len(seen) == 2
        assert all("preheader" not in body.lower() for body in seen)

    def test_branch_retries_once_before_failing(self, tmp_path, monkeypatch):
        _seed(tmp_path, n_must=1, n_should=0)
        attempts = {"n": 0}

        async def fake(_prompt, *, system_prompt, **_k):
            if "news writer" in system_prompt:
                attempts["n"] += 1
                branch = Path(_BRANCH_DIR_RE.search(system_prompt).group(1))
                if attempts["n"] > 1:
                    (branch / "draft_selections.json").write_text(
                        json.dumps({"must_know": [_story("H")], "should_know": [], "preheader": "x"})
                    )
            elif "preheader" in system_prompt.lower():
                (tmp_path / "preheader.txt").write_text("P.")
            return _result()

        _run_write(tmp_path, fake, monkeypatch)
        assert attempts["n"] == 2


# --------------------------------------------------------------------------- #
# Preheader stage.
# --------------------------------------------------------------------------- #


class TestPreheader:
    def test_preheader_agent_fills_the_assembled_draft(self, tmp_path, monkeypatch):
        _seed(tmp_path, n_must=1, n_should=1)
        fake = _Fake(tmp_path, preheader="Iran strikes widen; quake toll rises.")
        _run_write(tmp_path, fake, monkeypatch)
        draft = json.loads((tmp_path / "draft_selections.json").read_text())
        assert draft["preheader"] == "Iran strikes widen; quake toll rises."
        assert fake.preheader_calls == 1

    def test_preheader_runs_after_the_branches_so_it_can_read_the_headlines(self, tmp_path, monkeypatch):
        _seed(tmp_path, n_must=1, n_should=1)
        order: list[str] = []

        class Ordered(_Fake):
            async def __call__(self, prompt, *, system_prompt, **k):
                if "news writer" in system_prompt:
                    order.append("write")
                elif "preheader" in system_prompt.lower():
                    order.append("preheader")
                    draft = json.loads((self.root / "draft_selections.json").read_text())
                    order.append(f"saw {len(draft['must_know']) + len(draft['should_know'])} stories")
                return await super().__call__(prompt, system_prompt=system_prompt, **k)

        _run_write(tmp_path, Ordered(tmp_path), monkeypatch)
        assert order == ["write", "write", "preheader", "saw 2 stories"]

    def test_preheader_gets_its_own_usage_row(self, tmp_path, monkeypatch):
        _seed(tmp_path, n_must=1, n_should=0)
        rows = _run_write(tmp_path, _Fake(tmp_path), monkeypatch)
        assert [r["subagent"] for r in rows] == ["write", "preheader"]
        assert rows[1]["model"] == "claude-haiku-4-5"

    def test_a_preheader_that_never_lands_leaves_the_field_for_merge_to_fill(self, tmp_path, monkeypatch):
        """Doctrine since run 229 (merge._enforce_capped_string_fields): NOTHING about the
        preheader may abort a delivered digest. A stage that fails after its retry leaves
        the field blank and the run continues -- merge substitutes the top headline."""
        _seed(tmp_path, n_must=2, n_should=1)
        rows = _run_write(tmp_path, _Fake(tmp_path, preheader="   "), monkeypatch)

        draft = json.loads((tmp_path / "draft_selections.json").read_text())
        assert draft["preheader"] == ""
        assert len(draft["must_know"]) == 2
        # The branches were still paid for, and still reach run_usage.
        assert [r["subagent"] for r in rows] == ["write"]

    @pytest.mark.parametrize(
        "boom",
        [TimeoutError("attempt timed out"), OSError("disk gone"), KeyError("shape")],
        ids=["timeout", "oserror", "keyerror"],
    )
    def test_no_preheader_failure_can_abort_the_run(self, tmp_path, monkeypatch, boom):
        """ "Nothing about the preheader may abort a delivered digest" has to hold for the
        exception types too. run_stage bounds an attempt with asyncio.wait_for, whose expiry
        is a bare TimeoutError that neither with_retry_async nor run_stage's own handler
        catches -- so a handler listing (RuntimeError, ValueError) would let a hung preheader
        call take the whole curation run down."""
        _seed(tmp_path, n_must=2, n_should=0)

        class Boom(_Fake):
            async def __call__(self, prompt, *, system_prompt, **k):
                if "news writer" not in system_prompt and "preheader" in system_prompt.lower():
                    raise boom
                return await super().__call__(prompt, system_prompt=system_prompt, **k)

        rows = _run_write(tmp_path, Boom(tmp_path), monkeypatch)

        draft = json.loads((tmp_path / "draft_selections.json").read_text())
        assert draft["preheader"] == ""
        assert len(draft["must_know"]) == 2
        assert [r["subagent"] for r in rows] == ["write"]

    def test_a_cancelled_run_is_not_swallowed_by_the_preheader_handler(self, tmp_path, monkeypatch):
        """The broad catch must not turn a real cancellation into a shipped digest.
        CancelledError is a BaseException, so it has to propagate."""
        _seed(tmp_path, n_must=1, n_should=0)

        class Cancel(_Fake):
            async def __call__(self, prompt, *, system_prompt, **k):
                if "news writer" not in system_prompt and "preheader" in system_prompt.lower():
                    raise asyncio.CancelledError
                return await super().__call__(prompt, system_prompt=system_prompt, **k)

        with pytest.raises(asyncio.CancelledError):
            _run_write(tmp_path, Cancel(tmp_path), monkeypatch)

    def test_a_preamble_before_the_sentence_is_stripped_end_to_end(self, tmp_path, monkeypatch):
        """Plain text goes straight into a reader-facing field, so "Here is the preheader:"
        would ship verbatim. Clean it rather than fail the digest."""
        _seed(tmp_path, n_must=1, n_should=0)
        _run_write(tmp_path, _Fake(tmp_path, preheader="Here is the preheader:\n\nIran strikes widen."), monkeypatch)
        assert json.loads((tmp_path / "draft_selections.json").read_text())["preheader"] == "Iran strikes widen."

    def test_an_over_cap_preheader_is_truncated_not_raised(self, tmp_path, monkeypatch):
        """run 229 died on a 152-char preheader against a hard 150 cap. The cap is enforced
        by degrading, here and in merge, never by failing."""
        _seed(tmp_path, n_must=1, n_should=0)
        _run_write(tmp_path, _Fake(tmp_path, preheader="word " * 100), monkeypatch)
        shipped = json.loads((tmp_path / "draft_selections.json").read_text())["preheader"]
        assert 0 < len(shipped) <= schema.PREHEADER_MAX_CHARS

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Iran strikes widen.", "Iran strikes widen."),
            ('"Iran strikes widen."', "Iran strikes widen."),
            ("Preheader: Iran strikes widen.", "Iran strikes widen."),
            ("Here is the preheader:\n\nIran strikes widen.", "Iran strikes widen."),
            ("```\nIran strikes widen.\n```", "Iran strikes widen."),
            ("- Iran strikes widen.", "Iran strikes widen."),
            ('1. "Iran strikes widen."', "Iran strikes widen."),
            ("\u201cIran strikes widen.\u201d", "Iran strikes widen."),
            ("**Preheader:** Iran strikes widen.", "Iran strikes widen."),
            # A line that is a label and nothing else carries no content, so dropping it
            # cannot lose any; one that merely ENDS in a colon is content and is kept.
            ("Three things today: Iran, Gaza, Kyiv.", "Three things today: Iran, Gaza, Kyiv."),
            ("", ""),
            ("```\n```", ""),
        ],
    )
    def test_the_preheader_is_cleaned_of_model_wrapping(self, raw, expected):
        assert orchestrate.clean_preheader(raw) == expected

    @pytest.mark.parametrize(
        "headline",
        [
            "Colombia quake: 3-year-old rescued 100 hours on as death toll reaches 294",
            "WHO: ultra-processed food companies filed 235 lawsuits against regulators",
            "Indonesia earthquake: 53 dead, more than 12,000 await aid in Sulawesi",
            "Gaza ceasefire holds: Iran talks stall; Manila counts quake dead",
            "Iran strikes widen: Tehran says it will retaliate within days",
        ],
    )
    def test_a_lead_clause_ending_in_a_colon_keeps_its_subject(self, headline):
        """The register this line is composed from is full of load-bearing colons. Any
        label rule general enough to match "Preheader:" also matches these and deletes the
        subject of the sentence -- silently, since every downstream check only asks whether
        the field is non-empty. Measured: a length-bounded rule ate 10 of the 1,225
        headlines in the 79 archived digests. These five are real ones."""
        assert orchestrate.clean_preheader(headline) == headline

    def test_a_cleaned_preheader_that_empties_out_falls_back_to_merge(self, tmp_path, monkeypatch):
        _seed(tmp_path, n_must=1, n_should=0)
        _run_write(tmp_path, _Fake(tmp_path, preheader="```\n```"), monkeypatch)
        assert json.loads((tmp_path / "draft_selections.json").read_text())["preheader"] == ""

    def test_the_preheader_stage_has_its_own_bounds(self, tmp_path, monkeypatch):
        """A Haiku call over a ~2 KB draft does not need the whole-stage 45-minute, $8
        allowance, and the fan-out's worst case has to include it."""
        _seed(tmp_path, n_must=1, n_should=0)
        seen: list[dict] = []

        class Recording(_Fake):
            async def __call__(self, prompt, *, system_prompt, **k):
                if "preheader" in system_prompt.lower() and "news writer" not in system_prompt:
                    seen.append(k)
                return await super().__call__(prompt, system_prompt=system_prompt, **k)

        _run_write(tmp_path, Recording(tmp_path), monkeypatch)
        assert seen[0]["max_budget_usd"] == orchestrate._PREHEADER_BUDGET_USD
        assert orchestrate._PREHEADER_BUDGET_USD < orchestrate._STAGE_BUDGET_USD
        assert orchestrate._PREHEADER_ATTEMPT_TIMEOUT_S < orchestrate._STAGE_ATTEMPT_TIMEOUT_S


# --------------------------------------------------------------------------- #
# Usage aggregation and the per-branch artifact.
# --------------------------------------------------------------------------- #


class TestUsageAndArtifacts:
    def test_branches_collapse_into_one_write_row(self, tmp_path, monkeypatch):
        _seed(tmp_path, n_must=2, n_should=1)
        rows = _run_write(tmp_path, _Fake(tmp_path), monkeypatch)
        write_rows = [r for r in rows if r["subagent"] == "write"]
        assert len(write_rows) == 1
        row = write_rows[0]
        assert row["input_tokens"] == 3 * 100
        assert row["output_tokens"] == 3 * 20
        assert row["cache_write_tokens"] == 3 * 50
        assert row["cache_read_tokens"] == 3 * 800
        assert row["api_cost_usd"] == pytest.approx(3 * 0.1)
        assert row["model"] == orchestrate.parse_agent_spec(AGENTS_DIR / "write.md").model

    def test_write_row_duration_is_stage_wall_clock_not_the_sum_of_concurrent_branches(self, tmp_path, monkeypatch):
        _seed(tmp_path, n_must=2, n_should=1)
        rows = _run_write(tmp_path, _Fake(tmp_path), monkeypatch)
        row = next(r for r in rows if r["subagent"] == "write")
        assert row["duration_ms"] < 3 * 2000

    def test_per_branch_breakdown_is_archived(self, tmp_path, monkeypatch):
        _seed(tmp_path, n_must=2, n_should=1)
        archived: dict[str, str] = {}
        monkeypatch.setattr(
            orchestrate.db, "record_run_artifact", lambda name, content: archived.update({name: content}) or True
        )
        _run_write(tmp_path, _Fake(tmp_path), monkeypatch)

        assert "write_branches.json" in archived
        branches = json.loads(archived["write_branches.json"])["branches"]
        assert [b["branch"] for b in branches] == ["s00", "s01", "s02"]
        assert [b["tier"] for b in branches] == ["must_know", "must_know", "should_know"]
        assert all(b["api_cost_usd"] == 0.1 for b in branches)
        assert branches[0]["cluster_index"] == 0
        # The ids, not a count: the branch CSVs are never archived (archive_run_artifacts
        # globs articles_*.csv non-recursively), so this is the only record of what each
        # branch was allowed to read.
        assert branches[0]["context_article_ids"] == ["A1"]

    def test_the_artifact_says_whether_the_write_row_covers_the_whole_phase(self, tmp_path, monkeypatch):
        """A partial write row and a whole one are indistinguishable from the aggregate
        alone -- so are a phase where a branch was dropped and one where all ran."""
        _seed(tmp_path, n_must=2, n_should=1)
        archived: dict[str, str] = {}
        monkeypatch.setattr(
            orchestrate.db, "record_run_artifact", lambda name, content: archived.update({name: content}) or True
        )
        with pytest.raises(RuntimeError, match="s02"):
            _run_write(tmp_path, _Fake(tmp_path, fail_branch="s02"), monkeypatch)

        payload = json.loads(archived["write_branches.json"])
        assert payload["branches_total"] == 3
        assert payload["branches_completed"] == 2

    def test_a_dropped_story_is_recorded_in_the_artifact(self, tmp_path, monkeypatch):
        _seed(tmp_path, n_must=2, n_should=0)
        selected = json.loads((tmp_path / "selected.json").read_text())
        selected["must_know"].append({"cluster_index": 99, "article_ids": []})
        (tmp_path / "selected.json").write_text(json.dumps(selected))
        archived: dict[str, str] = {}
        monkeypatch.setattr(
            orchestrate.db, "record_run_artifact", lambda name, content: archived.update({name: content}) or True
        )

        _run_write(tmp_path, _Fake(tmp_path), monkeypatch)

        payload = json.loads(archived["write_branches.json"])
        assert payload["branches_total"] == 2
        assert [d["branch"] for d in payload["dropped"]] == ["s02"]
        assert payload["dropped"][0]["reason"]

    def test_spend_already_billed_survives_a_later_branch_failing(self, tmp_path, monkeypatch):
        """The regression _record's own comment says it exists to prevent, reintroduced
        one level down: 15 paid branches must not be discarded because the 16th failed."""
        _seed(tmp_path, n_must=4, n_should=4)
        rows: list[dict] = []
        with pytest.raises(RuntimeError, match="s07"):
            _run_write(tmp_path, _Fake(tmp_path, fail_branch="s07"), monkeypatch, rows=rows)

        assert [r["subagent"] for r in rows] == ["write"]
        assert rows[0]["api_cost_usd"] == pytest.approx(7 * 0.1)

    def test_nothing_is_recorded_when_no_branch_completed(self, tmp_path, monkeypatch):
        _seed(tmp_path, n_must=1, n_should=0)
        rows: list[dict] = []
        with pytest.raises(RuntimeError, match="s00"):
            _run_write(tmp_path, _Fake(tmp_path, fail_branch="s00"), monkeypatch, rows=rows)
        assert rows == []

    def test_the_artifact_is_archived_even_when_every_branch_resumed(self, tmp_path, monkeypatch):
        """A fully-resumed phase bills nothing, so there is no write row -- but it still
        dropped whatever it dropped, and STORIES_DROPPED_AT_WRITE reads that list. Skipping
        the archive would make a re-run look like a clean one."""
        _seed(tmp_path, n_must=2, n_should=0)
        selected = json.loads((tmp_path / "selected.json").read_text())
        selected["must_know"].append({"cluster_index": 99, "article_ids": []})
        (tmp_path / "selected.json").write_text(json.dumps(selected))
        _run_write(tmp_path, _Fake(tmp_path), monkeypatch)

        archived: dict[str, str] = {}
        monkeypatch.setattr(
            orchestrate.db, "record_run_artifact", lambda name, content: archived.update({name: content}) or True
        )
        second = _Fake(tmp_path)
        rows = _run_write(tmp_path, second, monkeypatch)

        assert second.write_calls == []
        assert "write" not in [r["subagent"] for r in rows]
        payload = json.loads(archived["write_branches.json"])
        assert payload["branches_total"] == 2
        assert payload["branches_completed"] == 0
        assert [d["branch"] for d in payload["dropped"]] == ["s02"]

    def test_the_breakdown_is_left_on_disk_for_the_archive_sweep(self, tmp_path, monkeypatch):
        """record_run_artifact is a no-op until db.start_run(), and on --resume start_run
        happens AFTER curation -- so the row alone would leave STORIES_DROPPED_AT_WRITE dark
        on the recovery path. The file rides archive_run_artifacts' sweep instead. Asserted
        on disk rather than through a stubbed recorder, which cannot see that gate."""
        _seed(tmp_path, n_must=2, n_should=0)
        _run_write(tmp_path, _Fake(tmp_path), monkeypatch)

        payload = json.loads((tmp_path / "write_branches.json").read_text())
        assert payload["branches_total"] == 2
        assert payload["branches_completed"] == 2

    def test_the_file_is_in_the_archive_sweeps_list(self):
        assert "write_branches.json" in orchestrate.db._TRACE_ARTIFACTS


# --------------------------------------------------------------------------- #
# Repetition guard.
# --------------------------------------------------------------------------- #


class TestRepetitionGuard:
    def test_repeated_headlines_warn_without_failing_the_stage(self, tmp_path, monkeypatch, caplog):
        _seed(tmp_path, n_must=2, n_should=0)

        class Same(_Fake):
            async def _write(self, system_prompt):
                branch = Path(_BRANCH_DIR_RE.search(system_prompt).group(1))
                self.write_calls.append(branch)
                (branch / "draft_selections.json").write_text(
                    json.dumps(
                        {
                            "must_know": [_story("US widens Iran sanctions on trading partners")],
                            "should_know": [],
                            "preheader": "x",
                        }
                    )
                )
                return _result()

        with caplog.at_level("WARNING"):
            rows = _run_write(tmp_path, Same(tmp_path), monkeypatch)

        assert any(r["subagent"] == "write" for r in rows)
        assert "US widens Iran sanctions on trading partners" in caplog.text


# --------------------------------------------------------------------------- #
# Resume.
# --------------------------------------------------------------------------- #


class TestResume:
    def test_a_branch_with_a_valid_draft_is_not_re_paid_for(self, tmp_path, monkeypatch):
        """The preheader stage failing used to mean re-running all 16 branches on the next
        attempt. A branch that already holds a valid draft for THIS story is skipped, and
        contributes no usage row -- the same contract stage-level resume has."""
        _seed(tmp_path, n_must=2, n_should=1)
        first = _Fake(tmp_path)
        _run_write(tmp_path, first, monkeypatch)
        assert len(first.write_calls) == 3

        second = _Fake(tmp_path)
        rows = _run_write(tmp_path, second, monkeypatch)
        assert second.write_calls == []
        assert [r["subagent"] for r in rows] == ["preheader"]
        draft = json.loads((tmp_path / "draft_selections.json").read_text())
        assert [s["headline"] for s in draft["must_know"]] == ["Headline s00", "Headline s01"]

    def test_only_the_failed_branch_reruns(self, tmp_path, monkeypatch):
        _seed(tmp_path, n_must=2, n_should=1)
        with pytest.raises(RuntimeError, match="s02"):
            _run_write(tmp_path, _Fake(tmp_path, fail_branch="s02"), monkeypatch)

        second = _Fake(tmp_path)
        _run_write(tmp_path, second, monkeypatch)
        assert [p.name for p in second.write_calls] == ["s02"]

    def test_a_valid_draft_on_disk_skips_the_branches_entirely(self, tmp_path, monkeypatch):
        _seed(tmp_path, n_must=2, n_should=1)
        (tmp_path / "draft_selections.json").write_text(
            json.dumps({"must_know": [_story("Already written")], "should_know": [], "preheader": "p"})
        )
        (tmp_path / "recap.txt").write_text("A recap.")
        fake = _Fake(tmp_path)
        monkeypatch.setattr(orchestrate.claude_cli, "run_agent", fake)
        monkeypatch.setattr(orchestrate, "_AGENTS_DIR", AGENTS_DIR)
        monkeypatch.setattr(orchestrate.cluster_extractjoin, "run_extractjoin_stage", None)
        (tmp_path / "clusters.json").write_text(json.dumps({"clusters": [{"story": "c", "article_ids": ["A1"]}]}))
        (tmp_path / "coherence_report.json").write_text(
            json.dumps({"results": [{"headline": "Already written", "pass": True}]})
        )
        (tmp_path / "selected.json").write_text(
            json.dumps({"must_know": [{"cluster_index": 0, "article_ids": ["A1"]}], "should_know": []})
        )

        rows = asyncio.run(orchestrate.orchestrate_selections(claude_input_dir=tmp_path, resume=True))

        assert fake.write_calls == []
        assert fake.preheader_calls == 0
        assert [r["subagent"] for r in rows] == []


# --------------------------------------------------------------------------- #
# Cohesion gate hook (Phase C, Task 3)
# --------------------------------------------------------------------------- #


class TestCohesionGateHook:
    """Between SELECT and the fan-out, behind COHESION_ENABLED. The gate's row is emitted
    before any write row; with the flag off the artifact says so and nothing is billed."""

    def _two_article_cluster(self, root):
        _seed(root, n_must=1, n_should=0)
        (root / "clusters.json").write_text(json.dumps({"clusters": [{"story": "c0", "article_ids": ["A1", "A2"]}]}))

    def test_flag_on_runs_the_gate_first_and_the_branch_reflects_it(self, tmp_path, monkeypatch):
        self._two_article_cluster(tmp_path)
        monkeypatch.setattr(orchestrate.config, "COHESION_ENABLED", True)
        monkeypatch.setattr(orchestrate.config, "COHESION_MODEL", "claude-sonnet-4-6")
        seen = {}

        async def fake_gate(claude_input_dir, *, model, cwd):
            seen["model"] = model
            (claude_input_dir / "cluster_cohesion.json").write_text(
                json.dumps(
                    {
                        "outcome": "completed",
                        "verdicts": [
                            {"cluster_index": 0, "applied": True, "dominant": ["A1"], "strays": ["A2"], "reason": None}
                        ],
                    }
                )
            )
            return {"subagent": "cohesion", "model": model, "api_cost_usd": 0.02}

        monkeypatch.setattr(orchestrate.cohesion, "run_cohesion_stage", fake_gate)
        rows = _run_write(tmp_path, _Fake(tmp_path), monkeypatch)
        assert rows[0]["subagent"] == "cohesion" and seen["model"] == "claude-sonnet-4-6"
        assert [r["subagent"] for r in rows[1:]].count("cohesion") == 0
        csv_ids = [
            ln.split(",")[0]
            for ln in (tmp_path / "write_branches" / "s00" / "articles_1.csv").read_text().splitlines()[1:]
        ]
        assert csv_ids == ["A1"]

    def test_flag_off_writes_a_skipped_artifact_and_bills_nothing(self, tmp_path, monkeypatch):
        self._two_article_cluster(tmp_path)
        monkeypatch.setattr(orchestrate.config, "COHESION_ENABLED", False)

        async def never(*a, **k):
            raise AssertionError("gate ran with the flag off")

        monkeypatch.setattr(orchestrate.cohesion, "run_cohesion_stage", never)
        rows = _run_write(tmp_path, _Fake(tmp_path), monkeypatch)
        assert all(r["subagent"] != "cohesion" for r in rows)
        doc = json.loads((tmp_path / "cluster_cohesion.json").read_text())
        assert doc["outcome"] == "skipped"
        csv_ids = [
            ln.split(",")[0]
            for ln in (tmp_path / "write_branches" / "s00" / "articles_1.csv").read_text().splitlines()[1:]
        ]
        assert csv_ids == ["A1", "A2"]
