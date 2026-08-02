"""Local semantic helpers for VioletAI memory retrieval.

The embedder is intentionally lightweight and local-only. It uses normalized
character n-gram hashing so semantic-ish variants such as "fav colour" and
"favorite color" share useful signals without requiring an external service.
"""

from __future__ import annotations

import math
import re
from collections import Counter

EMBEDDING_MODEL_NAME = "local-hash-ngram-v1"

SYNONYMS = {
    "fav": "favorite",
    "fave": "favorite",
    "preferred": "favorite",
    "preference": "favorite",
    "colour": "color",
    "colours": "colors",
    "movie": "film",
    "job": "occupation",
    "work": "occupation",
    "city": "location",
    "from": "location",
}


def canonical_text(text: str) -> str:
    words = re.findall(r"[\w']+", text.casefold())
    normalized = [SYNONYMS.get(word, word) for word in words]
    return " ".join(normalized)


def canonical_key(key: str) -> str:
    text = canonical_text(key)
    text = re.sub(r"\bfavorite\s+favorite\b", "favorite", text)
    return " ".join(text.split())


def embed_text(text: str) -> dict[str, float]:
    text = canonical_text(text)
    features: Counter[str] = Counter()
    words = text.split()
    for word in words:
        features[f"w:{word}"] += 2.0
        padded = f"_{word}_"
        for index in range(max(len(padded) - 2, 0)):
            features[f"c:{padded[index:index + 3]}"] += 1.0
    for index in range(max(len(words) - 1, 0)):
        features[f"b:{words[index]} {words[index + 1]}"] += 2.5
    magnitude = math.sqrt(sum(value * value for value in features.values()))
    if magnitude == 0:
        return {}
    return {key: value / magnitude for key, value in features.items()}


def cosine_similarity(left: dict[str, float], right: dict[str, float]) -> float:
    if not left or not right:
        return 0.0
    if len(left) > len(right):
        left, right = right, left
    return sum(value * right.get(key, 0.0) for key, value in left.items())
