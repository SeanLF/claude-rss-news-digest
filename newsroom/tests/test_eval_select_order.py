"""The SELECT order-dependence harness: pure parts, plus its own negative control."""

import json
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import eval_select_order as eso

CLUSTERS = [
    {"story": "zero", "article_ids": ["A1", "A2"]},
    {"story": "one", "article_ids": ["A3"]},
    {"story": "two", "article_ids": ["A4", "A5", "A6"]},
    {"story": "three", "article_ids": ["A7"]},
]
# A non-ASCII label, so a serialisation that differs from production's in escaping shows up.
CLUSTERS_ACCENTED = [{**c, "story": c["story"] + " Canelón £1bn"} for c in CLUSTERS]


def test_permutation_is_a_bijection_and_inverse_recovers_the_original():
    permuted, perm = eso.permute_clusters(CLUSTERS, random.Random(7))
    assert sorted(perm) == list(range(len(CLUSTERS)))
    # perm[new_pos] == original index: walking the permuted list through perm restores the input.
    assert [CLUSTERS[perm[i]] for i in range(len(permuted))] == permuted
    assert eso.to_original(perm, list(range(len(permuted)))) == perm


def test_identity_permutation_is_the_fixed_arm():
    permuted, perm = eso.permute_clusters(CLUSTERS, None)
    assert permuted == CLUSTERS
    assert perm == [0, 1, 2, 3]


def test_canonical_selection_maps_positions_back_through_the_permutation():
    permuted, perm = eso.permute_clusters(CLUSTERS, random.Random(3))
    # The "model" selected the cluster now at position 0 of the permuted list, citing its ids.
    pos0 = permuted[0]
    selected = {"must_know": [{"cluster_index": 0, "article_ids": pos0["article_ids"]}], "should_know": []}
    canon = eso.canonical_selection(selected, permuted, perm)
    assert canon["must_know"] == frozenset({CLUSTERS.index(pos0)})
    assert canon["all"] == canon["must_know"]
    assert canon["drifted"] == 0


def test_a_drifted_cluster_index_is_resolved_by_citations_and_counted():
    # SELECT names position 1 but cites cluster 2's articles: the citations decide, as in prod.
    selected = {"must_know": [], "should_know": [{"cluster_index": 1, "article_ids": ["A4", "A5"]}]}
    canon = eso.canonical_selection(selected, CLUSTERS, [0, 1, 2, 3])
    assert canon["should_know"] == frozenset({2})
    assert canon["drifted"] == 1


def test_a_story_citing_nothing_known_is_dropped_not_crashed():
    selected = {"must_know": [{"cluster_index": 9, "article_ids": ["A99"]}], "should_know": []}
    canon = eso.canonical_selection(selected, CLUSTERS, [0, 1, 2, 3])
    assert canon["all"] == frozenset()
    assert canon["unresolved"] == 1


def test_pairwise_jaccard_bounds():
    a, b, c = frozenset({1, 2, 3}), frozenset({1, 2, 3}), frozenset({4})
    assert eso.pairwise_jaccard([a, b]) == [1.0]
    assert eso.pairwise_jaccard([a, c]) == [0.0]
    assert eso.pairwise_jaccard([a, b, c]) == [1.0, 0.0, 0.0]
    assert eso.pairwise_jaccard([frozenset(), frozenset()]) == [1.0]  # two empty picks agree


def test_summary_reports_each_arm_and_selection_frequency():
    reps = {
        "fixed": [
            {"all": frozenset({0, 1}), "must_know": frozenset({0}), "drifted": 0, "unresolved": 0},
            {"all": frozenset({0, 2}), "must_know": frozenset({0}), "drifted": 1, "unresolved": 0},
        ],
        "shuffled": [
            {"all": frozenset({1}), "must_know": frozenset({1}), "drifted": 0, "unresolved": 0},
            {"all": frozenset({3}), "must_know": frozenset(), "drifted": 0, "unresolved": 1},
        ],
    }
    s = eso.summarise(reps, n_clusters=4)
    assert s["fixed"]["all_jaccard_mean"] == pytest.approx(1 / 3)
    assert s["fixed"]["must_know_jaccard_mean"] == 1.0
    assert s["shuffled"]["all_jaccard_mean"] == 0.0
    assert s["fixed"]["drifted"] == 1 and s["shuffled"]["unresolved"] == 1
    assert s["fixed"]["always_selected"] == [0]
    assert s["cross_fixed_shuffled_all_jaccard_mean"] == pytest.approx((0.5 + 0 + 0 + 0) / 4)


