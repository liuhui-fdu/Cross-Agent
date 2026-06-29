"""Text utilities kept separate from memory business rules."""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Dict, Iterable, List, Sequence, Set


STOPWORDS: Set[str] = {
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by", "can",
    "do", "does", "for", "from", "had", "has", "have", "i", "if", "in",
    "is", "it", "its", "me", "my", "of", "on", "or", "our", "should",
    "so", "that", "the", "their", "there", "this", "to", "was", "we",
    "were", "what", "when", "where", "which", "who", "why", "will",
    "with", "you", "your", "they", "them", "he", "she", "his", "her",
    "up", "out", "down", "about", "into", "some", "any", "then", "than",
}

TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:[.'-][A-Za-z0-9]+)*|[\u4e00-\u9fff]+")
SENTENCE_RE = re.compile(r"(?<=[.!?。！？])\s+")


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def tokenize(text: str, keep_stopwords: bool = False) -> List[str]:
    tokens: List[str] = []
    for match in TOKEN_RE.finditer(text or ""):
        token = match.group(0).lower().strip("'")
        if re.fullmatch(r"[\u4e00-\u9fff]+", token) and len(token) > 2:
            tokens.append(token)
            tokens.extend(token[index : index + 2] for index in range(len(token) - 1))
            tokens.extend(token[index : index + 3] for index in range(len(token) - 2))
        else:
            tokens.append(token)
    if keep_stopwords:
        return [t for t in tokens if t]
    return [t for t in tokens if t and t not in STOPWORDS and len(t) > 1]


def term_frequencies(text: str) -> Counter:
    return Counter(tokenize(text))


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    left, right = set(a), set(b)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def cosine_from_counts(a: Dict[str, float], b: Dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    numerator = sum(a[t] * b[t] for t in common)
    left = math.sqrt(sum(v * v for v in a.values()))
    right = math.sqrt(sum(v * v for v in b.values()))
    if left == 0 or right == 0:
        return 0.0
    return numerator / (left * right)


def split_sentences(text: str) -> List[str]:
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    if not cleaned:
        return []
    return [s.strip() for s in SENTENCE_RE.split(cleaned) if s.strip()]


def best_snippet(text: str, query: str, max_chars: int = 420) -> str:
    sentences = split_sentences(text)
    if not sentences:
        return text[:max_chars]
    q_tokens = set(tokenize(query))
    ranked = []
    for idx, sentence in enumerate(sentences):
        overlap = len(q_tokens & set(tokenize(sentence)))
        ranked.append((overlap, -idx, sentence))
    ranked.sort(reverse=True)
    snippet = ranked[0][2] if ranked else sentences[0]
    if len(snippet) <= max_chars:
        return snippet
    return snippet[: max_chars - 1].rstrip() + "..."


def distinctive_terms(text: str, limit: int) -> List[str]:
    counts = term_frequencies(text)
    scored = []
    for term, count in counts.items():
        if len(term) <= 2:
            continue
        score = count * (1.0 + min(len(term), 12) / 12.0)
        if term[0].isdigit():
            score += 0.5
        scored.append((score, term))
    scored.sort(reverse=True)
    return [term for _, term in scored[:limit]]


def expand_with_synonyms(text: str, synonyms: Dict[str, Sequence[str]]) -> List[str]:
    lowered = normalize(text)
    expanded: List[str] = []
    for phrase, values in synonyms.items():
        if phrase.lower() in lowered:
            expanded.extend(values)
    return list(dict.fromkeys(tokenize(" ".join(expanded), keep_stopwords=False)))
