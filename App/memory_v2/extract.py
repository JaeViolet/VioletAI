"""Deterministic memory intent and fact extraction for VioletAI Memory V2.

The extractor decides whether a user message touches memory and, if so, what
kind of typed operation is requested. It is deliberately conservative:

* questions about memory are read-only and can never trigger writes or deletes
* negative imperatives ("don't save this") suppress writes
* "forget/delete that" without a resolvable reference yields an invalid
  reference instead of guessing
* personal facts are only extracted from first-person statements, never from
  assistant output, research, or quoted content
"""

from __future__ import annotations

import re

from memory_v2.models import MemoryCommand, MutationKind, ParsedMemory, Provenance, TurnAnalysis
from memory_v2.normalize import canonical_text

_TRAILING_EMOTICON = re.compile(
    r"\s*(?:[:;=8xX][-']?[)(DPpOo/\\]|[🙂😊😉😄😃😂🤣😅🥲]+)\s*$"
)

_CONTEXTUAL_CREATE = {"remember that", "remember this", "save that", "save this", "store that", "store this", "keep that in mind", "remember it", "save it", "store it"}
_CONTEXTUAL_DELETE = {"forget that", "forget this", "delete that", "delete this", "remove that", "remove this", "erase that", "erase this", "clear that", "clear this", "forget it", "delete it", "remove it", "erase it", "clear it", "forget about it"}

_QUESTION_PATTERNS = (
    re.compile(r"^(how|tell me how)\s+(does|do)\s+(your\s+)?(memory|the memory system)\s+work", re.IGNORECASE),
    re.compile(r"^how\s+(do|does)\s+(you|your memory)\s+(work|remember)", re.IGNORECASE),
    re.compile(r"^what\s+do\s+you\s+remember", re.IGNORECASE),
    re.compile(r"^what\s+memor(?:y|ies)\s+(?:do you\s+|have|do i have)", re.IGNORECASE),
    re.compile(r"^do\s+you\s+remember\s+me\b", re.IGNORECASE),
    re.compile(r"^(?:do you\s+)?(?:still\s+)?(?:remember|know)\s+(?:my|what)\b", re.IGNORECASE),
    re.compile(r"^what(?:'s| is)\s+my\b", re.IGNORECASE),
    re.compile(r"^(?:tell me|what do you know)\s+(?:about\s+)?(?:me|my)\b", re.IGNORECASE),
    re.compile(r"^what\s+have\s+you\s+(?:saved|remembered)\b", re.IGNORECASE),
    re.compile(r"^(?:can\s+)?you\s+see\s+(?:my\s+)?memor", re.IGNORECASE),
    re.compile(r"^(?:is|does)\s+(?:there\s+a\s+)?(?:memory\s+system|memory)\s+(?:enabled|active|on)", re.IGNORECASE),
    re.compile(r"^what\s+(?:is|are)\s+(?:the\s+)?(?:saved\s+)?memor", re.IGNORECASE),
    re.compile(r"^(?:show|list)\s+(?:me\s+)?(?:my\s+)?memor", re.IGNORECASE),
    re.compile(r"^how\s+many\s+memor", re.IGNORECASE),
)

_SUPPRESSION_PATTERNS = (
    re.compile(r"^(?:please\s+)?(?:don'?t|do not)\s+(?:save|store|remember|keep|add|record)\b", re.IGNORECASE),
    re.compile(r"^(?:please\s+)?(?:don'?t|do not)\s+add\s+this\s+to\b", re.IGNORECASE),
)

_CREATE_WRAPPER = re.compile(
    r"^(?:(?:can|could|would|will)\s+you\s+)?(?:please\s+)?"
    r"(?P<command>remember that|remember this|remember|don'?t forget that|dont forget that|"
    r"save that|save this|save|store that|store this|store|note that|keep in mind|"
    r"create a memory|add this to(?: your)? memory|put this in(?: your)? memory)"
    r"[:\s]*(?P<body>.*)$",
    re.IGNORECASE,
)

_TEMPORARY_TASK = re.compile(r"^(?:remember to|don'?t forget to|remind me to)\s+(?P<task>.+)$", re.IGNORECASE)

