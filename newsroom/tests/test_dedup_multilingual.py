"""Cross-day dedup must not fire on grammar when the corpus turns multilingual.

`dedup.TfidfMatcher` filters the cross-day blocklist. Its stopword list was English-only,
which is safe while every headline is English and dangerous the moment it is not: a German
function word is RARE in a mostly-English corpus, so IDF scores it HIGH, and two unrelated
German headlines then match on their shared grammar. A drop here is silent and total --
`prepare.py` discards the article before curation ever sees it.

Live as of 2026-07-25: le_monde (French), der_spiegel (German world desk), clarin_mundo
(Spanish). The blocklist is `shown_narratives.original_title` over 7 days, so this only
bites once non-English titles have accumulated -- days after deploy, not on the first run.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from config import DEDUP_SIMILARITY_THRESHOLD
from dedup import STOPWORDS, TfidfMatcher, tokenize


@pytest.mark.parametrize(
    ("blocklist", "query", "language"),
    [
        # Measured at cosine 0.835 before the fix -- over the 0.80 cut, so the earthquake
        # story would have been dropped as a duplicate of the Nigeria attack.
        (
            "Mindestens 30 Tote bei Angriff auf Dorf in Nigeria",
            "Mindestens 20 Tote nach schwerem Erdbeben in Afghanistan",
            "German",
        ),
        (
            "Le président de la République annonce une réforme des retraites",
            "Le président de la Commission annonce une réforme des marchés",
            "French",
        ),
        (
            "Milei viaja a Brasil para apoyar la campaña de Bolsonaro",
            "Lula anuncia un nuevo plan de inversión en el noreste del país",
            "Spanish",
        ),
    ],
)
def test_unrelated_non_english_headlines_are_not_matched(blocklist, query, language):
    """Two different stories sharing only function words must stay below the drop cut."""
    _, similarity = TfidfMatcher([blocklist]).find_most_similar(query)
    assert similarity < DEDUP_SIMILARITY_THRESHOLD, (
        f"{language}: unrelated headlines scored {similarity:.3f}, at/over the "
        f"{DEDUP_SIMILARITY_THRESHOLD} drop threshold -- a real story would be silently discarded"
    )


def test_genuine_non_english_duplicates_still_match():
    """The fix must not buy precision by destroying recall: a real restatement of the same
    story, in the same language, must still be caught."""
    blocklist = "Waldbrände in Spanien und Frankreich zwingen 250.000 Menschen zur Flucht"
    query = "Waldbrände in Spanien und Frankreich zwingen 200.000 Menschen zur Flucht"
    _, similarity = TfidfMatcher([blocklist]).find_most_similar(query)
    assert similarity >= DEDUP_SIMILARITY_THRESHOLD, f"real duplicate scored only {similarity:.3f}"


@pytest.mark.parametrize(
    ("a", "b"),
    [
        (
            "Türkei und Griechenland streiten über Migration im Mittelmeer",
            "Türkei und Griechenland streiten über Gasvorkommen im Mittelmeer",
        ),
        (
            "El Gobierno de España aprueba una nueva ley sobre la vivienda",
            "El Gobierno de Francia aprueba una nueva ley sobre las pensiones",
        ),
    ],
)
def test_known_limit_heavy_content_word_overlap_still_fires(a, b):
    """Documents what the stopword fix does NOT solve, so nobody mistakes it for a cure.

    These pairs share most of their CONTENT words (Türkei/Griechenland/streiten/Mittelmeer;
    Gobierno/aprueba/nueva/ley) and differ only in the topic noun. Stopwords cannot help --
    the residual is real lexical overlap, and TF-IDF over a ~8-word headline cannot tell a
    gas dispute from a migration dispute between the same two countries.

    This is the same weakness already recorded for cross-day dedup (measured 65%
    false-positive rate at the 0.35 setting); non-English simply makes formulaic
    constructions more common. The fix for THIS is entity/event matching, not tokenisation.
    """
    _, similarity = TfidfMatcher([a]).find_most_similar(b)
    assert similarity >= DEDUP_SIMILARITY_THRESHOLD, (
        "if this now passes the threshold, the residual overlap problem improved -- "
        "re-measure and update the claim rather than deleting the test"
    )


def test_stopwords_cover_the_languages_actually_in_sources():
    """Guard against a language being added to sources.json without its function words."""
    for lang, sample in (
        ("German", {"der", "die", "und", "für", "über", "nicht"}),
        ("Spanish", {"el", "los", "del", "por", "para", "que"}),
        ("French", {"le", "les", "des", "pour", "dans", "qui"}),
    ):
        missing = sample - STOPWORDS
        assert not missing, f"{lang} function words missing from STOPWORDS: {sorted(missing)}"


def test_tokenize_keeps_accented_words_as_content():
    """Accented content words must survive tokenisation -- if they were stripped, the
    stopword fix would be masking a worse bug."""
    assert "waldbrände" in tokenize("Waldbrände in Spanien")
    assert "españa" in tokenize("Incendios en España")
