"""Small, dependency-free lexical utilities used by the heuristic scorer.

Deliberately simple and inspectable: light stemming, stopwords, unigram and
bigram matching, and TF-IDF cosine similarity.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from typing import Iterable, Mapping

STOPWORDS = frozenset(
    """a an and are as at be based by can for from has have how in into is it its of on or our over
    that the their them these this those through to towards toward under using via we what when where
    which while with within without new novel paper approach method methods study results show propose
    proposed propose towards about across between both more most such than then there they use used""".split()
)

_TOKEN = re.compile(r"[a-z0-9]+")


def _stem(word: str) -> str:
    if len(word) <= 4 or word.isdigit():
        return word
    for suffix, repl in (("ies", "y"), ("sses", "ss")):
        if word.endswith(suffix):
            return word[: -len(suffix)] + repl
    if word.endswith("s") and not word.endswith(("ss", "us", "is")):
        word = word[:-1]
    for suffix in ("ing", "ed"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            word = word[: -len(suffix)]
            if len(word) >= 2 and word[-1] == word[-2] and word[-1] not in "lsz":
                word = word[:-1]
            break
    if word.endswith("e") and len(word) > 4:
        word = word[:-1]
    return word


stem = _stem  # public name for other modules (trend term counting)


def tokenize(text: str | None) -> list[str]:
    if not text:
        return []
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    return [_stem(t) for t in _TOKEN.findall(text) if t not in STOPWORDS and len(t) > 1]


def bigrams(tokens: list[str]) -> list[str]:
    return [f"{a} {b}" for a, b in zip(tokens, tokens[1:])]


def expand_synonyms(text: str, synonyms: Mapping[str, list[str]]) -> str:
    """Append expansions for configured abbreviations found in ``text``."""
    words = set(_TOKEN.findall(text.lower()))
    extra = [exp for abbr, exps in synonyms.items() if abbr.lower() in words for exp in exps]
    return text + (" " + " ".join(extra) if extra else "")


def phrase_terms(phrases: Iterable[str]) -> list[list[str]]:
    """Tokenize criteria/keywords into token lists, dropping empties."""
    out = []
    for phrase in phrases:
        toks = tokenize(phrase)
        if toks:
            out.append(toks)
    return out


def phrase_matches(phrase_tokens: list[str], doc_tokens: set[str]) -> bool:
    return all(t in doc_tokens for t in phrase_tokens)


def coverage(query_tokens: list[str], doc_tokens: set[str]) -> tuple[float, list[str]]:
    """Fraction of distinct query terms present in the document, and which matched."""
    distinct = list(dict.fromkeys(query_tokens))
    if not distinct:
        return 0.0, []
    matched = [t for t in distinct if t in doc_tokens]
    return len(matched) / len(distinct), matched


class TfidfSpace:
    """TF-IDF vectors over a small document collection (the current candidate set)."""

    def __init__(self, documents: Iterable[list[str]]):
        docs = list(documents)
        self.n_docs = len(docs)
        df: Counter[str] = Counter()
        for tokens in docs:
            df.update(set(tokens))
        self.df = df

    def idf(self, term: str) -> float:
        return math.log(1 + (self.n_docs + 1) / (self.df.get(term, 0) + 0.5))

    def vector(self, tokens: list[str]) -> dict[str, float]:
        counts = Counter(tokens)
        vec = {t: (1 + math.log(c)) * self.idf(t) for t, c in counts.items()}
        norm = math.sqrt(sum(v * v for v in vec.values()))
        return {t: v / norm for t, v in vec.items()} if norm else {}

    @staticmethod
    def cosine(a: Mapping[str, float], b: Mapping[str, float]) -> float:
        if len(a) > len(b):
            a, b = b, a
        return sum(v * b.get(t, 0.0) for t, v in a.items())
