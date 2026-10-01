"""Index BM25 local pour la recherche lexicale, sans dépendance externe."""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence

_TOKEN_PATTERN = re.compile(r"\w+", re.UNICODE)


def tokenize(text: str) -> list[str]:
    return _TOKEN_PATTERN.findall(text.lower())


class Bm25Index:
    def __init__(self, documents: Sequence[str], k1: float = 1.5, b: float = 0.75) -> None:
        self._k1 = k1
        self._b = b
        self._tokens = [tokenize(document) for document in documents]
        self._counts = [Counter(tokens) for tokens in self._tokens]
        self._doc_count = len(documents)
        self._average_length = (
            sum(len(tokens) for tokens in self._tokens) / self._doc_count if self._doc_count else 0.0
        )
        self._doc_frequency: Counter = Counter()
        for counts in self._counts:
            self._doc_frequency.update(counts)
        self._idf = {
            token: math.log(1.0 + (self._doc_count - doc_freq + 0.5) / (doc_freq + 0.5))
            for token, doc_freq in self._doc_frequency.items()
        }

    def scores(self, query: str) -> list[float]:
        result = [0.0] * self._doc_count
        for token in tokenize(query):
            idf = self._idf.get(token)
            if idf is None:
                continue
            for index, (counts, tokens) in enumerate(zip(self._counts, self._tokens)):
                frequency = counts[token]
                if not frequency:
                    continue
                length = len(tokens)
                result[index] += idf * (frequency * (self._k1 + 1.0)) / (
                    frequency + self._k1 * (1.0 - self._b + self._b * length / self._average_length)
                )
        return result
