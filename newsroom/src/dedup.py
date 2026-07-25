"""TF-IDF similarity matching for headline deduplication.

Filters articles that are too similar to recently shown headlines,
preventing the same story from appearing in consecutive digests.
"""

import math
import re
from collections import Counter

# Stopwords filtered from TF-IDF matching.
#
# English-only was a latent false-positive generator once non-English feeds landed
# (le_monde French, der_spiegel German, clarin_mundo Spanish). A German function word
# is RARE in a mostly-English corpus, so IDF scores it HIGH, and formulaic German
# headlines then collide on their grammar rather than their content -- measured: a
# Nigeria attack story vs an Afghanistan earthquake scored 0.835, over the 0.80 cut,
# which would silently drop one before curation ever saw it. These carry no topical
# signal in any language, so filtering them cannot cost recall.
STOPWORDS = frozenset(
    [
        # --- German ---
        "der",
        "die",
        "das",
        "den",
        "dem",
        "des",
        "ein",
        "eine",
        "einen",
        "einem",
        "eines",
        "und",
        "oder",
        "aber",
        "nicht",
        "mit",
        "von",
        "vom",
        "zu",
        "zum",
        "zur",
        "für",
        "auf",
        "aus",
        "bei",
        "beim",
        "nach",
        "über",
        "unter",
        "vor",
        "durch",
        "gegen",
        "ist",
        "sind",
        "war",
        "waren",
        "wird",
        "werden",
        "hat",
        "haben",
        "hatte",
        "sich",
        "auch",
        "noch",
        "wie",
        "was",
        "wer",
        "sie",
        "sein",
        "seine",
        "ihre",
        "mehr",
        # --- Spanish ---
        "el",
        "la",
        "los",
        "las",
        "un",
        "una",
        "unos",
        "unas",
        "del",
        "al",
        "y",
        "o",
        "pero",
        "no",
        "con",
        "de",
        "en",
        "por",
        "para",
        "sobre",
        "entre",
        "desde",
        "hasta",
        "es",
        "son",
        "era",
        "fue",
        "ser",
        "está",
        "están",
        "que",
        "como",
        "más",
        "su",
        "sus",
        "se",
        "lo",
        "le",
        "les",
        "ya",
        "tras",
        # --- French ---
        "le",
        "les",
        "un",
        "une",
        "des",
        "du",
        "et",
        "ou",
        "mais",
        "ne",
        "pas",
        "avec",
        "dans",
        "sur",
        "sous",
        "pour",
        "par",
        "vers",
        "chez",
        "est",
        "sont",
        "était",
        "être",
        "qui",
        "que",
        "quoi",
        "plus",
        "ses",
        "leur",
        "leurs",
        "aux",
        "cette",
        # --- English ---
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "with",
        "by",
        "from",
        "as",
        "is",
        "was",
        "are",
        "were",
        "been",
        "be",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "must",
        "shall",
        "can",
        "need",
        "dare",
        "ought",
        "used",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "i",
        "you",
        "he",
        "she",
        "we",
        "they",
        "me",
        "him",
        "her",
        "us",
        "them",
        "my",
        "your",
        "his",
        "our",
        "their",
        "what",
        "which",
        "who",
        "whom",
        "when",
        "where",
        "why",
        "how",
        "all",
        "each",
        "every",
        "both",
        "few",
        "more",
        "most",
        "other",
        "some",
        "such",
        "no",
        "nor",
        "not",
        "only",
        "own",
        "same",
        "so",
        "than",
        "too",
        "very",
        "just",
        "also",
        "now",
        "after",
        "before",
        "during",
        "about",
        "into",
        "through",
        "above",
        "below",
        "up",
        "down",
        "out",
        "off",
        "over",
        "under",
        "again",
        "further",
        "then",
        "once",
        "here",
        "there",
        "any",
        "s",
        "t",
        "don",
        "didn",
        "doesn",
        "hasn",
        "haven",
        "isn",
        "wasn",
        "weren",
        "won",
        "wouldn",
        "couldn",
        "shouldn",
        "ain",
        "aren",
        "hadn",
    ]
)


def tokenize(text: str) -> list[str]:
    """Lowercase, strip punctuation, split into words, remove stopwords."""
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return [w for w in text.split() if w not in STOPWORDS]


class TfidfMatcher:
    """TF-IDF similarity matcher for headline deduplication."""

    def __init__(self, headlines: list[str]):
        self.headlines = headlines
        self._documents = [tokenize(h) for h in headlines]
        self.idf = self._compute_idf()
        # Pre-compute vectors for corpus (queried many times)
        self._doc_vectors = [self._tfidf_vector(doc) for doc in self._documents]

    def _compute_idf(self) -> dict[str, float]:
        """Compute inverse document frequency for each word."""
        n_docs = len(self._documents)
        if n_docs == 0:
            return {}

        doc_freq: Counter[str] = Counter()
        for doc in self._documents:
            doc_freq.update(set(doc))

        return {word: math.log(n_docs / (1 + df)) for word, df in doc_freq.items()}

    def _tfidf_vector(self, doc: list[str]) -> dict[str, float]:
        """Compute TF-IDF vector for a document."""
        if not doc:
            return {}
        tf = Counter(doc)
        max_tf = max(tf.values())
        return {word: (count / max_tf) * self.idf[word] for word, count in tf.items() if word in self.idf}

    def _cosine_similarity(self, vec1: dict[str, float], vec2: dict[str, float]) -> float:
        """Compute cosine similarity between two sparse vectors."""
        if not vec1 or not vec2:
            return 0.0
        # Only iterate over shared keys for dot product (others contribute 0)
        shared_keys = vec1.keys() & vec2.keys()
        if not shared_keys:
            return 0.0
        dot = sum(vec1[w] * vec2[w] for w in shared_keys)
        mag1 = math.sqrt(sum(v * v for v in vec1.values()))
        mag2 = math.sqrt(sum(v * v for v in vec2.values()))
        return dot / (mag1 * mag2)

    def find_most_similar(self, text: str) -> tuple[str | None, float]:
        """Find most similar headline and its similarity score."""
        if not self.headlines:
            return None, 0.0

        query_vec = self._tfidf_vector(tokenize(text))
        best_headline = None
        best_score = 0.0

        for i, doc_vec in enumerate(self._doc_vectors):
            score = self._cosine_similarity(query_vec, doc_vec)
            if score > best_score:
                best_score = score
                best_headline = self.headlines[i]

        return best_headline, best_score
