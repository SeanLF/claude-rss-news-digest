"""Tests for eval_stages.py (per-stage L1 code-assertion graders).

Each stage grader gets two proofs: the committed run-195 fixture passes every
check, and a targeted broken input makes exactly the relevant check fail. No
network, no DB -- loads only the committed fixture files under
``tests/fixtures/stages/``.
"""

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from eval_stages import (
    MissingArtifactError,
    grade_cluster,
    grade_coherence,
    grade_recap,
    grade_select,
    grade_write,
    load_stage_artifacts_from_dir,
)

FIXTURES = Path(__file__).parent / "fixtures" / "stages"


# --------------------------------------------------------------------------- #
# Fixture loading (committed run-195 artifacts, no DB).
# --------------------------------------------------------------------------- #


def _load(name):
    text = (FIXTURES / name).read_text(encoding="utf-8")
    return json.loads(text) if name.endswith(".json") else text


@pytest.fixture(scope="module")
def article_index():
    return _load("article_index.json")


@pytest.fixture(scope="module")
def clusters():
    return _load("clusters.json")


@pytest.fixture(scope="module")
def selected():
    return _load("selected.json")


@pytest.fixture(scope="module")
def draft():
    return _load("draft_selections.json")


@pytest.fixture(scope="module")
def coherence():
    return _load("coherence_report.json")


def _check(report, name):
    return next(c for c in report.checks if c.name == name)


def _names_failed(report):
    return {c.name for c in report.failures}


# --------------------------------------------------------------------------- #
# CLUSTER.
# --------------------------------------------------------------------------- #


def test_cluster_golden_passes(clusters, article_index):
    report = grade_cluster(clusters, article_index)
    assert report.passed, f"unexpected failures: {_names_failed(report)}"


def test_cluster_empty_fails():
    report = grade_cluster({"clusters": []}, {})
    assert not report.passed
    assert not _check(report, "clusters_present").passed


def test_cluster_unknown_article_id_fails(clusters, article_index):
    broken = copy.deepcopy(clusters)
    broken["clusters"][0]["article_ids"].append("A999999")
    report = grade_cluster(broken, article_index)
    assert _names_failed(report) == {"cluster_ids_in_index"}


def test_cluster_story_empty_fails(clusters, article_index):
    broken = copy.deepcopy(clusters)
    broken["clusters"][0]["story"] = "   "
    report = grade_cluster(broken, article_index)
    assert not _check(report, "cluster_story_nonempty").passed


def test_cluster_duplicate_assignment_fails(clusters, article_index):
    broken = copy.deepcopy(clusters)
    # Re-assign an article from cluster 0 into cluster 1.
    dup_id = broken["clusters"][0]["article_ids"][0]
    broken["clusters"][1]["article_ids"].append(dup_id)
    report = grade_cluster(broken, article_index)
    assert not _check(report, "cluster_no_duplicate_assignment").passed


# --------------------------------------------------------------------------- #
# RECAP.
# --------------------------------------------------------------------------- #


def test_recap_good_passes():
    report = grade_recap(_load("recap_good.txt"))
    assert report.passed, f"unexpected failures: {_names_failed(report)}"


def test_recap_run195_stub_fails():
    # The recorded run-195 recap is the missing-input stub -- the grader MUST
    # flag it (the floor's job), so this is a documented broken case, not green.
    report = grade_recap(_load("recap.txt"))
    assert not report.passed
    assert not _check(report, "recap_not_stub").passed


def test_recap_empty_fails():
    report = grade_recap("   ")
    assert not _check(report, "recap_nonempty").passed


def test_recap_too_long_fails():
    report = grade_recap("word " * 200)
    assert not _check(report, "recap_length").passed


def test_recap_sentence_count_ignores_abbreviations():
    # Two real sentences, peppered with abbreviations that a naive split would
    # miscount as sentence ends. Must NOT trip the sentence-count check.
    text = (
        "The U.S. and the E.U. clashed with China over trade, while Mr. Smith's "
        "panel met in Washington D.C. Talks on the U.K. budget dominated, vs. "
        "expectations of a quiet week."
    )
    report = grade_recap(text)
    assert _check(report, "recap_sentence_count").passed, _check(report, "recap_sentence_count").detail


def test_recap_too_many_sentences_fails():
    text = "One thing. Two thing. Three thing. Four thing. Five thing. Six thing. Seven thing."
    report = grade_recap(text)
    assert not _check(report, "recap_sentence_count").passed


def test_recap_bullet_list_fails():
    text = "This week's themes:\n- Iran peace framework\n- EU-China trade\n- Helicopter crash"
    report = grade_recap(text)
    assert not _check(report, "recap_paragraph").passed


def test_recap_reproducing_a_source_headline_verbatim_fails():
    titles = ["Senate votes to halt Iran war in rare rebuke to Trump", "EU and China escalate trade tensions"]
    text = "In a notable week, the Senate votes to halt Iran war in rare rebuke to Trump, alarming allies."
    report = grade_recap(text, source_titles=titles)
    assert not _check(report, "recap_no_headline_reproduction").passed


