"""Canonical text normalization for the VioletAI Memory V2 pipeline.

Normalization is the foundation for exact-match and near-duplicate detection.
All keys, subjects, and values pass through these helpers before storage or
comparison so that "Favorite Color" and "favourite colour" resolve to the same
canonical form while remaining fully deterministic and dependency-free.
"""

from __future__ import annotations

import re

SYNONYMS = {
    "fav": "favorite",
    "fave": "favorite",
    "favourite": "favorite",
    "preferred": "favorite",
    "colour": "color",
    "colours": "colors",
    "movie": "film",
    "job": "occupation",
    "work": "occupation",
    "city": "location",
    "from": "location",
}

CATEGORY_ALIASES = {
    "profile": "User",
    "user": "User",
    "preference": "Preferences",
    "preferences": "Preferences",
    "project": "Projects",
    "projects": "Projects",
    "person": "People",
    "people": "People",
    "fact": "Facts",
    "facts": "Facts",
}

_TOKEN_RE = re.compile(r"[\w']+")

_SIMILAR_WORD_FAMILIES = {
    frozenset({"color", "colour", "colors"}),
    frozenset({"movie", "film"}),
    frozenset({"job", "occupation", "work"}),
    frozenset({"city", "location"}),
}


def canonical_text(text: str) -> str:
    words = _TOKEN_RE.findall(text.casefold())
    normalized = [SYNONYMS.get(word, word) for word in words]
    return " ".join(normalized)


def canonical_key(category: str, subject: str, key: str) -> str:
    def clean(value: str) -> str:
        return " ".join(canonical_text(value.replace("_", " ")).split())

    category_raw = " ".join(_TOKEN_RE.findall(category.casefold().replace("_", " ")))
    category_name = CATEGORY_ALIASES.get(category_raw, category_raw)
    return f"{clean(category_name)}:{clean(subject)}:{clean(key)}"


def attribute_core(key: str) -> str:
    text = canonical_text(key)
    return " ".join(text.split())


def subjects_overlap(left: str, right: str) -> bool:
    if not left or not right:
        return False
    left_words = set(_TOKEN_RE.findall(left.casefold()))
    right_words = set(_TOKEN_RE.findall(right.casefold()))
    if not left_words or not right_words:
        return False
    if len(left_words & right_words) >= min(len(left_words), len(right_words)):
        return True
    for left_word in left_words:
        for right_word in right_words:
            for family in _SIMILAR_WORD_FAMILIES:
                if left_word in family and right_word in family:
                    return True
    return False


def keys_equivalent(left: str, right: str) -> bool:
    left_words = set(canonical_text(left.replace("_", " ")).split())
    right_words = set(canonical_text(right.replace("_", " ")).split())
    if not left_words or not right_words:
        return left_words == right_words
    if left_words == right_words:
        return True
    if len(left_words) != len(right_words):
        return False
    left_remaining = left_words - right_words
    right_remaining = right_words - left_words
    if len(left_remaining) != len(right_remaining):
        return False
    for left_word in left_remaining:
        matched = False
        for right_word in right_remaining:
            for family in _SIMILAR_WORD_FAMILIES:
                if left_word in family and right_word in family:
                    matched = True
                    break
            if matched:
                break
        if not matched:
            return False
    return True


def collapse_whitespace(text: str) -> str:
    return " ".join(text.split())
