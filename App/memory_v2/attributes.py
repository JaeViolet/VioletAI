"""Structured attribute references for VioletAI Memory V2.

Memory V2's precision guarantee lives here. A user phrase is resolved into a
structured reference (subject + canonical attribute identity) and stored keys
are reduced to the same identity space, so generic modifiers never bridge
unrelated attributes:

* "what is my job" resolves to the same identity as stored "occupation",
* "my favorite color" never matches "my favorite drink" or "my hair color",
* "work phone" and "personal phone" are distinct identities,
* "Forget Alice's birthday" is scoped to Alice and only Alice.

The module is deterministic and dependency-free on purpose.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from memory_v2.normalize import SYNONYMS, canonical_text

GENERIC_MODIFIERS = frozenset({
    "favorite",
    "favourite",
    "fav",
    "fave",
    "preferred",
    "current",
    "old",
    "new",
    "main",
    "primary",
    "best",
    "top",
    "personal",
})

_ATTRIBUTE_PHRASE_ALIASES = {
    "job": "occupation",
    "jobs": "occupation",
    "profession": "occupation",
    "professions": "occupation",
    "occupation": "occupation",
    "work": "occupation",
    "work role": "occupation",
    "job title": "occupation",
    "career": "occupation",
    "role": "occupation",
    "living": "occupation",
    "phone": "phone number",
    "phone number": "phone number",
    "telephone": "phone number",
    "telephone number": "phone number",
    "mobile": "phone number",
    "mobile number": "phone number",
    "mobile phone": "phone number",
    "cell": "phone number",
    "cellphone": "phone number",
    "cell phone": "phone number",
    "cell number": "phone number",
    "personal phone": "phone number",
    "personal phone number": "phone number",
    "work phone": "work phone number",
    "work phone number": "work phone number",
    "business phone": "work phone number",
    "home phone": "home phone number",
    "home phone number": "home phone number",
    "address": "home address",
    "home address": "home address",
    "residence": "home address",
    "residential address": "home address",
    "house": "home address",
    "home": "home address",
    "work address": "work address",
    "workplace": "work address",
    "work place": "work address",
    "email": "email",
    "email address": "email",
    "personal email": "email",
    "work email": "work email",
    "business email": "work email",
    "birthday": "birthday",
    "birthdays": "birthday",
    "birthdate": "birthday",
    "date of birth": "birthday",
    "dob": "birthday",
    "color": "color",
    "colour": "color",
    "colors": "color",
    "colours": "color",
    "favorite color": "color",
    "favourite colour": "color",
    "favorite drink": "drink",
    "favorite movie": "film",
    "favorite film": "film",
    "favorite song": "song",
    "favorite food": "food",
    "favorite game": "game",
    "favorite band": "band",
    "favorite book": "book",
    "favorite sport": "sport",
    "favorite city": "location",
    "favorite tv show": "tv show",
    "favorite show": "tv show",
    "city": "location",
    "cities": "location",
    "hometown": "location",
    "location": "location",
    "project": "project",
    "projects": "project",
    "current project": "project",
    "activity": "activity",
    "current activity": "activity",
    "task": "task",
    "current task": "task",
    "plan": "plan",
    "current plan": "plan",
    "issue": "issue",
    "name": "name",
    "pet": "pet name",
    "pet name": "pet name",
    "device": "device",
    "handedness": "handedness",
    "learning": "learning",
    "interest": "interest",
    "hobby": "hobby",
    "preference": "preference",
}

_WORD_ALIASES = {
    "colors": "color",
    "colours": "color",
    "drinks": "drink",
    "movies": "film",
    "films": "film",
    "songs": "song",
    "books": "book",
    "games": "game",
    "cities": "location",
    "phones": "phone number",
    "telephones": "phone number",
    "mobiles": "phone number",
    "cells": "phone number",
    "birthdates": "birthday",
    "hometowns": "location",
    "hometown": "location",
    "residences": "home address",
    "houses": "home address",
    "emails": "email",
    "pet names": "pet name",
}

_SPECIAL_PHRASES = {
    "what do you do": "occupation",
    "what do you do for work": "occupation",
    "what do you do for a living": "occupation",
    "where do you work": "occupation",
    "what is your occupation": "occupation",
    "what is your profession": "occupation",
    "where do you live": "location",
    "where do i live": "location",
    "where am i from": "location",
}

_TOKEN_RE = re.compile(r"[\w']+")

_POSSESSIVE_RE = re.compile(
    r"^(?:my|your|our|his|her|its|their|the user'?s)\s+(?P<attr>.+)$",
    re.IGNORECASE,
)

_NAME_POSSESSIVE_RE = re.compile(
    r"^(?P<name>[A-Za-z][\w-]*)(?:'s|')\s+(?P<attr>.+)$",
    re.IGNORECASE,
)

_QUESTION_WRAPPERS = (
    re.compile(r"^(?:what|when|where|who)'s\s+", re.IGNORECASE),
    re.compile(
        r"^(?:what|when|where|who|why|how)\s+(?:is|are|was|were|do|does|did|am|will)\s+",
        re.IGNORECASE,
    ),
    re.compile(r"^(?:can you tell me|do you know|tell me)\s+", re.IGNORECASE),
)

_NAME_BLOCKLIST = frozenset({
    "my", "your", "our", "his", "her", "its", "their", "the",
    "a", "an", "it", "this", "that", "these", "those", "they",
    "we", "you", "i", "me", "us", "them",
})

_USER_SUBJECTS = frozenset({"user", "me", "i", "my", "you", "your", "our", "ours"})

_POSSESSIVE_PRONOUNS = frozenset({
    "my", "your", "our", "his", "her", "its", "their", "the", "a", "an",
})


@dataclass(frozen=True, slots=True)
class AttributeReference:
    subject: str
    phrase: str
    identity: str
    is_generic_only: bool
    explicit_subject: bool
    raw: str


def attribute_identity(phrase: str) -> str:
    """Canonical identity of an attribute phrase (aliases applied, generic
    modifiers stripped). Returns an empty string when nothing remains."""
    text = _collapse(phrase)
    if not text:
        return ""
    lowered = text.casefold()
    if lowered in _ATTRIBUTE_PHRASE_ALIASES:
        return _ATTRIBUTE_PHRASE_ALIASES[lowered]
    words = canonical_text(text).split()
    words = [word for word in words if word not in GENERIC_MODIFIERS and word not in _POSSESSIVE_PRONOUNS]
    if not words:
        return ""
    identity = " ".join(words)
    if identity in _WORD_ALIASES:
        return _WORD_ALIASES[identity]
    return identity


def parse_reference(text: str) -> AttributeReference | None:
    cleaned = _collapse(text)
    if not cleaned:
        return None
    special = cleaned.strip(" .!?").casefold()
    if special in _SPECIAL_PHRASES:
        identity = _SPECIAL_PHRASES[special]
        return AttributeReference(
            subject="user",
            phrase=identity,
            identity=identity,
            is_generic_only=False,
            explicit_subject=True,
            raw=cleaned,
        )
    body = cleaned
    for wrapper in _QUESTION_WRAPPERS:
        match = wrapper.match(cleaned)
        if match:
            body = cleaned[match.end():].lstrip(" ,;:").strip(" ?!.")
            break
    if not body:
        return None
    possessive = _POSSESSIVE_RE.match(body)
    if possessive:
        subject = "user"
        phrase = _collapse(possessive.group("attr"))
        explicit = True
    else:
        named = _NAME_POSSESSIVE_RE.match(body)
        if named and named.group("name").casefold() not in _NAME_BLOCKLIST:
            subject = _collapse(named.group("name"))
            phrase = _collapse(named.group("attr"))
            explicit = True
        else:
            subject = "user"
            phrase = body
            explicit = False
    if not phrase:
        return None
    identity = attribute_identity(phrase)
    words = set(_TOKEN_RE.findall(phrase.casefold()))
    normalized = {SYNONYMS.get(word, word) for word in words}
    generic_only = bool(words) and (words <= GENERIC_MODIFIERS or normalized <= GENERIC_MODIFIERS)
    return AttributeReference(
        subject=_collapse(subject),
        phrase=phrase,
        identity=identity,
        is_generic_only=generic_only,
        explicit_subject=explicit,
        raw=cleaned,
    )


def references_record(reference: AttributeReference | None, record_key: str) -> bool:
    if reference is None or reference.is_generic_only or not reference.identity:
        return False
    return attribute_identity(record_key) == reference.identity


def subject_matches(left: str, right: str) -> bool:
    left = _collapse(left or "").casefold()
    right = _collapse(right or "").casefold()
    if not left or not right:
        return False
    if left == right:
        return True
    return left in _USER_SUBJECTS and right in _USER_SUBJECTS


def is_user_subject(subject: str) -> bool:
    return _collapse(subject or "").casefold() in _USER_SUBJECTS


def _collapse(text: str) -> str:
    return " ".join(str(text or "").split())
