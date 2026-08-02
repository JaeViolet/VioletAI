"""Conservative explicit-memory intent handling and retrieval."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from memory_embeddings import canonical_key, embed_text, cosine_similarity
from memory_intent import CREATE, DELETE, IGNORE, RETRIEVE, UPDATE, MemoryIntentClassifier
from memory_models import MemoryRecord, ParsedMemory
from memory_store import MemoryStore, normalize_key

NO_MATCH = "NO_MATCH"
MULTIPLE_MATCHES = "MULTIPLE_MATCHES"
INVALID_REFERENCE = "INVALID_REFERENCE"
WRITE_FAILED = "WRITE_FAILED"
INVALID_MEMORY_REQUEST = "INVALID_MEMORY_REQUEST"

REMEMBER_PREFIXES = (
    "remember",
    "remember that",
    "remember this",
    "don't forget that",
    "dont forget that",
    "save this to memory",
    "add this to memory",
    "add this to your memory",
    "please remember",
    "save that",
    "save this",
    "save",
    "store that",
    "store this",
    "store",
    "note that",
    "keep in mind",
    "create a memory",
)
CONTEXTUAL_REMEMBER_COMMANDS = (
    "remember that",
    "remember this",
    "save that",
    "save this",
    "store that",
    "store this",
)
FORGET_PREFIXES = (
    "forget that",
    "forget my",
    "forget everything you know about",
    "delete the memory about",
    "delete the memory of",
    "remove the memory about",
    "remove the memory of",
    "delete",
    "remove",
    "erase",
    "clear",
)
CONTEXTUAL_FORGET_COMMANDS = (
    "forget that",
    "forget this",
    "delete that",
    "delete this",
    "remove that",
    "remove this",
    "erase that",
    "erase this",
    "clear that",
    "clear this",
)
UPDATE_PATTERNS = (
    re.compile(r"^(?:update|change|edit) my (?P<key>.+?) to (?P<value>.+)$", re.IGNORECASE),
    re.compile(r"^my (?P<key>.+?) is now (?P<value>.+)$", re.IGNORECASE),
    re.compile(r"^my (?P<key>.+?) is (?P<value>.+?) now$", re.IGNORECASE),
    re.compile(r"^(?:replace|edit) my saved (?P<key>.+?) with (?P<value>.+)$", re.IGNORECASE),
    re.compile(r"^replace my (?P<key>.+?) with (?P<value>.+)$", re.IGNORECASE),
    re.compile(
        r"^in your memory it says (?:(?:my )?(?P<key>.+?) is |)(?P<old>.+?)[;,]?\s*change it to (?P<value>.+)$",
        re.IGNORECASE,
    ),
)
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
    error_code: str | None = None
    remembered: bool = False
    updated: bool = False
    removed: bool = False
    clarification_needed: bool = False
    memories: list[MemoryRecord] | None = None


class MemoryService:
    def __init__(
        self,
        store: MemoryStore,
        max_retrieved: int = 6,
        classifier: MemoryIntentClassifier | None = None,
    ) -> None:
        self.store = store
        self.max_retrieved = max_retrieved
        self.classifier = classifier or MemoryIntentClassifier()
        self.migrate_duplicates()

    def migrate_duplicates(self) -> None:
        groups: dict[tuple[str, str, str], list[MemoryRecord]] = {}
        for record in self.store.list_memories():
            groups.setdefault((record.category, record.subject, canonical_key(record.key)), []).append(record)
        for records in groups.values():
            if len(records) <= 1:
                continue
            records.sort(key=lambda record: record.updated_at, reverse=True)
            for duplicate in records[1:]:
                self.store.archive(duplicate.id)

    def handle_explicit_intent(
        self,
        text: str,
        conversation_id: str,
        source_message_id: str | None,
        previous_user_text: str | None = None,
        memory_mode: str = "Explicit",
    ) -> MemoryActionResult:
        if memory_mode == "Off":
            return MemoryActionResult(False)
        intent = self.classifier.classify(text, previous_user_text)
        if intent.action == IGNORE:
            return MemoryActionResult(False)
        lowered = text.casefold().strip()
        if intent.action == RETRIEVE or any(pattern in lowered for pattern in MEMORY_QUERY_PATTERNS):
            memories = self.retrieve(text, mark_accessed=False, include_all_if_query=True)
            if not memories:
                return MemoryActionResult(True, "I do not have any saved memories yet.", memories=[])
            lines = ["Here is what I remember:"]
            lines.extend(f"- {memory.key}: {memory.value}" for memory in memories)
            self.store.mark_accessed([memory.id for memory in memories])
            return MemoryActionResult(True, "\n".join(lines), memories=memories)

        remember_body = self._strip_prefix(text, REMEMBER_PREFIXES)
        if remember_body is None and intent.action == CREATE:
            remember_body = text
        if remember_body is not None:
            if self._is_contextual_command(text, CONTEXTUAL_REMEMBER_COMMANDS):
                if not self._suitable_reference(previous_user_text):
                    return MemoryActionResult(
                        True,
                        "I couldn't save that because 'that' doesn't refer to a previous user statement. Try:\nRemember that I'm from Montreal.",
                        error_code=INVALID_REFERENCE,
                        clarification_needed=True,
                    )
                remember_body = previous_user_text or ""
            parsed = self.parse_memory(remember_body)
            if parsed is None:
                return MemoryActionResult(
                    True,
                    "I couldn't save that because the memory request was not specific enough.",
                    error_code=INVALID_MEMORY_REQUEST,
                    clarification_needed=True,
                )
            try:
                memory = self.remember(parsed, conversation_id, source_message_id, text)
            except Exception as error:
                return MemoryActionResult(
                    True,
                    f"I couldn't save that memory: {error}",
                    error_code=WRITE_FAILED,
                )
            return MemoryActionResult(
                True,
                f"I'll remember that {memory.key} is {memory.value}.",
                remembered=True,
                memories=[memory],
            )

        update_result = self.handle_update_intent(text, conversation_id, source_message_id)
        if update_result.handled:
            return update_result

        forget_query = self._strip_prefix(text, FORGET_PREFIXES)
        if forget_query is None and intent.action == DELETE:
            forget_query = text
        if forget_query is not None:
            forget_query = re.sub(r"\b(from|in)\s+(long[- ]term\s+)?memory\b", "", forget_query, flags=re.IGNORECASE).strip(" .")
            if self._is_contextual_command(text, CONTEXTUAL_FORGET_COMMANDS):
                if not self._suitable_reference(previous_user_text):
                    return MemoryActionResult(
                        True,
                        "I couldn't remove that because 'that' doesn't refer to a previous user statement.",
                        error_code=INVALID_REFERENCE,
                        clarification_needed=True,
                    )
                forget_query = previous_user_text or ""
            matches = self.find_forget_matches(forget_query)
            if not matches:
                return MemoryActionResult(
                    True,
                    f"I couldn't find a saved memory matching '{forget_query}'.",
                    error_code=NO_MATCH,
                )
            if len(matches) > 1 and not self._looks_specific(forget_query):
                lines = ["I found multiple matching memories. Which one should I remove?"]
                lines.extend(f"- {memory.key}: {memory.value}" for memory in matches[:5])
                return MemoryActionResult(
                    True,
                    "\n".join(lines),
                    error_code=MULTIPLE_MATCHES,
                    clarification_needed=True,
                    memories=matches,
                )
            try:
                for memory in matches:
                    self.store.archive(memory.id)
            except Exception as error:
                return MemoryActionResult(
                    True,
                    f"I couldn't remove that memory: {error}",
                    error_code=WRITE_FAILED,
                    memories=matches,
                )
            return MemoryActionResult(True, "Memory removed.", removed=True, memories=matches)

        return MemoryActionResult(False)

    def maybe_capture_automatic_memory(
        self,
        text: str,
        conversation_id: str,
        source_message_id: str | None,
        memory_mode: str,
    ) -> MemoryActionResult:
        if memory_mode not in {"Suggest", "Automatic"}:
            return MemoryActionResult(False)
        parsed = self.parse_memory(text)
        if parsed is None or parsed.confidence < 0.85:
            return MemoryActionResult(False)
        if not self._is_durable_user_memory(text, parsed):
            return MemoryActionResult(False)
        if memory_mode == "Suggest":
            return MemoryActionResult(
                True,
                "Would you like me to remember that?",
                clarification_needed=True,
                memories=[],
            )
        try:
            memory = self.remember(parsed, conversation_id, source_message_id, text)
        except Exception as error:
            return MemoryActionResult(True, f"I couldn't save that memory: {error}", error_code=WRITE_FAILED)
        return MemoryActionResult(
            True,
            f"I'll remember that {memory.key} is {memory.value}.",
            remembered=True,
            memories=[memory],
        )

    def _is_durable_user_memory(self, text: str, parsed: ParsedMemory) -> bool:
        lowered = text.casefold()
        blocked = ("search", "according to", "quote", "article", "website", "tool", "assistant said")
        if any(word in lowered for word in blocked):
            return False
        return parsed.category in {"User", "Preferences", "Projects", "People"} and parsed.subject == "user"

    def handle_update_intent(
        self,
        text: str,
        conversation_id: str,
        source_message_id: str | None,
    ) -> MemoryActionResult:
        cleaned = " ".join(text.strip().strip(".").split())
        for pattern in UPDATE_PATTERNS:
            match = pattern.match(cleaned)
            if not match:
                continue
            key = (match.groupdict().get("key") or match.groupdict().get("old") or "").strip()
            value = match.group("value").strip()
            if not key or not value:
                return MemoryActionResult(
                    True,
                    "What memory should I update?",
                    error_code=INVALID_MEMORY_REQUEST,
                    clarification_needed=True,
                )
            matches = self.find_update_matches(key)
            if not matches:
                return MemoryActionResult(
                    True,
                    f"I couldn't find a saved memory matching '{key}'.",
                    error_code=NO_MATCH,
                )
            if len(matches) > 1:
                lines = ["I found multiple matching memories. Which one should I update?"]
                lines.extend(f"- {memory.category} / {memory.key}: {memory.value}" for memory in matches[:5])
                return MemoryActionResult(
                    True,
                    "\n".join(lines),
                    error_code=MULTIPLE_MATCHES,
                    clarification_needed=True,
                    memories=matches,
                )
            existing = matches[0]
            parsed = ParsedMemory(
                category=existing.category,
                subject=existing.subject,
                key=existing.key,
                value=value,
                content=f"{existing.key} is {value}",
            )
            try:
                memory = self.remember(parsed, conversation_id, source_message_id, text)
            except Exception as error:
                return MemoryActionResult(
                    True,
                    f"I couldn't update that memory: {error}",
                    error_code=WRITE_FAILED,
                    memories=[existing],
                )
            return MemoryActionResult(
                True,
                f"Memory updated: {memory.key} is {memory.value}.",
                remembered=True,
                updated=True,
                memories=[memory],
            )
        return MemoryActionResult(False)

    def _is_contextual_command(self, text: str, commands: tuple[str, ...]) -> bool:
        cleaned = text.casefold().strip(" .!?:;")
        return cleaned in commands

    def _suitable_reference(self, previous_user_text: str | None) -> bool:
        if not previous_user_text or not previous_user_text.strip():
            return False
        previous = previous_user_text.strip()
        if self._is_contextual_command(previous, CONTEXTUAL_REMEMBER_COMMANDS + CONTEXTUAL_FORGET_COMMANDS):
            return False
        lowered = previous.casefold()
        if any(pattern in lowered for pattern in MEMORY_QUERY_PATTERNS):
            return False
        if self._strip_prefix(previous, REMEMBER_PREFIXES) is not None:
            return False
        if self._strip_prefix(previous, FORGET_PREFIXES) is not None:
            return False
        return not any(pattern.match(" ".join(previous.strip().strip(".").split())) for pattern in UPDATE_PATTERNS)

    def _strip_prefix(self, text: str, prefixes: tuple[str, ...]) -> str | None:
        normalized = text.strip()
        lowered = normalized.casefold()
        for prefix in sorted(prefixes, key=len, reverse=True):
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

        category = "Facts"
        subject = "user"
        key = "note"
        value = cleaned

        patterns = [
            (r"^my (?P<key>.+?) is (?P<value>.+)$", "User"),
            (r"^i am from (?P<value>.+)$", "User"),
            (r"^i'?m from (?P<value>.+)$", "User"),
            (r"^i am (?P<value>.+)$", "User"),
            (r"^i'?m (?P<value>.+)$", "User"),
            (r"^i live in (?P<value>.+)$", "User"),
            (r"^i like (?P<value>.+)$", "Preferences"),
            (r"^i prefer (?P<value>.+)$", "Preferences"),
            (r"^project (?P<key>.+?) is (?P<value>.+)$", "Projects"),
        ]
        for pattern, detected_category in patterns:
            match = re.match(pattern, cleaned, flags=re.IGNORECASE)
            if not match:
                continue
            category = detected_category
            groups = match.groupdict()
            if "key" in groups and groups.get("key"):
                key = groups["key"].strip()
                if category == "Projects":
                    subject = f"project {key}"
                    key = "description"
            else:
                if cleaned.casefold().startswith(("i live in", "i'm from", "im from")):
                    key = "location"
                elif cleaned.casefold().startswith("i like") or cleaned.casefold().startswith("i prefer"):
                    key = "preference"
                else:
                    key = "identity"
            value = groups["value"].strip(" .")
            break

        if key == "note":
            possessive = re.match(r"^my (?P<key>.+?) (?P<value>.+)$", cleaned, flags=re.IGNORECASE)
            if possessive:
                category = "User"
                key = possessive.group("key").strip()
                value = possessive.group("value").strip(" .")

        if not value:
            return None
        if expires_at:
            category = "Temporary"
        return ParsedMemory(
            category=category,
            subject=subject,
            key=key.strip().casefold(),
            value=value.strip(" ."),
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
        if existing is None:
            existing = self.find_semantic_duplicate(parsed)
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

    def find_semantic_duplicate(self, parsed: ParsedMemory) -> MemoryRecord | None:
        target_key = canonical_key(parsed.key)
        target_embedding = embed_text(f"{parsed.category} {parsed.subject} {parsed.key} {parsed.value}")
        best: tuple[float, MemoryRecord] | None = None
        for record in self.store.list_memories():
            if record.category != parsed.category or record.subject != parsed.subject:
                continue
            key_match = canonical_key(record.key) == target_key
            similarity = cosine_similarity(
                target_embedding,
                embed_text(f"{record.category} {record.subject} {record.key} {record.value}"),
            )
            score = (0.55 if key_match else 0.0) + similarity
            if score >= 0.82 and (best is None or score > best[0]):
                best = (score, record)
        return best[1] if best else None

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
        query_embedding = embed_text(query)
        records = self.store.search(category=category)
        scored: list[tuple[float, MemoryRecord]] = []
        for record in records:
            if record.expires_at and _parse_time(record.expires_at) <= now:
                continue
            haystack = " ".join(
                [record.normalized_key, record.subject, record.key, record.value, record.content]
            ).casefold()
            terms = set(re.findall(r"\w+", haystack))
            score = 0.0
            matched = False
            if query_terms:
                overlap = len(query_terms & terms)
                score += 4.0 * overlap
                matched = overlap > 0
                if record.key.casefold() in query.casefold():
                    score += 12.0
                    matched = True
                if canonical_key(record.key) and canonical_key(record.key) in canonical_key(query):
                    score += 10.0
                    matched = True
                if record.subject.casefold() in query.casefold():
                    score += 4.0
                    matched = True
                similarity = cosine_similarity(
                    query_embedding,
                    embed_text(f"{record.category} {record.subject} {record.key} {record.value} {record.content}"),
                )
                if similarity >= 0.18:
                    score += 14.0 * similarity
                    matched = True
                if category and record.category == category:
                    score += 2.0
            elif include_all_if_query:
                score += 1.0
                matched = True
            if matched:
                score += min(record.importance, 10) * 0.75
                score += min(record.access_count, 5) * 0.6
                score += _recency_score(record.updated_at, now)
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

    def find_update_matches(self, key: str) -> list[MemoryRecord]:
        cleaned = key.strip()
        if cleaned.casefold().startswith("my "):
            cleaned = cleaned[3:]
        direct_matches = [
            memory
            for memory in self.store.search(cleaned, include_archived=False)
            if memory.key.casefold() == cleaned.casefold()
            or cleaned.casefold() in memory.key.casefold()
        ]
        if direct_matches:
            return direct_matches
        return self.retrieve(cleaned, limit=10, mark_accessed=False)

    def _looks_specific(self, query: str) -> bool:
        return len(re.findall(r"\w+", query)) >= 3


def _parse_time(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)


def _recency_score(value: str, now: datetime) -> float:
    updated = _parse_time(value)
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=UTC)
    age_days = max((now - updated).total_seconds() / 86400, 0)
    return max(0.0, 2.0 - min(age_days / 30, 2.0))