_UPDATES = (
    re.compile(r"^change it to (?P<value>.+)$", re.IGNORECASE),
    re.compile(r"^the color i like most is (?P<value>.+?) now$", re.IGNORECASE),
    re.compile(r"^(?:update|change|edit) my (?P<key>.+?) to (?P<value>.+)$", re.IGNORECASE),
    re.compile(r"^replace my (?P<key>.+?) with (?P<value>.+)$", re.IGNORECASE),
    re.compile(r"^replace (?:the )?(?P<key>.+?) you have saved with (?P<value>.+)$", re.IGNORECASE),
    re.compile(r"^my (?P<key>.+?) is now (?P<value>.+)$", re.IGNORECASE),
    re.compile(r"^my (?P<key>.+?) is (?P<value>.+?) now$", re.IGNORECASE),
    re.compile(
        r"^in your memory it says (?:(?:my )?(?P<key>.+?) is |)(?P<old>.+?)[;,]?\s*change it to (?P<value>.+)$",
        re.IGNORECASE,
    ),
    re.compile(r"^update (?:your )?memory about (?P<key>.+?) to (?P<value>.+)$", re.IGNORECASE),
)

_FACT_PATTERNS = (
    ("name", r"^my name is (?P<value>.+)$", "User"),
    ("favorite color", r"^the color i like most is (?P<value>.+)$", "Preferences"),
    ("favorite color", r"^my favorite color is (?P<value>.+)$", "Preferences"),
    ("favorite color", r"^my favourite colour is (?P<value>.+)$", "Preferences"),
    ("favorite drink", r"^my favorite drink is (?P<value>.+)$", "Preferences"),
    ("favorite movie", r"^my favorite movie is (?P<value>.+)$", "Preferences"),
    ("favorite movie", r"^my favourite movie is (?P<value>.+)$", "Preferences"),
    ("favorite <attr>", r"^my favorite (?P<key>.+?) is (?P<value>.+)$", "Preferences"),
    ("favourite <attr>", r"^my favourite (?P<key>.+?) is (?P<value>.+)$", "Preferences"),
    ("<attr>", r"^my (?P<key>.+?) is (?P<value>.+)$", "User"),
    ("device", r"^i have an? (?P<value>iphone|android phone|phone|ipad|tablet|macbook|laptop|desktop|pc|computer)$", "User"),
    ("device", r"^i own an? (?P<value>iphone|android phone|phone|ipad|tablet|macbook|laptop|desktop|pc|computer)$", "User"),
    ("location", r"^i live in (?P<value>.+)$", "User"),
    ("location", r"^i am from (?P<value>.+)$", "User"),
    ("location", r"^i'?m from (?P<value>.+)$", "User"),
    ("handedness", r"^i(?: am|'?m) (?P<value>left[- ]handed)$", "User"),
    ("occupation", r"^i work (?:as|at) (?P<value>.+)$", "User"),
    ("identity", r"^i am an? (?P<value>.+)$", "User"),
    ("identity", r"^i'?m an? (?P<value>.+)$", "User"),
    ("learning", r"^i(?: am|'?m) learning (?P<value>.+)$", "User"),
    ("interest", r"^i play (?P<value>.+?) regularly$", "Preferences"),
    ("preference", r"^i prefer (?P<value>.+)$", "Preferences"),
    ("interest", r"^i love (?P<value>.+)$", "Preferences"),
    ("preference", r"^i like (?P<value>.+)$", "Preferences"),
    ("project", r"^i(?: am|'?m) building (?P<value>.+)$", "Projects"),
    ("project description", r"^project (?P<key>.+?) is (?P<value>.+)$", "Projects"),
)

_TEMPORARY_PATTERNS = (
    ("current project", r"^i(?: am|'?m) (?:currently |now |right now )?working on (?P<value>.+)$", True),
    ("current project", r"^i(?: am|'?m) working on (?P<value>.+?) (?:right now|at the moment|these days)$", True),
    ("current activity", r"^i(?: am|'?m) playing (?P<value>.+)$", False),
    ("unresolved", r"^i(?: am|'?m) (?:stuck on|having trouble with|struggling with) (?P<value>.+)$", True),
    ("unresolved", r"^there is a bug in (?P<value>.+)$", True),
    ("unresolved", r"^there'?s a bug in (?P<value>.+)$", True),
    ("plan", r"^i(?: am|'?m) going to (?P<value>.+)$", False),
    ("plan", r"^i(?:'ll| will) be (?:back|away) (?P<value>.*)$", False),
)

_DEVICE_NORMALIZATION = {
    "iphone": "iPhone",
    "ipad": "iPad",
    "macbook": "MacBook",
    "pc": "PC",
}

_DELETE_PREFIXES = (
    re.compile(r"^forget\s+(?P<body>.*)$", re.IGNORECASE),
    re.compile(r"^delete\s+(?P<body>.*)$", re.IGNORECASE),
    re.compile(r"^remove\s+(?P<body>.*)$", re.IGNORECASE),
    re.compile(r"^erase\s+(?P<body>.*)$", re.IGNORECASE),
    re.compile(r"^clear\s+(?P<body>.*)$", re.IGNORECASE),
)

