"""Unit tests for eval_repair's deterministic scorer (no model calls).

The model-run path (run_agent_to_file/main) is exercised only by the opt-in
`bin/eval-repair` in Docker; here we pin score_repair's string-assertion and
action-conditional-preservation logic, which is the reusable scoring asset.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import eval_repair

_LABELS = {
    "repair": {
        "3": {
            "field": "headline",
            "expected_action": "substitute",
            "must_not_contain": ["most"],
            "must_contain_any": ["some"],
        },
        "4": {
            "field": "summary",
            "expected_action": "delete",
            "must_not_contain": ["two years"],
            "must_contain_any": [],
        },
    }
}
# idx 3 -> article_ids {A3}; idx 4 -> {A4}
_IDX_BY_IDS = {frozenset(["A3"]): 3, frozenset(["A4"]): 4}
_ORIG = {
    3: "Trump imposes 50% tariffs on most Canadian goods, citing discrimination",
    4: "...replacing Starmer after barely two years in office.",
}


def _score(tmp_path, results):
    (tmp_path / "repaired_fields.json").write_text(json.dumps({"results": results}))
    return eval_repair.score_repair(tmp_path / "repaired_fields.json", _LABELS, _IDX_BY_IDS, _ORIG)


class TestScoreRepair:
    def test_substitute_error_removed_when_bad_gone_and_replacement_present(self, tmp_path):
        s = _score(tmp_path, [{"article_ids": ["A3"], "headline": "Trump imposes 50% tariffs on some Canadian goods"}])
        assert s["error_removed"] == [3]
        assert s["per_idx"][3]["shape_ok"] is True

    def test_error_not_removed_when_flagged_specific_survives(self, tmp_path):
        s = _score(tmp_path, [{"article_ids": ["A3"], "headline": "Trump imposes 50% tariffs on most goods"}])
        assert s["error_removed"] == []

    def test_substitute_not_removed_without_supported_replacement(self, tmp_path):
        # "most" gone but no must_contain_any term -> not a confirmed correction.
        s = _score(tmp_path, [{"article_ids": ["A3"], "headline": "Trump imposes 50% tariffs on Canadian goods"}])
        assert s["error_removed"] == []

    def test_delete_needs_no_replacement(self, tmp_path):
        s = _score(tmp_path, [{"article_ids": ["A4"], "summary": "Burnham takes office as PM."}])
        assert s["error_removed"] == [4]

    def test_shape_bad_when_extra_field_returned(self, tmp_path):
        s = _score(tmp_path, [{"article_ids": ["A3"], "headline": "...some...", "summary": "sneaky"}])
        assert s["shape_bad"] == [3]

    def test_gutted_substitute_flagged(self, tmp_path):
        # A substitute answered by collapsing the field to almost nothing.
        s = _score(tmp_path, [{"article_ids": ["A3"], "headline": "some"}])
        assert s["gutted_substitutes"] == [3]

    def test_delete_short_output_not_gutted(self, tmp_path):
        # delete/shrink is EXPECTED to get shorter -> never a preservation fail.
        s = _score(tmp_path, [{"article_ids": ["A4"], "summary": "Burnham is PM."}])
        assert s["gutted_substitutes"] == []

    def test_missing_entry_counts_as_not_removed(self, tmp_path):
        s = _score(tmp_path, [{"article_ids": ["A3"], "headline": "Trump imposes 50% tariffs on some goods"}])
        assert s["missing"] == [4]
        assert 4 not in s["error_removed"]

    def test_unknown_article_ids_is_shape_error(self, tmp_path):
        s = _score(tmp_path, [{"article_ids": ["A99"], "headline": "x"}])
        assert s["shape_errors"]
