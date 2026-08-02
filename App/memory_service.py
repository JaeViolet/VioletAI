"""Conservative explicit-memory intent handling and retrieval."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from memory_models import MemoryRecord, ParsedMemory
from memory_store import MemoryStore, normalize_key

REMEMBER_PREFIXES = (
    "remember that",
    "remember this",
    "don't forget that",
    "dont forget that",
    "save this to memory",
    "add this to memory",
    "please remember",
)
FORGET_PREFIXES = ("forget that", "forget my", "forget everything you know about", "delete the memory about")
MEMORY_QUERY_PATTERNS = (
    "what do you remember about me",
    "what do you remember",
    "what memories do you have",
)
AMBIGUOUS_REFERENCES = re.compile(r"\b(it|this|that|they|them)\b", re.IGNORECASE)


@dataclass(slots=True)
class MemoryActionResult:
    handled: bool
    response: str = ""
    remembered: bool = False
    removed: bool = False
    clarification_needed: bool = False
    memories: list[MemoryRecord] | None = None


class MemoryService:
    def __init__(self, store: MemoryStore, max_retrieved: int = 6) -> None:
        self.store = store
        self.max_retrieved = max_retrieved

    def handle_explicit_intent(
        self,
        text: str,
        conversation_id: str,
        source_message_id: str | None,
    ) -> MemoryActionResult:
        lowered = text.casefold().strip()
        if any(pattern in lowered for pattern in MEMORY_QUERY_PATTERNS):
            memories = self.retrieve(text, mark_accessed=False, include_all_if_query=True)
            if not memories:
                return MemoryActionResult(True, "I do not have any saved memories yet.", memories=[])
            lines = ["Here is what I remember:"]
            lines.extend(f"- {memory.key}: {memory.value}" for memory in memories)
            self.store.mark_accessed([memory.id for memory in memories])
            return MemoryActionResult(True, "\n".join(lines), memories=memories)

        remember_body = self._strip_prefix(text, REMEMBER_PREFIXES)
        if remember_body is not None:
            parsed = self.parse_memory(remember_body)
            if parsed is None:
                return MemoryActionResult(
                    True,
                    "What should I remember specifically?",
                    clarification_needed=True,
                )
            memory = self.remember(parsed, conversation_id, source_message_id, text)
            return MemoryActionResult(
                True,
                f"I'll remember that {memory.key} is {memory.value}.",
                remembered=True,
                memories=[memory],
            )

        forget_query = self._strip_prefix(text, FORGET_PREFIXES)
        if forget_query is not None:
            matches = self.find_forget_matches(forget_query)
            if not matches:
                return MemoryActionResult(True, "I could not find a matching memory to remove.")
            if len(matches) > 1 and not self._looks_specific(forget_query):
                lines = ["I found multiple matching memories. Which one should I remove?"]
                lines.extend(f"- {memory.key}: {memory.value}" for memory in matches[:5])
                return MemoryActionResult(True, "\n".join(lines), memories=matches)
            for memory in matches:
                self.store.archive(memory.id)
            return MemoryActionResult(True, "Memory removed.", removed=True, memories=matches)

        return MemoryActionResult(False)

    def _strip_prefix(self, text: str, prefixes: tuple[str, ...]) -> str | None:
        normalized = text.strip()
        lowered = normalized.casefold()
        for prefix in prefixes:
            if lowered.startswith(prefix):
                body = normalized[len(prefix):].strip(" .:-")
                return body
        return None

    def parse_memory(self, body: str) -> ParsedMemory | None:
        cleaned = " ".join(body.strip().split())
        if not cleaned:
            return None
        if AMBIGUOUS_REFERENCES.fullmatch(cleaned) or cleaned.casefold() in {"i like it", "i like this", "i like that"}:
            return None

        expires_at = None
        until_match = re.search(r"\buntil tomorrow\b", cleaned, flags=re.IGNORECASE)
        if until_match:
            expires_at = (datetime.now(UTC) + timedelta(days=1)).isoformat(timespec="seconds")
            cleaned = re.sub(r"\buntil tomorrow\b", "", cleaned, flags=re.IGNORECASE).strip()

        category = "fact"
        subject = "user"
        key = "note"
        value = cleaned

        patterns = [
            (r"^my (?P<key>.+?) is (?P<value>.+)$", "profile"),
            (r"^i am (?P<value>.+)$", "profile"),
            (r"^i live in (?P<value>.+)$", "profile"),
            (r"^i like (?P<value>.+)$", "preference"),
            (r"^i prefer (?P<value>.+)$", "preference"),
            (r"^project (?P<key>.+?) is (?P<value>.+)$", "project"),
        ]
        for pattern, detected_category in patterns:
            match = re.match(pattern, cleaned, flags=re.IGNORECASE)
            if not match:
                continue
            category = detected_category
            groups = match.groupdict()
            if "key" in groups and groups.get("key"):
                key = groups["key"].strip()
                if category == "project":
                    subject = f"project {key}"
                    key = "description"
            else:
                if cleaned.casefold().startswith("i live in"):
                    key = "location"
                elif cleaned.casefold().startswith("i like") or cleaned.casefold().startswith("i prefer"):
                    key = "preference"
                else:
                    key = "identity"
            value = groups["value"].strip()
            break

        if key == "note":
            possessive = re.match(r"^my (?P<key>.+?) (?P<value>.+)$", cleaned, flags=re.IGNORECASE)
            if possessive:
                category = "profile"
                key = possessive.group("key").strip()
                value = possessive.group("value").strip()

        if not value:
            return None
        if expires_at:
            category = "temporary"
        return ParsedMemory(
            category=category,
            subject=subject,
            key=key.strip().casefold(),
            value=value.strip(),
            content=cleaned,
            expires_at=expires_at,
        )

    def remember(
        self,
        parsed: ParsedMemory,
        conversation_id: str,
        source_message_id: str | None,
        source_user_text: str,
    ) -> MemoryRecord:
        normalized = normalize_key(parsed.category, parsed.subject, parsed.key)
        existing = self.store.active_by_normalized_key(normalized)
        if existing and existing.value.casefold() == parsed.value.casefold():
            updated = self.store.update_existing_provenance(
                existing.id,
                conversation_id,
                source_message_id,
                source_user_text,
            )
            return updated or existing
        return self.store.add_memory(
            parsed,
            conversation_id,
            source_message_id,
            source_user_text,
            supersedes_memory_id=existing.id if existing else None,
        )

    def retrieve(
        self,
        query: str,
        category: str = "",
        limit: int | None = None,
        mark_accessed: bool = True,
        include_all_if_query: bool = False,
    ) -> list[MemoryRecord]:
        limit = limit or self.max_retrieved
        now = datetime.now(UTC)
        query_terms = set(re.findall(r"\w+", query.casefold()))
        records = self.store.search(category=category)
        scored: list[tuple[int, MemoryRecord]] = []
        for record in records:
            if record.expires_at and _parse_time(record.expires_at) <= now:
                continue
            haystack = " ".join(
                [record.normalized_key, record.subject, record.key, record.value, record.content]
            ).casefold()
            terms = set(re.findall(r"\w+", haystack))
            score = 0
            matched = False
            if query_terms:
                overlap = len(query_terms & terms)
                score += 4 * overlap
                matched = overlap > 0
                if record.key.casefold() in query.casefold():
                    score += 10
                    matched = True
                if record.subject.casefold() in query.casefold():
                    score += 6
                    matched = True
            elif include_all_if_query:
                score += 1
                matched = True
            if matched:
                score += min(record.importance, 10)
                score += min(record.access_count, 5)
                scored.append((score, record))
        scored.sort(key=lambda item: (item[0], item[1].updated_at), reverse=True)
        selected = [record for _score, record in scored[:limit]]
        if mark_accessed:
            self.store.mark_accessed([record.id for record in selected])
        return selected

    def find_forget_matches(self, query: str) -> list[MemoryRecord]:
        cleaned = query.strip()
        if cleaned.casefold().startswith("my "):
            cleaned = cleaned[3:]
        return self.retrieve(cleaned, limit=10, mark_accessed=False, include_all_if_query=not cleaned)

    def _looks_specific(self, query: str) -> bool:
        return len(re.findall(r"\w+", query)) >= 3


def _parse_time(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)
