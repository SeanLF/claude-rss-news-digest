"""TF-IDF similarity matching for headline deduplication.

Filters articles that are too similar to recently shown headlines,
preventing the same story from appearing in consecutive digests.
"""

import math
import re
from collections import Counter

# Common English stopwords to filter from TF-IDF matching
STOPWORDS = frozenset(
    [
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
