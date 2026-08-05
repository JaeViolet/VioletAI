"""Local deterministic embeddings for VioletAI Memory V2 retrieval.

The embedder is intentionally lightweight and local-only. It uses normalized
character n-gram hashing so semantic-ish variants such as "fav colour" and
"favorite color" share useful signals without requiring an external service.
The exact same vector is produced for the same input, which keeps retrieval
deterministic and testable.
"""

from __future__ import annotations

import math
import re
from collections import Counter

from memory_v2.normalize import canonical_text

EMBEDDING_MODEL_NAME = "local-hash-ngram-v2"

_TOKEN_RE = re.compile(r"[\w']+")


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


def embed_key_value(key: str, value: str, subject: str = "", category: str = "") -> dict[str, float]:
    return embed_text(f"{category} {subject} {key} {value}")
