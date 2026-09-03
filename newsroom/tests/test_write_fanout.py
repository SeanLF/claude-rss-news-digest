"""Unit tests for write_fanout: the per-story branch inputs and the fan-in.

Everything here is deterministic Python -- no SDK, no model call. The
orchestrator's wiring (run_stage bounds, usage aggregation, the preheader
stage, resume) is covered in test_write_per_story.py.
"""

import csv
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import eval_coherence
import write_fanout

REPO_ROOT = Path(__file__).parent.parent.parent
WRITE_SPEC = REPO_ROOT / ".claude" / "agents" / "write.md"

_HEADER = ["article_id", "source_id", "title", "published", "summary"]


def _seed(tmp_path, *, selected=None, clusters=None, extras=("recap.txt",)):
    """A minimal claude_input dir: 3 articles, 2 clusters, 2 selected stories."""
    rows = [
        ["A1", "s1", "Iran strike", "2026-09-01", "Body one"],
        ["A2", "s2", "Iran talks", "2026-09-01", "Body two"],
        ["A3", "s3", "Quake toll", "2026-09-01", "Body three"],
        ["A4", "s4", "Quake aid", "2026-09-01", "Body four"],
    ]
    with open(tmp_path / "articles_1.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(_HEADER)
        w.writerows(rows[:2])
    with open(tmp_path / "articles_2.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(_HEADER)
        w.writerows(rows[2:])
    (tmp_path / "clusters.json").write_text(
        json.dumps(
            clusters
            or {
                "clusters": [
                    {"story": "iran", "article_ids": ["A1", "A2"]},
                    {"story": "quake", "article_ids": ["A3", "A4"]},
                ]
            }
        )
    )
    (tmp_path / "selected.json").write_text(
        json.dumps(
            selected
            or {
                "must_know": [{"cluster_index": 0, "article_ids": ["A1"]}],
                "should_know": [{"cluster_index": 1, "article_ids": ["A3", "A4"]}],
                "not_covered_blurb": "We left out the weather.",
            }
        )
    )
    (tmp_path / "article_fulltext.json").write_text(
        json.dumps({"A1": {"text": "full one"}, "A3": {"text": "full three"}})
    )
    for name in extras:
        (tmp_path / name).write_text(f"contents of {name}")
    return tmp_path


def _branch_rows(branch):
    with open(branch.dir / "articles_1.csv", newline="") as f:
        return list(csv.reader(f))


# --------------------------------------------------------------------------- #
# Fan out: branch input dirs
# --------------------------------------------------------------------------- #


class TestBuildBranches:
    def test_branches_follow_selected_order_must_know_first(self, tmp_path):
        branches = write_fanout.build_branches(_seed(tmp_path)).branches
        assert [b.name for b in branches] == ["s00", "s01"]
        assert [b.tier for b in branches] == ["must_know", "should_know"]

    def test_branch_selected_json_holds_only_its_own_story(self, tmp_path):
        branches = write_fanout.build_branches(_seed(tmp_path)).branches
        first = json.loads((branches[0].dir / "selected.json").read_text())
        assert first["must_know"] == [{"cluster_index": 0, "article_ids": ["A1"]}]
        assert first["should_know"] == []
        assert first["not_covered_blurb"] == "We left out the weather."
        second = json.loads((branches[1].dir / "selected.json").read_text())
        assert second["must_know"] == []
        assert second["should_know"] == [{"cluster_index": 1, "article_ids": ["A3", "A4"]}]

    def test_branch_csv_is_the_cluster_unioned_with_the_story_ids(self, tmp_path):
        # Story s00 cites only A1; its cluster carries A1+A2, so the branch must see both.
        branches = write_fanout.build_branches(_seed(tmp_path)).branches
        rows = _branch_rows(branches[0])
        assert rows[0] == _HEADER
        assert [r[0] for r in rows[1:]] == ["A1", "A2"]
        assert [r[0] for r in _branch_rows(branches[1])[1:]] == ["A3", "A4"]

    def test_branch_csv_spans_every_source_articles_file(self, tmp_path):
        # A3/A4 live in articles_2.csv; a branch that only read articles_1.csv sees nothing.
        sel = {
            "must_know": [{"cluster_index": 1, "article_ids": ["A3"]}],
            "should_know": [],
        }
        branches = write_fanout.build_branches(_seed(tmp_path, selected=sel)).branches
        assert [r[0] for r in _branch_rows(branches[0])[1:]] == ["A3", "A4"]

    def test_story_ids_outside_the_cluster_are_still_included(self, tmp_path):
        sel = {"must_know": [{"cluster_index": 0, "article_ids": ["A1", "A3"]}], "should_know": []}
        branches = write_fanout.build_branches(_seed(tmp_path, selected=sel)).branches
        assert [r[0] for r in _branch_rows(branches[0])[1:]] == ["A1", "A2", "A3"]

    def test_out_of_range_cluster_index_resolves_by_citations_loudly(self, tmp_path, caplog):
        """Before the drift fix an out-of-range index left the branch with its citations alone.
        The citations name the cluster (A1 lives in cluster 0), so the branch gets it, with a
        warning; the citations-in-no-cluster case is TestClusterIndexDrift's fallback test."""
        sel = {"must_know": [{"cluster_index": 99, "article_ids": ["A1"]}], "should_know": []}
        with caplog.at_level("WARNING"):
            branches = write_fanout.build_branches(_seed(tmp_path, selected=sel)).branches
        assert [r[0] for r in _branch_rows(branches[0])[1:]] == ["A1", "A2"]
        assert branches[0].cluster_index == 0 and branches[0].selected_cluster_index == 99
        assert "cluster_index" in caplog.text

    def test_branch_fulltext_is_filtered_to_the_same_ids(self, tmp_path):
        branches = write_fanout.build_branches(_seed(tmp_path)).branches
        assert json.loads((branches[0].dir / "article_fulltext.json").read_text()) == {"A1": {"text": "full one"}}
        assert json.loads((branches[1].dir / "article_fulltext.json").read_text()) == {"A3": {"text": "full three"}}

    def test_absent_fulltext_produces_no_branch_fulltext(self, tmp_path):
        seeded = _seed(tmp_path)
        (seeded / "article_fulltext.json").unlink()
        branches = write_fanout.build_branches(seeded).branches
        assert not (branches[0].dir / "article_fulltext.json").exists()

    def test_shared_context_files_are_copied_unchanged(self, tmp_path):
        seeded = _seed(tmp_path, extras=("recap.txt", "weekly_recap.txt", "recent_digest_headlines.txt"))
        branches = write_fanout.build_branches(seeded).branches
        for name in ("recap.txt", "weekly_recap.txt", "recent_digest_headlines.txt"):
            assert (branches[0].dir / name).read_text() == (seeded / name).read_text()

    def test_absent_optional_context_files_are_not_invented(self, tmp_path):
        branches = write_fanout.build_branches(_seed(tmp_path, extras=("recap.txt",))).branches
        assert not (branches[0].dir / "weekly_recap.txt").exists()
        assert not (branches[0].dir / "recent_digest_headlines.txt").exists()

    def test_an_invalid_leftover_draft_is_rebuilt(self, tmp_path):
        seeded = _seed(tmp_path)
        stale = write_fanout.build_branches(seeded).branches[0].dir / "draft_selections.json"
        stale.write_text('{"must_know": [], "should_know": [], "preheader": ""}')
        write_fanout.build_branches(seeded)
        assert not stale.exists()

    def test_a_valid_draft_for_the_same_story_is_kept(self, tmp_path):
        """Branch-level resume: a re-run after a mid-phase failure must not re-pay for the
        branches that already finished."""
        seeded = _seed(tmp_path)
        draft = write_fanout.build_branches(seeded).branches[0].dir / "draft_selections.json"
        draft.write_text(json.dumps(_draft()))
        write_fanout.build_branches(seeded)
        assert json.loads(draft.read_text())["must_know"][0]["headline"] == "Iran strike widens"

    def test_a_draft_is_rebuilt_when_the_cluster_was_repartitioned_under_it(self, tmp_path):
        """selected.json can be byte-identical while the cluster it points at gained or
        lost articles -- cluster_index and article_ids do not move. Reusing the draft then
        would leave context_article_ids in the run artifact describing evidence the draft
        was never written from."""
        seeded = _seed(tmp_path)
        draft = write_fanout.build_branches(seeded).branches[0].dir / "draft_selections.json"
        draft.write_text(json.dumps(_draft()))
        (seeded / "clusters.json").write_text(
            json.dumps(
                {
                    "clusters": [
                        {"story": "iran", "article_ids": ["A1", "A2", "A4"]},
                        {"story": "quake", "article_ids": ["A3"]},
                    ]
                }
            )
        )
        fanout = write_fanout.build_branches(seeded)
        assert not draft.exists()
        assert fanout.branches[0].context_article_ids == ("A1", "A2", "A4")

    def test_a_valid_draft_written_for_a_different_story_is_rebuilt(self, tmp_path):
        """Identity is the branch's own selected.json, so a run whose SELECT output moved
        cannot reuse a draft written about something else."""
        seeded = _seed(tmp_path)
        draft = write_fanout.build_branches(seeded).branches[0].dir / "draft_selections.json"
        draft.write_text(json.dumps(_draft()))
        moved = {
            "must_know": [{"cluster_index": 1, "article_ids": ["A3"]}],
            "should_know": [],
            "not_covered_blurb": "We left out the weather.",
        }
        (seeded / "selected.json").write_text(json.dumps(moved))
        write_fanout.build_branches(seeded)
        assert not draft.exists()

    def test_branch_dirs_from_a_longer_previous_run_are_removed(self, tmp_path):
        seeded = _seed(tmp_path)
        root = write_fanout.build_branches(seeded).branches[0].dir.parent
        (root / "s09").mkdir()
        write_fanout.build_branches(seeded)
        assert not (root / "s09").exists()

    def test_no_selected_stories_raises(self, tmp_path):
        sel = {"must_know": [], "should_know": []}
        with pytest.raises(ValueError, match="no selected stories"):
            write_fanout.build_branches(_seed(tmp_path, selected=sel))

    def test_a_malformed_story_entry_raises_rather_than_shipping_one_short(self, tmp_path):
        """SELECT drift, not a thin story: nothing sensible can be built from it, and
        skipping it would deliver a digest one story short with nothing saying so."""
        sel = {"must_know": [{"cluster_index": 0, "article_ids": ["A1"]}, "not a story"], "should_know": []}
        with pytest.raises(ValueError, match=r"must_know\[1\]"):
            write_fanout.build_branches(_seed(tmp_path, selected=sel))

    def test_a_story_resolving_to_zero_articles_is_dropped_loudly(self, tmp_path, caplog):
        """A WRITE call whose CSV is header-only has no evidence at all -- the highest-
        probability fabrication input there is. Drop that story, at ERROR, rather than
        pay for it or kill a run that has 15 good stories."""
        sel = {
            "must_know": [{"cluster_index": 0, "article_ids": ["A1"]}, {"cluster_index": 99, "article_ids": []}],
            "should_know": [],
        }
        with caplog.at_level("ERROR"):
            fanout = write_fanout.build_branches(_seed(tmp_path, selected=sel))
        assert [b.name for b in fanout.branches] == ["s00"]
        assert [(d.name, d.tier) for d in fanout.dropped] == [("s01", "must_know")]
        assert "dropping the story" in caplog.text

    def test_article_ids_with_no_csv_row_do_not_count_as_evidence(self, tmp_path):
        sel = {
            "must_know": [{"cluster_index": 0, "article_ids": ["A1"]}, {"cluster_index": 99, "article_ids": ["A999"]}],
            "should_know": [],
        }
        fanout = write_fanout.build_branches(_seed(tmp_path, selected=sel))
        assert [d.name for d in fanout.dropped] == ["s01"]

    def test_branch_names_keep_selects_positions_when_one_drops(self, tmp_path):
        """s01 dropping must not renumber s02 to s01 -- the artifact and the logs are read
        against SELECT's list."""
        sel = {
            "must_know": [
                {"cluster_index": 0, "article_ids": ["A1"]},
                {"cluster_index": 99, "article_ids": []},
                {"cluster_index": 1, "article_ids": ["A3"]},
            ],
            "should_know": [],
        }
        fanout = write_fanout.build_branches(_seed(tmp_path, selected=sel))
        assert [b.name for b in fanout.branches] == ["s00", "s02"]

    def test_every_story_dropping_still_raises(self, tmp_path):
        sel = {"must_know": [{"cluster_index": 99, "article_ids": []}], "should_know": []}
        with pytest.raises(ValueError, match="no selected stories"):
            write_fanout.build_branches(_seed(tmp_path, selected=sel))


# --------------------------------------------------------------------------- #
# Branch output validation
# --------------------------------------------------------------------------- #


def _draft(story=None, tier="must_know"):
    story = story or {
        "headline": "Iran strike widens",
        "summary": "A summary.",
        "why_it_matters": "Because.",
        "sources": [{"article_id": "A1"}],
    }
    out = {"must_know": [], "should_know": [], "preheader": "p"}
    out[tier] = [story]
    return out


def _seed_branch(branch_dir, tier="must_know"):
    """The Python-written half of a branch dir: SELECT's tier for the one story."""
    selected = {"must_know": [], "should_know": [], "not_covered_blurb": ""}
    selected[tier] = [{"cluster_index": 0, "article_ids": ["A1"]}]
    (branch_dir / "selected.json").write_text(json.dumps(selected))


class TestValidateBranchDraft:
    def _write(self, tmp_path, payload):
        _seed_branch(
            tmp_path, "should_know" if payload.get("should_know") and not payload.get("must_know") else "must_know"
        )
        (tmp_path / "draft_selections.json").write_text(json.dumps(payload))
        return tmp_path

    def test_accepts_one_story(self, tmp_path):
        write_fanout.validate_branch_draft(self._write(tmp_path, _draft()))

    def test_rejects_zero_stories(self, tmp_path):
        with pytest.raises(ValueError, match="exactly 1 story"):
            write_fanout.validate_branch_draft(
                self._write(tmp_path, {"must_know": [], "should_know": [], "preheader": "p"})
            )

    def test_rejects_two_stories(self, tmp_path):
        payload = _draft()
        payload["should_know"] = [dict(payload["must_know"][0])]
        with pytest.raises(ValueError, match="exactly 1 story"):
            write_fanout.validate_branch_draft(self._write(tmp_path, payload))

    def test_rejects_missing_field(self, tmp_path):
        story = {"headline": "H", "summary": "S", "sources": [{"article_id": "A1"}]}
        with pytest.raises(ValueError, match="why_it_matters"):
            write_fanout.validate_branch_draft(self._write(tmp_path, _draft(story)))

    def test_rejects_empty_sources(self, tmp_path):
        story = {"headline": "H", "summary": "S", "why_it_matters": "W", "sources": []}
        with pytest.raises(ValueError, match="sources"):
            write_fanout.validate_branch_draft(self._write(tmp_path, _draft(story)))

    def test_rejects_missing_file(self, tmp_path):
        _seed_branch(tmp_path)
        with pytest.raises(ValueError, match=r"draft_selections\.json"):
            write_fanout.validate_branch_draft(tmp_path)


# --------------------------------------------------------------------------- #
# Fan in: assembly order
# --------------------------------------------------------------------------- #


class TestAssembleDraft:
    def test_order_is_selects_order_not_completion_order(self, tmp_path):
        """The contract nothing pinned before: branches run concurrently and finish in
        any order, so assembly must key off SELECT's order, never the filesystem or the
        order tasks returned in."""
        sel = {
            "must_know": [
                {"cluster_index": 0, "article_ids": ["A1"]},
                {"cluster_index": 1, "article_ids": ["A3"]},
            ],
            "should_know": [{"cluster_index": 1, "article_ids": ["A4"]}],
        }
        branches = write_fanout.build_branches(_seed(tmp_path, selected=sel)).branches
        # Write the branch drafts back-to-front, the worst case for an order bug.
        for branch, headline in zip(reversed(branches), ["third", "second", "first"], strict=True):
            (branch.dir / "draft_selections.json").write_text(
                json.dumps(_draft({**_draft()["must_know"][0], "headline": headline}, tier=branch.tier))
            )
        draft = write_fanout.assemble_draft(branches)
        assert [s["headline"] for s in draft["must_know"]] == ["first", "second"]
        assert [s["headline"] for s in draft["should_know"]] == ["third"]

    def test_sources_pass_through_exactly_as_the_branch_wrote_them(self, tmp_path):
        branches = write_fanout.build_branches(_seed(tmp_path)).branches
        sources = [{"article_id": "A2"}, {"article_id": "A1"}]
        for branch in branches:
            (branch.dir / "draft_selections.json").write_text(
                json.dumps(_draft({**_draft()["must_know"][0], "sources": sources}, tier=branch.tier))
            )
        draft = write_fanout.assemble_draft(branches)
        assert draft["must_know"][0]["sources"] == sources

    def test_preheader_starts_empty(self, tmp_path):
        branches = write_fanout.build_branches(_seed(tmp_path)).branches
        for branch in branches:
            (branch.dir / "draft_selections.json").write_text(json.dumps(_draft(tier=branch.tier)))
        assert write_fanout.assemble_draft(branches)["preheader"] == ""

    def test_a_branch_story_filed_under_the_other_tier_still_lands_on_selects_tier(self, tmp_path):
        # The branch's own selected.json only carries its tier, but a model that files
        # the story under the wrong key must not silently move it between digest tiers.
        branches = write_fanout.build_branches(_seed(tmp_path)).branches
        for branch in branches:
            wrong = "should_know" if branch.tier == "must_know" else "must_know"
            (branch.dir / "draft_selections.json").write_text(json.dumps(_draft(tier=wrong)))
        draft = write_fanout.assemble_draft(branches)
        assert len(draft["must_know"]) == 1
        assert len(draft["should_know"]) == 1


# --------------------------------------------------------------------------- #
# Cross-story repetition guard
# --------------------------------------------------------------------------- #


class TestRepetitionWarnings:
    def test_flags_a_near_duplicate_pair(self):
        draft = {
            "must_know": [{"headline": "US widens Iran sanctions to target trading partners"}],
            "should_know": [{"headline": "US widens Iran sanctions targeting more trading partners"}],
            "preheader": "",
        }
        warnings = write_fanout.repetition_warnings(draft)
        assert len(warnings) == 1
        a, b, score = warnings[0]
        assert "Iran sanctions" in a and "Iran sanctions" in b
        assert score >= write_fanout.HEADLINE_REPETITION_THRESHOLD

    def test_silent_on_the_closest_non_duplicate_pair_shipped_under_batch_write(self):
        # Highest-scoring genuinely-distinct pair across 79 archived digests (run 245,
        # j=0.263). The threshold sits above it, so the guard does not cry wolf on the
        # topic overlap that batch WRITE itself shipped.
        draft = {
            "must_know": [
                {"headline": "Zelensky says Russia seeks 30,000 more North Korean troops as strikes continue"},
                {"headline": "Romania shoots down third Russian drone in three days, summons envoy"},
            ],
            "should_know": [],
            "preheader": "",
        }
        assert write_fanout.repetition_warnings(draft) == []

    def test_compares_across_tiers_and_within_them(self):
        draft = {
            "must_know": [
                {"headline": "Quake death toll rises to 51 as aid arrives"},
                {"headline": "Quake death toll rises to 51 while aid arrives"},
            ],
            "should_know": [],
            "preheader": "",
        }
        assert len(write_fanout.repetition_warnings(draft)) == 1

    def test_no_pairs_when_fewer_than_two_stories(self):
        assert write_fanout.repetition_warnings({"must_know": [{"headline": "Only one"}], "should_know": []}) == []

    def test_paraphrase_of_one_event_is_not_detected(self):
        """The bound, pinned rather than implied away. Token-Jaccard sees shared
        vocabulary; two branches describing one event in AP-wire style share almost none,
        so this guard cannot catch that case and must not be read as if it does. Raising
        the sensitivity is not the fix either -- every pair here scores below the highest
        genuinely-distinct pair batch WRITE shipped (0.263)."""
        pairs = [
            (
                "Israel strikes Beirut suburb, killing Hezbollah commander",
                "Hezbollah says senior figure died in Lebanese capital raid",
            ),
            ("US imposes new tariffs on Chinese electric vehicles", "Beijing vows response to Washington's EV duties"),
            (
                "Quake death toll in Afghanistan rises to 812",
                "Aid convoys reach Herat as casualties mount after tremor",
            ),
            (
                "Zelensky asks allies for longer-range missiles",
                "Kyiv presses Western capitals over strike-range limits",
            ),
            (
                "Fed holds rates steady, signals one cut this year",
                "Powell says policy stays restrictive as inflation cools",
            ),
            (
                "Trump signs order raising tariffs on Indian goods to 50%",
                "India calls 50% US tariff unjustified, weighs retaliation",
            ),
        ]
        for a, b in pairs:
            draft = {"must_know": [{"headline": a}], "should_know": [{"headline": b}], "preheader": ""}
            assert write_fanout.repetition_warnings(draft) == [], (a, b)


# --------------------------------------------------------------------------- #
# The path-redirect contract
# --------------------------------------------------------------------------- #


def test_marker_matches_the_eval_redirect_contract():
    """Two modules redirect the same hardcoded prod path. If they drift, one of them
    silently runs a stage against the wrong directory."""
    assert write_fanout.PROD_INPUT_MARKER == eval_coherence._PROD_INPUT_MARKER


def test_write_spec_still_carries_the_marker_to_redirect():
    assert write_fanout.PROD_INPUT_MARKER in WRITE_SPEC.read_text(encoding="utf-8")


def test_redirect_rewrites_every_occurrence():
    body = "read /app/data/claude_input/selected.json then /app/data/claude_input/articles_1.csv"
    out = write_fanout.branch_body(body, Path("/tmp/b/s00"))
    assert write_fanout.PROD_INPUT_MARKER not in out
    assert out.count("/tmp/b/s00/") == 2


def test_redirect_raises_when_the_prompt_lost_the_marker():
    with pytest.raises(ValueError, match="drifted"):
        write_fanout.branch_body("no paths here", Path("/tmp/b/s00"))


class TestBranchBody:
    """write.md IS the per-story prompt now -- branch_body only points it at the branch's
    own input directory. Nothing about the prompt's content may be rewritten in flight."""

    def _body(self):
        return WRITE_SPEC.read_text(encoding="utf-8").split("---", 2)[2].strip()

    def test_paths_are_redirected(self):
        out = write_fanout.branch_body(self._body(), Path("/tmp/b/s00"))
        assert write_fanout.PROD_INPUT_MARKER not in out
        assert "/tmp/b/s00/selected.json" in out

    def test_nothing_but_the_paths_changes(self):
        """The prompt a branch runs differs from write.md by the input path and nothing
        else. Reinstating an in-flight rewrite of the prompt fails here."""
        body = self._body()
        out = write_fanout.branch_body(body, Path("/tmp/b/s00"))
        assert out == body.replace(write_fanout.PROD_INPUT_MARKER, "/tmp/b/s00/")

    def test_output_schema_stays_valid_json_shaped(self):
        out = write_fanout.branch_body(self._body(), Path("/tmp/b/s00"))
        schema_text = out[out.index("**Output schema:**") :]
        assert json.loads(schema_text[schema_text.index("{") : schema_text.rindex("}") + 1].replace("...", "x"))


# --------------------------------------------------------------------------- #
# why_it_matters is a must_know field
# --------------------------------------------------------------------------- #


class TestShouldKnowCarriesNoWhyItMatters:
    """Briefs render headline + summary only (render.py), so a should_know branch writes no
    why_it_matters, and one it writes anyway is dropped here -- before COHERENCE can flag it
    and repair can spend on it (run 285: 3 of 4 flags, 2 of 3 repairs, on the invisible field)."""

    def test_a_should_know_branch_draft_needs_no_why_it_matters(self, tmp_path):
        _seed_branch(tmp_path, "should_know")
        story = {"headline": "h", "summary": "s", "sources": [{"article_id": "A1"}]}
        (tmp_path / "draft_selections.json").write_text(json.dumps(_draft(story, tier="should_know")))
        write_fanout.validate_branch_draft(tmp_path)

    def test_a_must_know_branch_draft_still_needs_one(self, tmp_path):
        _seed_branch(tmp_path, "must_know")
        story = {"headline": "h", "summary": "s", "sources": [{"article_id": "A1"}]}
        (tmp_path / "draft_selections.json").write_text(json.dumps(_draft(story, tier="must_know")))
        with pytest.raises(ValueError, match="why_it_matters"):
            write_fanout.validate_branch_draft(tmp_path)

    def test_the_tier_that_decides_the_required_fields_is_selects_not_the_key_the_model_chose(self, tmp_path):
        """assemble_draft already refuses to let a mis-filed story move tier; validation
        must read the same authority. Keyed on the model's choice, a must_know story filed
        under should_know without its why_it_matters passes here and kills the run at
        assembly, after COHERENCE and repair have been paid for."""
        _seed_branch(tmp_path, "must_know")
        story = {"headline": "h", "summary": "s", "sources": [{"article_id": "A1"}]}
        (tmp_path / "draft_selections.json").write_text(json.dumps(_draft(story, tier="should_know")))
        with pytest.raises(ValueError, match="why_it_matters"):
            write_fanout.validate_branch_draft(tmp_path)

    def test_a_should_know_story_misfiled_under_must_know_is_still_complete(self, tmp_path):
        _seed_branch(tmp_path, "should_know")
        story = {"headline": "h", "summary": "s", "sources": [{"article_id": "A1"}]}
        (tmp_path / "draft_selections.json").write_text(json.dumps(_draft(story, tier="must_know")))
        write_fanout.validate_branch_draft(tmp_path)

    def test_a_branch_without_its_selected_json_is_not_a_branch(self, tmp_path):
        (tmp_path / "draft_selections.json").write_text(json.dumps(_draft()))
        with pytest.raises(ValueError, match=r"selected\.json"):
            write_fanout.validate_branch_draft(tmp_path)

    def test_fan_in_drops_a_why_it_matters_a_should_know_branch_wrote_anyway(self, tmp_path):
        branches = write_fanout.build_branches(_seed(tmp_path)).branches
        for branch in branches:
            (branch.dir / "draft_selections.json").write_text(json.dumps(_draft(tier=branch.tier)))
        draft = write_fanout.assemble_draft(branches)
        assert "why_it_matters" not in draft["should_know"][0]
        assert draft["must_know"][0]["why_it_matters"] == "Because."


# --------------------------------------------------------------------------- #
# Single-turn delivery of a branch (Task 2 of docs/2026-09-03-stage-invocation-rewrite-plan.md)
# --------------------------------------------------------------------------- #


class TestSingleTurnBranch:
    """The same branch, delivered inline: every file the prompt lists, in the prompt's order,
    and the prompt with only its I/O section swapped. Derived, so a cost measurement cannot
    quietly become a quality measurement."""

    def test_corpus_inlines_exactly_the_files_the_prompt_lists(self, tmp_path):
        branches = write_fanout.build_branches(_seed(tmp_path, extras=("recap.txt", "weekly_recap.txt"))).branches
        corpus = write_fanout.branch_corpus(branches[0].dir)
        for name in ("selected.json", "articles_1.csv", "weekly_recap.txt"):
            assert f"## {name}" in corpus
        assert "## recap.txt" not in corpus  # copied into the dir for parity, never read by WRITE
        assert (
            corpus.index("## selected.json") < corpus.index("## articles_1.csv") < corpus.index("## weekly_recap.txt")
        )

    def test_corpus_skips_files_the_branch_does_not_have(self, tmp_path):
        branches = write_fanout.build_branches(_seed(tmp_path, extras=())).branches
        corpus = write_fanout.branch_corpus(branches[0].dir)
        assert "## weekly_recap.txt" not in corpus
        assert "## recent_digest_headlines.txt" not in corpus

    def test_single_turn_body_keeps_every_rule_and_drops_the_tools(self):
        body = WRITE_SPEC.read_text(encoding="utf-8").split("---", 2)[2].strip()
        out = write_fanout.single_turn_branch_body(write_fanout.branch_body(body, Path("/tmp/b/s00")))
        rules_start = body.index("**Writing style")
        rules_end = body.index("**Output schema:**")
        assert body[rules_start:rules_end] in out
        assert "Use the Read tool" not in out
        assert "Use the Write tool" not in out
        assert "DO NOT use Bash" not in out
        assert "Reply with the JSON object and nothing else" in out


# --------------------------------------------------------------------------- #
# Cohesion gate: a verdict narrows what a branch sees
# --------------------------------------------------------------------------- #


class TestCohesionNarrowsTheBranch:
    """cluster_cohesion.json (written by cohesion.py, read here as a file so this module stays
    a leaf) names the dominant event of a selected cluster. An applied verdict is the only
    thing that changes a branch's evidence; anything else leaves it exactly as today."""

    def test_dominant_replaces_the_cluster_and_filters_selects_citations(self, tmp_path):
        sel = {"must_know": [{"cluster_index": 0, "article_ids": ["A1", "A2"]}], "should_know": []}
        _seed(tmp_path, selected=sel)  # cluster 0 = A1, A2
        (tmp_path / "cluster_cohesion.json").write_text(
            json.dumps(
                {
                    "outcome": "completed",
                    "verdicts": [
                        {
                            "cluster_index": 0,
                            "article_ids": ["A1", "A2"],
                            "events": [["A1"], ["A2"]],
                            "dominant": ["A1"],
                            "strays": ["A2"],
                            "applied": True,
                            "reason": None,
                        }
                    ],
                }
            )
        )
        branch = write_fanout.build_branches(tmp_path).branches[0]
        assert branch.context_article_ids == ("A1",)
        assert branch.strays_removed == 1
        one = json.loads((branch.dir / "selected.json").read_text())
        assert one["must_know"][0]["article_ids"] == ["A1"]
        assert [r[0] for r in _branch_rows(branch) if r[0] != "article_id"] == ["A1"]

    def test_an_unapplied_verdict_changes_nothing(self, tmp_path):
        sel = {"must_know": [{"cluster_index": 0, "article_ids": ["A1", "A2"]}], "should_know": []}
        _seed(tmp_path, selected=sel)
        (tmp_path / "cluster_cohesion.json").write_text(
            json.dumps(
                {
                    "outcome": "completed",
                    "verdicts": [{"cluster_index": 0, "applied": False, "reason": "one event", "dominant": ["A1"]}],
                }
            )
        )
        branch = write_fanout.build_branches(tmp_path).branches[0]
        assert set(branch.context_article_ids) == {"A1", "A2"}
        assert branch.strays_removed == 0

    def test_a_malformed_artifact_changes_nothing(self, tmp_path):
        sel = {"must_know": [{"cluster_index": 0, "article_ids": ["A1", "A2"]}], "should_know": []}
        _seed(tmp_path, selected=sel)
        (tmp_path / "cluster_cohesion.json").write_text("{not json")
        branch = write_fanout.build_branches(tmp_path).branches[0]
        assert set(branch.context_article_ids) == {"A1", "A2"}
        assert branch.strays_removed == 0

    def test_a_dominant_that_would_leave_no_citation_keeps_selects_list(self, tmp_path):
        """cohesion.py refuses such a verdict, but the file is an input; defend here too."""
        sel = {"must_know": [{"cluster_index": 0, "article_ids": ["A2"]}], "should_know": []}
        _seed(tmp_path, selected=sel)
        (tmp_path / "cluster_cohesion.json").write_text(
            json.dumps(
                {"outcome": "completed", "verdicts": [{"cluster_index": 0, "applied": True, "dominant": ["A1"]}]}
            )
        )
        branch = write_fanout.build_branches(tmp_path).branches[0]
        one = json.loads((branch.dir / "selected.json").read_text())
        assert one["must_know"][0]["article_ids"] == ["A2"]
        assert set(branch.context_article_ids) == {"A1", "A2"}
        assert branch.strays_removed == 0


class TestClusterIndexDrift:
    """SELECT's cluster_index is a 0-based position a model counts into a several-hundred
    element list, and it drifts: on 182 of 1291 archived stories (14%, 79 runs) the index
    names a cluster holding none or few of the story's own citations, mostly off by one to
    three. threads stopped trusting it in July (utils.cluster_for_articles); the fan-out
    trusted it, so run 285's SCO story was written with a lone Treasury-yields article as
    context (index 234 where the citations live in 235). The citations decide."""

    def test_the_cluster_holding_the_citations_wins_over_the_index(self, tmp_path):
        sel = {"must_know": [{"cluster_index": 0, "article_ids": ["A3", "A4"]}], "should_know": []}
        _seed(tmp_path, selected=sel)  # cluster 0 = A1, A2; cluster 1 = A3, A4
        branch = write_fanout.build_branches(tmp_path).branches[0]
        assert branch.cluster_index == 1
        assert branch.selected_cluster_index == 0
        assert set(branch.context_article_ids) == {"A3", "A4"}

    def test_an_index_that_agrees_with_the_citations_is_kept(self, tmp_path):
        branch = write_fanout.build_branches(_seed(tmp_path)).branches[0]
        assert branch.cluster_index == 0 and branch.selected_cluster_index == 0

    def test_citations_in_no_cluster_fall_back_to_the_index(self, tmp_path):
        sel = {"must_know": [{"cluster_index": 1, "article_ids": ["A9"]}], "should_know": []}
        _seed(tmp_path, selected=sel)
        with open(tmp_path / "articles_2.csv", "a", newline="") as f:
            csv.writer(f).writerow(["A9", "s9", "Stray", "2026-09-01", "Body nine"])
        branch = write_fanout.build_branches(tmp_path).branches[0]
        assert branch.cluster_index == 1
        assert set(branch.context_article_ids) == {"A3", "A4", "A9"}