def test_negative_control_round_trips_the_archived_selection(tmp_path):
    """The harness's own instrument check: push the archived selected.json through a permutation
    as if a model had answered in the permuted space, and it must canonicalise to the same
    clusters as the unpermuted file. A broken mapping fails here with no model call."""
    (tmp_path / "clusters.json").write_text(json.dumps({"clusters": CLUSTERS}))
    (tmp_path / "selected.json").write_text(
        json.dumps(
            {
                "must_know": [{"cluster_index": 2, "article_ids": ["A4", "A6"]}],
                "should_know": [{"cluster_index": 0, "article_ids": ["A1"]}],
            }
        )
    )
    assert eso.negative_control(tmp_path, seed=11) == {"ok": True, "original": [0, 2], "round_trip": [0, 2]}


def test_negative_control_catches_a_mapping_that_does_not_invert(tmp_path, monkeypatch):
    (tmp_path / "clusters.json").write_text(json.dumps({"clusters": CLUSTERS}))
    (tmp_path / "selected.json").write_text(
        json.dumps({"must_know": [{"cluster_index": 2, "article_ids": ["A4"]}], "should_know": []})
    )
    # A wrong inverse that is never accidentally right: every position lands one cluster over.
    monkeypatch.setattr(eso, "to_original", lambda perm, positions: [(perm[p] + 1) % len(perm) for p in positions])
    assert eso.negative_control(tmp_path, seed=11)["ok"] is False


def _fixture(tmp_path):
    fx = tmp_path / "fx"
    fx.mkdir()
    (fx / "clusters.json").write_text(json.dumps({"clusters": CLUSTERS_ACCENTED}, indent=2))
    (fx / "recap.txt").write_text("recap")
    (fx / "articles_1.csv").write_text("article_id,source_id,title,published,summary\nA1,s,t,p,x\n")
    (fx / "selected.json").write_text("{}")  # the archived answer must NOT leak into a rep
    return fx


def test_prepare_workdir_writes_permuted_clusters_as_production_does(tmp_path):
    fx = _fixture(tmp_path)
    work = tmp_path / "work"
    perm = eso.order_for("shuffled", CLUSTERS, random.Random(5))
    assert eso.prepare_workdir(fx, work, perm) == perm
    assert not (work / "selected.json").exists()
    assert (work / "recap.txt").read_text() == "recap"
    text = (work / "clusters.json").read_text(encoding="utf-8")
    assert json.loads(text)["clusters"] == [CLUSTERS_ACCENTED[i] for i in perm]
    # Byte-identical to cluster_extractjoin's writer, `json.dumps(out, indent=2)`, ASCII escapes
    # included: the fixed arm reads exactly what prod reads.
    assert text == json.dumps({"clusters": [CLUSTERS_ACCENTED[i] for i in perm]}, indent=2)
    assert "\\u00f3" in text and "ó" not in text
    assert not (work / "permutation.json").exists()  # the arm is not readable from the input dir
    assert json.loads((tmp_path / "work.permutation.json").read_text()) == perm


def test_prepare_workdir_refuses_a_fixture_with_no_article_rows(tmp_path):
    fx = _fixture(tmp_path)
    (fx / "articles_1.csv").unlink()
    with pytest.raises(RuntimeError, match="no article rows"):
        eso.prepare_workdir(fx, tmp_path / "work", None)
    (fx / "articles_1.csv").write_text("article_id,source_id,title,published,summary\n")  # header only
    with pytest.raises(RuntimeError, match="no article rows"):
        eso.prepare_workdir(fx, tmp_path / "work", None)


def test_prepare_workdir_refuses_a_non_permutation(tmp_path):
    with pytest.raises(ValueError):
        eso.prepare_workdir(_fixture(tmp_path), tmp_path / "work", [0, 0, 1, 2])


def test_order_for_each_arm():
    assert eso.order_for("fixed", CLUSTERS, random.Random(1)) == [0, 1, 2, 3]
    assert sorted(eso.order_for("shuffled", CLUSTERS, random.Random(1))) == [0, 1, 2, 3]
    # size-descending, stable: sizes are 2,1,3,1 -> 2,0,1,3
    assert eso.order_for("sorted", CLUSTERS, random.Random(1)) == [2, 0, 1, 3]
    with pytest.raises(ValueError):
        eso.order_for("random", CLUSTERS, random.Random(1))


def test_parse_arms_rejects_unknown_empty_and_duplicate():
    assert eso.parse_arms("fixed, sorted") == ["fixed", "sorted"]
    for bad in ("fixed,foo", "", "fixed,fixed"):
        with pytest.raises(SystemExit):
            eso.parse_arms(bad)


def test_rep_order_interleaves_arms_so_time_is_not_an_arm():
    order = eso.rep_order(["fixed", "shuffled", "sorted"], 2)
    assert order == [("fixed", 0), ("shuffled", 0), ("sorted", 0), ("fixed", 1), ("shuffled", 1), ("sorted", 1)]
    # No arm's rep i+1 is created before every arm's rep i.
    firsts = [order.index((a, 1)) for a in ("fixed", "shuffled", "sorted")]
    lasts = [order.index((a, 0)) for a in ("fixed", "shuffled", "sorted")]
    assert min(firsts) > max(lasts)