_MEMORY_OF = re.compile(r"\b(?:the\s+)?memory\s+(?:about|of|on)\b", re.IGNORECASE)
_MEMORY_FROM = re.compile(r"\b(?:from|in)\s+(?:long[- ]term\s+)?memory\b", re.IGNORECASE)
_MY_PREFIX = re.compile(r"^my\s+", re.IGNORECASE)


def clean_value(value: str) -> str:
    cleaned = " ".join(str(value or "").strip().split())
    while True:
        previous = cleaned
        cleaned = _TRAILING_EMOTICON.sub("", cleaned).strip()
        cleaned = cleaned.rstrip(" \t\r\n.!?;:")
        if cleaned == previous:
            break
    return cleaned


def clean_text(text: str) -> str:
    return clean_value(text)


class Extractor:
    def analyze(self, text: str, previous_user_text: str | None = None) -> TurnAnalysis:
        cleaned = clean_text(text)
        if not cleaned:
            return TurnAnalysis(reason="empty message", raw=text)
        lowered = cleaned.casefold()

        if _is_suppression(lowered):
            return TurnAnalysis(memory_related=True, reason="write suppressed by user", raw=text)

        if _looks_like_memory_question(lowered):
            return TurnAnalysis(
                memory_related=True,
                is_question_about_memory=True,
                reason="question about memory - read only",
                raw=text,
            )

        command = self._parse_create_command(cleaned, lowered)
        if command is not None:
            return TurnAnalysis(
                memory_related=True,
                command=command,
                reason=command.reason,
                raw=text,
            )

        command = self._parse_update_command(cleaned, lowered)
        if command is not None:
            return TurnAnalysis(
                memory_related=True,
                command=command,
                reason=command.reason,
                raw=text,
            )

        command = self._parse_delete_command(cleaned, lowered)
        if command is not None:
            return TurnAnalysis(
                memory_related=True,
                command=command,
                reason=command.reason,
                raw=text,
            )

        fact = self._parse_durable_fact(cleaned)
        if fact is not None:
            return TurnAnalysis(
                memory_related=True,
                durable_fact=fact,
                reason="first-person durable fact",
                raw=text,
            )

        temporary = self._parse_temporary_fact(cleaned)
        if temporary is not None:
            return TurnAnalysis(
                memory_related=True,
                temporary_fact=temporary,
                reason="temporary cross-chat context",
                raw=text,
            )

        return TurnAnalysis(memory_related=False, reason="not memory related", raw=text)

    def _parse_create_command(self, cleaned: str, lowered: str) -> MemoryCommand | None:
        temporary_match = _TEMPORARY_TASK.match(cleaned)
        if temporary_match:
            task = clean_value(temporary_match.group("task"))
            if task:
                return MemoryCommand(
                    kind=MutationKind.TEMPORARY_CREATE,
                    confidence=0.95,
                    key="task",
                    value=task,
                    subject="user",
                    raw=cleaned,
                    reason="temporary task command",
                )
        if lowered.strip(" .!?") in _CONTEXTUAL_CREATE:
            return MemoryCommand(
                kind=MutationKind.CREATE,
                confidence=0.9,
                reference_previous=True,
                raw=cleaned,
                reason="contextual create command",
            )
        wrapper = _CREATE_WRAPPER.match(cleaned)
        if wrapper:
            body = clean_value(wrapper.group("body"))
            if not body:
                return MemoryCommand(
                    kind=MutationKind.CREATE,
                    confidence=0.85,
                    reference_previous=True,
                    raw=cleaned,
                    reason="create command without inline body",
                )
            fact = self._parse_durable_fact(body)
            if fact is not None:
                return MemoryCommand(
                    kind=MutationKind.CREATE,
                    confidence=0.95,
                    key=fact.key,
                    value=fact.value,
                    subject=fact.subject,
                    category=fact.category,
                    raw=cleaned,
                    reason="explicit create command with parsed fact",
                )
            return MemoryCommand(
                kind=MutationKind.CREATE,
                confidence=0.9,
                key="note",
                value=body,
                subject="user",
                category="Facts",
                raw=cleaned,
                reason="explicit create command with free-form body",
            )
        return None

    def _parse_update_command(self, cleaned: str, lowered: str) -> MemoryCommand | None:
        normalized = " ".join(cleaned.strip(" .").split())
        for pattern in _UPDATES:
            match = pattern.match(normalized)
            if not match:
                continue
            groups = match.groupdict()
            key = (groups.get("key") or groups.get("old") or "").strip()
            if not key and "color i like most" in normalized.casefold():
                key = "favorite color"
            value = clean_value(groups.get("value", ""))
            if not value:
                continue
            return MemoryCommand(
                kind=MutationKind.UPDATE,
                confidence=0.9,
                key=key,
                value=value,
                subject="user",
                reference_previous=not key,
                raw=cleaned,
                reason="explicit update command",
            )
        return None

    def _parse_delete_command(self, cleaned: str, lowered: str) -> MemoryCommand | None:
        stripped = lowered.strip(" .!?")
        if stripped in _CONTEXTUAL_DELETE:
            return MemoryCommand(
                kind=MutationKind.DELETE,
                confidence=0.9,
                reference_previous=True,
                raw=cleaned,
                reason="contextual delete command",
            )
        for prefix in _DELETE_PREFIXES:
            match = prefix.match(cleaned)
            if not match:
                continue
            body = clean_value(match.group("body"))
            if not body:
                continue
            if body.casefold().strip(" .!?") in {"it", "that", "this"}:
                return MemoryCommand(
                    kind=MutationKind.DELETE,
                    confidence=0.9,
                    reference_previous=True,
                    raw=cleaned,
                    reason="contextual delete command",
                )
            key = _clean_delete_body(body)
            if not key:
                continue
            return MemoryCommand(
                kind=MutationKind.DELETE,
                confidence=0.9,
                key=key,
                subject="user",
                raw=cleaned,
                reason="explicit delete command",
            )
        return None

    def _parse_durable_fact(self, cleaned: str) -> ParsedMemory | None:
        match_text = cleaned.strip(" .!?")
        for key_hint, pattern, category in _FACT_PATTERNS:
            match = re.match(pattern, match_text, flags=re.IGNORECASE)
            if not match:
                continue
            groups = match.groupdict()
            raw_value = groups.get("value") or ""
            if not raw_value:
                continue
            key = key_hint
            subject = "user"
            if key_hint in {"favorite <attr>", "favourite <attr>", "<attr>"}:
                key = clean_value(groups.get("key") or "")
                if not key:
                    continue
                key = _normalize_attribute_key(key)
            if key_hint == "device":
                key = "device"
                raw_value = _normalize_device(raw_value)
            if key_hint == "project description":
                key = "description"
                subject = f"project {clean_value(groups.get('key') or '')}"
            value = clean_value(raw_value)
            if not value or len(value) > 160:
                continue
            return ParsedMemory(
                category=category,
                subject=subject,
                key=key,
                value=value,
                content=cleaned,
                confidence=0.9,
            )
        return None

    def _parse_temporary_fact(self, cleaned: str) -> ParsedMemory | None:
        match_text = cleaned.strip(" .!?")
        for key_hint, pattern, unresolved in _TEMPORARY_PATTERNS:
            match = re.match(pattern, match_text, flags=re.IGNORECASE)
            if not match:
                continue
            value = clean_value(match.group("value"))
            if not value:
                continue
            if key_hint == "current project":
                key = "current project"
                subject = "user"
                importance = 6
            elif key_hint == "plan":
                key = "plan"
                subject = "user"
                importance = 4
            elif key_hint == "current activity":
                key = "current activity"
                subject = "user"
                importance = 3
            else:
                key = "issue"
                subject = "user"
                importance = 6
            return ParsedMemory(
                category="Temporary",
                subject=subject,
                key=key,
                value=value,
                content=cleaned,
                confidence=0.9,
                importance=importance,
                unresolved=unresolved,
                extra={"temporary_kind": key_hint},
            )
        return None


def _is_suppression(lowered: str) -> bool:
    return any(pattern.match(lowered) for pattern in _SUPPRESSION_PATTERNS)


def _looks_like_memory_question(lowered: str) -> bool:
    return any(pattern.match(lowered) for pattern in _QUESTION_PATTERNS)


def _clean_delete_body(body: str) -> str:
    cleaned = _MEMORY_OF.sub("", body)
    cleaned = _MEMORY_FROM.sub("", cleaned)
    cleaned = _MY_PREFIX.sub("", cleaned, count=1)
    cleaned = re.sub(r"\b(?:about|of|on|regarding)\b", "", cleaned, flags=re.IGNORECASE)
    return clean_value(cleaned)


def _normalize_attribute_key(key: str) -> str:
    text = canonical_text(key)
    text = re.sub(r"\bfavorite\s+favorite\b", "favorite", text)
    return " ".join(text.split())


def _normalize_device(value: str) -> str:
    cleaned = clean_value(value).casefold()
    return _DEVICE_NORMALIZATION.get(cleaned, clean_value(value))