def test_recap_thematic_summary_with_titles_passes_reproduction_check():
    titles = ["Senate votes to halt Iran war in rare rebuke to Trump", "EU and China escalate trade tensions"]
    text = "US-Iran tensions and a congressional rebuke shaped the week, alongside escalating EU-China trade friction."
    report = grade_recap(text, source_titles=titles)
    assert _check(report, "recap_no_headline_reproduction").passed


# --------------------------------------------------------------------------- #
# SELECT.
# --------------------------------------------------------------------------- #


def test_select_golden_passes(selected, clusters):
    report = grade_select(selected, clusters)
    assert report.passed, f"unexpected failures: {_names_failed(report)}"


def test_select_count_out_of_range_fails(selected, clusters):
    broken = copy.deepcopy(selected)
    broken["should_know"] = broken["should_know"][:1]  # below should_know min (3)
    report = grade_select(broken, clusters)
    assert not _check(report, "select_counts_in_range").passed


def test_select_bad_cluster_index_fails(selected, clusters):
    broken = copy.deepcopy(selected)
    broken["must_know"][0]["cluster_index"] = 99999
    report = grade_select(broken, clusters)
    assert not _check(report, "select_cluster_index_resolves").passed


def test_select_stray_article_id_fails(selected, clusters):
    broken = copy.deepcopy(selected)
    broken["must_know"][0]["article_ids"].append("A_NOT_IN_CLUSTER")
    report = grade_select(broken, clusters)
    assert not _check(report, "select_article_ids_in_cluster").passed


# --------------------------------------------------------------------------- #
# WRITE.
# --------------------------------------------------------------------------- #


def test_write_golden_passes(draft, article_index):
    report = grade_write(draft, article_index)
    assert report.passed, f"unexpected failures: {_names_failed(report)}"


def test_write_unknown_source_id_fails(draft, article_index):
    broken = copy.deepcopy(draft)
    broken["must_know"][0]["sources"].append({"article_id": "A999999"})
    report = grade_write(broken, article_index)
    assert not _check(report, "write_source_ids_in_index").passed


def test_write_empty_headline_fails(draft, article_index):
    broken = copy.deepcopy(draft)
    broken["must_know"][0]["headline"] = ""
    report = grade_write(broken, article_index)
    assert not _check(report, "write_text_fields_nonempty").passed


def test_write_sourceless_item_fails(draft, article_index):
    broken = copy.deepcopy(draft)
    broken["must_know"][0]["sources"] = []
    report = grade_write(broken, article_index)
    assert not _check(report, "write_sources_nonempty").passed


def test_write_headline_over_cap_fails(draft, article_index):
    broken = copy.deepcopy(draft)
    broken["must_know"][0]["headline"] = "word " * 30
    report = grade_write(broken, article_index)
    assert not _check(report, "write_headline_words").passed


def test_write_preheader_too_long_fails(draft, article_index):
    broken = copy.deepcopy(draft)
    broken["preheader"] = "x" * 200
    report = grade_write(broken, article_index)
    assert not _check(report, "write_preheader_length").passed


def test_write_preheader_missing_fails(draft, article_index):
    broken = copy.deepcopy(draft)
    del broken["preheader"]
    report = grade_write(broken, article_index)
    assert not _check(report, "write_preheader_present").passed


# --------------------------------------------------------------------------- #
# COHERENCE.
# --------------------------------------------------------------------------- #


def test_coherence_golden_passes(coherence, draft):
    report = grade_coherence(coherence, draft)
    assert report.passed, f"unexpected failures: {_names_failed(report)}"


def test_coherence_missing_verdict_fails(coherence, draft):
    broken = copy.deepcopy(coherence)
    broken["results"].pop()  # drop one headline's verdict
    report = grade_coherence(broken, draft)
    assert not _check(report, "coherence_covers_all_headlines").passed


def test_coherence_non_bool_pass_fails(coherence, draft):
    broken = copy.deepcopy(coherence)
    broken["results"][0]["pass"] = "yes"
    report = grade_coherence(broken, draft)
    assert not _check(report, "coherence_pass_is_bool").passed


def test_coherence_empty_results_fails(draft):
    report = grade_coherence({"results": []}, draft)
    assert not _check(report, "coherence_results_present").passed


# --------------------------------------------------------------------------- #
# Loader.
# --------------------------------------------------------------------------- #


def test_loader_reads_committed_fixtures():
    stages = load_stage_artifacts_from_dir(FIXTURES)
    assert set(stages) >= {"clusters.json", "recap.txt", "draft_selections.json"}
    assert isinstance(stages["clusters.json"], dict)
    assert isinstance(stages["recap.txt"], str)


def test_loader_fails_loudly_on_missing(tmp_path):
    (tmp_path / "clusters.json").write_text("{}", encoding="utf-8")
    with pytest.raises(MissingArtifactError):
        load_stage_artifacts_from_dir(tmp_path)