def test_negative_control_refuses_a_vacuous_pass(tmp_path):
    (tmp_path / "clusters.json").write_text(json.dumps({"clusters": CLUSTERS}))
    (tmp_path / "selected.json").write_text(json.dumps({"must_know": [], "should_know": []}))
    assert eso.negative_control(tmp_path, seed=1)["ok"] is False


def test_summary_carries_cost_and_every_cross_arm_pair():
    rep = {
        "all": frozenset({0}),
        "must_know": frozenset(),
        "drifted": 0,
        "unresolved": 0,
        "stories": 1,
        "cost_usd": 0.4,
    }
    s = eso.summarise({"fixed": [rep, rep], "shuffled": [rep], "sorted": [rep]}, n_clusters=2)
    assert s["fixed"]["cost_usd"] == 0.8 and s["fixed"]["stories"] == 2
    assert {k for k in s if k.startswith("cross_")} == {
        "cross_fixed_shuffled_all_jaccard_mean",
        "cross_fixed_sorted_all_jaccard_mean",
        "cross_shuffled_sorted_all_jaccard_mean",
    }


def test_require_picks_refuses_an_empty_tier():
    ok = {"all": frozenset({0}), "must_know": frozenset({0})}
    eso.require_picks(ok, "x")
    for bad in ({"all": frozenset(), "must_know": frozenset()}, {"all": frozenset({1}), "must_know": frozenset()}):
        with pytest.raises(RuntimeError, match="picked nothing"):
            eso.require_picks(bad, "x")


def test_permutation_tests_gap_and_shift_on_known_sets():
    same = [frozenset({1, 2, 3}), frozenset({1, 2, 4}), frozenset({1, 3, 4})]
    # Identical arms: no gap, and no split is more "shifted" than the true one (p at the top).
    # (shift itself is slightly negative here because cross pairs include each set with its
    # own copy, which within pairs never do; the p-value, not the raw statistic, is the reading.)
    t = eso.permutation_tests(same, list(same))
    assert t["gap"] == 0 and t["gap_p_two_sided"] == 1.0
    assert t["shift_p_one_sided"] == 1.0
    assert t["splits"] == 20
    # Two arms each perfectly self-consistent but on DISJOINT clusters: the gap test sees
    # nothing, the shift test sees the maximum shift (at 3v3 the p floor is 2/20) -- the case
    # that fooled the second write-up.
    a = [frozenset({1, 2, 3})] * 3
    b = [frozenset({7, 8, 9})] * 3
    t = eso.permutation_tests(a, b)
    assert t["gap"] == 0 and t["gap_p_two_sided"] == 1.0
    assert t["shift"] == 1.0 and t["shift_p_one_sided"] == pytest.approx(2 / 20)  # the two true splits
    # Unequal self-consistency: shift is the MEAN within minus cross, not either arm's within.
    tight = [frozenset({1, 2, 3})] * 3  # within 1.0
    loose = [frozenset({7, 8}), frozenset({7, 9}), frozenset({8, 9})]  # within 1/3, disjoint from tight
    t = eso.permutation_tests(tight, loose)
    assert t["shift"] == pytest.approx((1.0 + 1 / 3) / 2 - 0.0, abs=1e-4)
    assert t["gap"] == pytest.approx(1.0 - 1 / 3, abs=1e-4)
    assert t["shift_p_one_sided"] == pytest.approx(2 / 20)
    # Asymmetric sizes: the split enumerates len(a), so a 2-vs-4 pool has C(6,2)=15 splits.
    t = eso.permutation_tests(tight[:2], [*loose, frozenset({7, 8})])
    assert t["splits"] == 15
    # One arm noisy, the other not: the gap test sees it.
    noisy = [frozenset({1}), frozenset({2}), frozenset({3})]
    t = eso.permutation_tests(a, noisy)
    assert t["gap"] == 1.0 and t["gap_p_two_sided"] == pytest.approx(2 / 20)
    assert t["gap_threshold_p05"] is None or t["gap_threshold_p05"] <= 1.0
    # The shift test reports the same "what could it have seen" pair as the gap test.
    assert t["shift_threshold_p05"] is None or t["shift_threshold_p05"] <= t["shift_max"]
    assert t["shift_max"] >= t["shift"]


def test_summary_runs_the_tests_for_every_pair():
    rep = lambda *c: {"all": frozenset(c), "must_know": frozenset(c[:1]), "drifted": 0, "unresolved": 0}  # noqa: E731
    s = eso.summarise({"fixed": [rep(1, 2), rep(1, 3)], "sorted": [rep(1, 2), rep(2, 3)]}, n_clusters=4)
    assert set(s["tests"]) == {"fixed_vs_sorted"}
    assert set(s["tests"]["fixed_vs_sorted"]) == {"all", "must_know"}
    assert s["tests"]["fixed_vs_sorted"]["all"]["splits"] == 6
