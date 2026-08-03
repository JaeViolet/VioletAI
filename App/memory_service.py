"""Conservative explicit-memory intent handling and retrieval."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import perf_counter

from memory_diagnostics import MemoryDiagnostics
from memory_embeddings import canonical_key, embed_text, cosine_similarity
from memory_intent import CREATE, DELETE, NONE, RETRIEVE, UPDATE, MemoryAnalysis, MemoryIntentClassifier
from memory_models import MemoryRecord, ParsedMemory
from memory_store import MemoryStore, normalize_key

NO_MATCH = "NO_MATCH"
MULTIPLE_MATCHES = "MULTIPLE_MATCHES"
INVALID_REFERENCE = "INVALID_REFERENCE"
WRITE_FAILED = "WRITE_FAILED"
INVALID_MEMORY_REQUEST = "INVALID_MEMORY_REQUEST"
SUCCESS = "SUCCESS"
RETRIEVAL_EMPTY = "RETRIEVAL_EMPTY"
DISABLED_BY_MODE = "DISABLED_BY_MODE"

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
    re.compile(r"^change it to (?P<value>.+)$", re.IGNORECASE),
    re.compile(r"^the color i like most is (?P<value>.+?) now$", re.IGNORECASE),
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
TRAILING_EMOTICON = re.compile(r"\s*(?:[:;=8xX][-']?[)(DPpOo/\\]|[🙂😊😉😄😃😂🤣😅🥲]+)\s*$")


@dataclass(slots=True)
class MemoryActionResult:
    handled: bool
    response: str = ""
    status: str | None = None
    action: str = NONE
    error_code: str | None = None
    memory_id: str | None = None
    canonical_key: str = ""
    previous_value: str = ""
    new_value: str = ""
    affected_records: list[str] | None = None
    failure_reason: str = ""
    confirmation: str = ""
    analysis: MemoryAnalysis | None = None
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
        return self.process_user_message(text, conversation_id, source_message_id, previous_user_text, memory_mode)

    def process_user_message(
        self,
        text: str,
        conversation_id: str,
        source_message_id: str | None,
        previous_user_text: str | None = None,
        memory_mode: str = "Explicit",
        diagnostics_enabled: bool = False,
        diagnostics: MemoryDiagnostics | None = None,
    ) -> MemoryActionResult:
        diagnostics = diagnostics or MemoryDiagnostics(diagnostics_enabled)
        diagnostics.record(user_message=text, memory_mode=memory_mode)
        if memory_mode == "Off":
            result = MemoryActionResult(
                False,
                status=DISABLED_BY_MODE,
                action=NONE,
                failure_reason="Long-term memory is disabled by mode.",
            )
            diagnostics.record(
                memory_related=False,
                action=NONE,
                validation_result=DISABLED_BY_MODE,
                structured_result=DISABLED_BY_MODE,
                ui_confirmation="SKIPPED",
                assistant_response="Normal chat",
            )
            diagnostics.emit()
            return result
        analysis_started = perf_counter()
        analysis = self.classifier.analyze(text, previous_user_text)
        diagnostics.record_elapsed("analysis_ms", analysis_started)
        diagnostics.record(
            memory_related="YES" if analysis.memory_related else "NO",
            action=analysis.action,
            confidence=analysis.confidence,
            diagnostic_reasoning=analysis.diagnostic_reasoning,
            subject=analysis.subject,
            canonical_key=analysis.canonical_key,
            value=analysis.value,
            referenced_previous_user_message=analysis.referenced_previous_user_message,
        )
        if not analysis.memory_related:
            automatic = self._automatic_from_unrelated_message(
                text,
                conversation_id,
                source_message_id,
                memory_mode,
                analysis,
                diagnostics,
            )
            if automatic is not None:
                return automatic
            result = MemoryActionResult(False, status=None, action=NONE, analysis=analysis)
            diagnostics.record(
                validation_result="NOT_MEMORY_RELATED",
                operation_executed="Normal Chat",
                structured_result="NORMAL_CHAT",
                ui_confirmation="SKIPPED",
                assistant_response="Normal chat",
            )
            diagnostics.emit()
            return result
        lowered = text.casefold().strip()
        if analysis.action == RETRIEVE or any(pattern in lowered for pattern in MEMORY_QUERY_PATTERNS):
            retrieve_started = perf_counter()
            memories = self.retrieve(text, mark_accessed=False, include_all_if_query=True)
            diagnostics.record_elapsed("retrieve_ms", retrieve_started)
            if not memories:
                result = MemoryActionResult(
                    True,
                    "I do not have any saved memories yet.",
                    status=RETRIEVAL_EMPTY,
                    action=RETRIEVE,
                    error_code=RETRIEVAL_EMPTY,
                    failure_reason="No active memories matched retrieval request.",
                    analysis=analysis,
                    memories=[],
                )
                diagnostics.record(
                    candidate_memories=[],
                    validation_result=RETRIEVAL_EMPTY,
                    operation_executed=RETRIEVE,
                    database_result=RETRIEVAL_EMPTY,
                    structured_result=RETRIEVAL_EMPTY,
                    failed_stage="Candidate Retrieval",
                    failure_reason=result.failure_reason,
                    ui_confirmation="SKIPPED",
                    assistant_response=result.response,
                )
                diagnostics.emit()
                return result
            lines = ["Here is what I remember:"]
            lines.extend(f"- {memory.key}: {memory.value}" for memory in memories)
            self.store.mark_accessed([memory.id for memory in memories])
            result = MemoryActionResult(
                True,
                "\n".join(lines),
                status=SUCCESS,
                action=RETRIEVE,
                affected_records=[memory.id for memory in memories],
                analysis=analysis,
                memories=memories,
            )
            diagnostics.record(
                candidate_memories=[f"{memory.key}={memory.value}" for memory in memories],
                selected_memory=memories[0].id if memories else "",
                validation_result="VALID",
                operation_executed=RETRIEVE,
                database_result=SUCCESS,
                structured_result=SUCCESS,
                ui_confirmation="SKIPPED",
                assistant_response=result.response,
            )
            diagnostics.emit()
            return result

        remember_body = self._strip_prefix(text, REMEMBER_PREFIXES)
        if remember_body is None and analysis.action == CREATE:
            remember_body = text
        if remember_body is not None:
            if self._is_contextual_command(text, CONTEXTUAL_REMEMBER_COMMANDS):
                if not self._suitable_reference(previous_user_text):
                    result = MemoryActionResult(
                        True,
                        "I couldn't save that because 'that' doesn't refer to a previous user statement. Try:\nRemember that I'm from Montreal.",
                        status=INVALID_REFERENCE,
                        action=CREATE,
                        error_code=INVALID_REFERENCE,
                        failure_reason="'that' did not resolve to a previous user statement.",
                        analysis=analysis,
                        clarification_needed=True,
                    )
                    self._emit_failure_diagnostics(diagnostics, result, "Context Resolution")
                    return result
                remember_body = previous_user_text or ""
            parsed = (
                ParsedMemory(
                    "User",
                    "user",
                    analysis.canonical_key,
                    clean_memory_value(analysis.value),
                    f"{analysis.canonical_key} is {clean_memory_value(analysis.value)}",
                )
                if analysis.canonical_key and analysis.value and remember_body == text
                else self.parse_memory(remember_body)
            )
            if parsed is None:
                result = MemoryActionResult(
                    True,
                    "I couldn't save that because the memory request was not specific enough.",
                    status=INVALID_MEMORY_REQUEST,
                    action=CREATE,
                    error_code=INVALID_MEMORY_REQUEST,
                    failure_reason="Memory parser could not extract a durable memory.",
                    analysis=analysis,
                    clarification_needed=True,
                )
                self._emit_failure_diagnostics(diagnostics, result, "Validation")
                return result
            try:
                execute_started = perf_counter()
                memory = self.remember(parsed, conversation_id, source_message_id, text)
                diagnostics.record_elapsed("execute_ms", execute_started)
            except Exception as error:
                diagnostics.record_elapsed("execute_ms", execute_started)
                result = MemoryActionResult(
                    True,
                    f"I couldn't save that memory: {error}",
                    status=WRITE_FAILED,
                    action=CREATE,
                    error_code=WRITE_FAILED,
                    failure_reason=str(error),
                    analysis=analysis,
                )
                self._emit_failure_diagnostics(diagnostics, result, "Database Execution")
                return result
            result = MemoryActionResult(
                True,
                f"I'll remember that {memory.key} is {memory.value}.",
                status=SUCCESS,
                action=CREATE,
                memory_id=memory.id,
                canonical_key=canonical_key(memory.key),
                new_value=memory.value,
                affected_records=[memory.id],
                confirmation="✓ Remembered",
                analysis=analysis,
                remembered=True,
                memories=[memory],
            )
            self._emit_success_diagnostics(diagnostics, result, memory, CREATE)
            return result

        execute_started = perf_counter()
        update_result = self.handle_update_intent(text, conversation_id, source_message_id)
        if update_result.handled:
            diagnostics.record_elapsed("execute_ms", execute_started)
        if update_result.handled:
            update_result.analysis = analysis
            self._emit_result_diagnostics(diagnostics, update_result)
            return update_result

        forget_query = self._strip_prefix(text, FORGET_PREFIXES)
        if forget_query is None and analysis.action == DELETE:
            forget_query = text
        if forget_query is not None:
            forget_query = re.sub(r"\b(from|in)\s+(long[- ]term\s+)?memory\b", "", forget_query, flags=re.IGNORECASE).strip(" .")
            if self._is_contextual_command(text, CONTEXTUAL_FORGET_COMMANDS):
                if not self._suitable_reference(previous_user_text):
                    result = MemoryActionResult(
                        True,
                        "I couldn't remove that because 'that' doesn't refer to a previous user statement.",
                        status=INVALID_REFERENCE,
                        action=DELETE,
                        error_code=INVALID_REFERENCE,
                        failure_reason="'that' did not resolve to a previous user statement.",
                        analysis=analysis,
                        clarification_needed=True,
                    )
                    self._emit_failure_diagnostics(diagnostics, result, "Context Resolution")
                    return result
                forget_query = previous_user_text or ""
            retrieve_started = perf_counter()
            matches = self.find_forget_matches(forget_query)
            diagnostics.record_elapsed("retrieve_ms", retrieve_started)
            if not matches:
                result = MemoryActionResult(
                    True,
                    f"I couldn't find a saved memory matching '{forget_query}'.",
                    status=NO_MATCH,
                    action=DELETE,
                    error_code=NO_MATCH,
                    canonical_key=canonical_key(forget_query),
                    failure_reason="No active memory matched delete request.",
                    analysis=analysis,
                )
                self._emit_failure_diagnostics(diagnostics, result, "Candidate Retrieval")
                return result
            if len(matches) > 1 and not self._looks_specific(forget_query):
                lines = ["I found multiple matching memories. Which one should I remove?"]
                lines.extend(f"- {memory.key}: {memory.value}" for memory in matches[:5])
                result = MemoryActionResult(
                    True,
                    "\n".join(lines),
                    status=MULTIPLE_MATCHES,
                    action=DELETE,
                    error_code=MULTIPLE_MATCHES,
                    affected_records=[memory.id for memory in matches],
                    failure_reason="Multiple candidate memories matched delete request.",
                    analysis=analysis,
                    clarification_needed=True,
                    memories=matches,
                )
                self._emit_failure_diagnostics(diagnostics, result, "Validation")
                return result
            try:
                execute_started = perf_counter()
                for memory in matches:
                    self.store.archive(memory.id)
                diagnostics.record_elapsed("execute_ms", execute_started)
            except Exception as error:
                diagnostics.record_elapsed("execute_ms", execute_started)
                result = MemoryActionResult(
                    True,
                    f"I couldn't remove that memory: {error}",
                    status=WRITE_FAILED,
                    action=DELETE,
                    error_code=WRITE_FAILED,
                    affected_records=[memory.id for memory in matches],
                    failure_reason=str(error),
                    analysis=analysis,
                    memories=matches,
                )
                self._emit_failure_diagnostics(diagnostics, result, "Database Execution")
                return result
            result = MemoryActionResult(
                True,
                "Memory removed.",
                status=SUCCESS,
                action=DELETE,
                affected_records=[memory.id for memory in matches],
                confirmation="Memory removed.",
                analysis=analysis,
                removed=True,
                memories=matches,
            )
            self._emit_result_diagnostics(diagnostics, result)
            return result

        result = MemoryActionResult(False, status=None, action=NONE, analysis=analysis)
        diagnostics.record(
            validation_result="NO_EXECUTABLE_MEMORY_OPERATION",
            operation_executed="Normal Chat",
            structured_result="NORMAL_CHAT",
            ui_confirmation="SKIPPED",
            assistant_response="Normal chat",
        )
        diagnostics.emit()
        return result

    def maybe_capture_automatic_memory(
        self,
        text: str,
        conversation_id: str,
        source_message_id: str | None,
        memory_mode: str,
    ) -> MemoryActionResult:
        return self.process_user_message(text, conversation_id, source_message_id, memory_mode=memory_mode)

    def _automatic_from_unrelated_message(
        self,
        text: str,
        conversation_id: str,
        source_message_id: str | None,
        memory_mode: str,
        analysis: MemoryAnalysis,
        diagnostics: MemoryDiagnostics,
    ) -> MemoryActionResult | None:
        if memory_mode not in {"Suggest", "Automatic"}:
            return None
        parsed = self.parse_memory(text)
        if parsed is None or parsed.confidence < 0.85:
            return None
        if not self._is_durable_user_memory(text, parsed):
            return None
        if memory_mode == "Suggest":
            result = MemoryActionResult(
                True,
                "Would you like me to remember that?",
                status=SUCCESS,
                action=CREATE,
                confirmation="SKIPPED",
                analysis=analysis,
                clarification_needed=True,
                memories=[],
            )
            self._emit_result_diagnostics(diagnostics, result)
            return result
        try:
            execute_started = perf_counter()
            memory = self.remember(parsed, conversation_id, source_message_id, text)
            diagnostics.record_elapsed("execute_ms", execute_started)
        except Exception as error:
            diagnostics.record_elapsed("execute_ms", execute_started)
            result = MemoryActionResult(
                True,
                f"I couldn't save that memory: {error}",
                status=WRITE_FAILED,
                action=CREATE,
                error_code=WRITE_FAILED,
                failure_reason=str(error),
                analysis=analysis,
            )
            self._emit_failure_diagnostics(diagnostics, result, "Database Execution")
            return result
        result = MemoryActionResult(
            True,
            f"I'll remember that {memory.key} is {memory.value}.",
            status=SUCCESS,
            action=CREATE,
            memory_id=memory.id,
            canonical_key=canonical_key(memory.key),
            new_value=memory.value,
            affected_records=[memory.id],
            confirmation="✓ Remembered",
            analysis=analysis,
            remembered=True,
            memories=[memory],
        )
        self._emit_success_diagnostics(diagnostics, result, memory, CREATE)
        return result

    def _is_durable_user_memory(self, text: str, parsed: ParsedMemory) -> bool:
        lowered = text.casefold()
        blocked = ("search", "according to", "quote", "article", "website", "tool", "assistant said")
        if any(word in lowered for word in blocked):
            return False
        return parsed.category in {"User", "Preferences", "Projects", "People"} and parsed.subject == "user"

    def _emit_success_diagnostics(
        self,
        diagnostics: MemoryDiagnostics,
        result: MemoryActionResult,
        memory: MemoryRecord,
        operation: str,
    ) -> None:
        diagnostics.record(
            candidate_memories=[f"{record.key}={record.value}" for record in self.retrieve(memory.key, mark_accessed=False)],
            selected_memory=memory.id,
            validation_result="VALID",
            operation_executed=operation,
            database_result=SUCCESS,
            structured_result=result.status,
            ui_confirmation=result.confirmation or "SKIPPED",
            assistant_response=result.response,
            selected_memory_value=memory.value,
        )
        diagnostics.emit()

    def _emit_failure_diagnostics(
        self,
        diagnostics: MemoryDiagnostics,
        result: MemoryActionResult,
        failed_stage: str,
    ) -> None:
        diagnostics.record(
            candidate_memories=[f"{memory.key}={memory.value}" for memory in (result.memories or [])],
            ranking_scores="see hybrid retrieval scores in candidate order",
            selected_memory=result.memory_id or "",
            validation_result=result.status or result.error_code,
            operation_executed=result.action,
            database_result=result.status,
            structured_result=result.status or result.error_code,
            failed_stage=failed_stage,
            failure_reason=result.failure_reason or result.response,
            ui_confirmation="SKIPPED",
            assistant_response=result.response,
        )
        diagnostics.emit()

    def _emit_result_diagnostics(self, diagnostics: MemoryDiagnostics, result: MemoryActionResult) -> None:
        if result.status == SUCCESS:
            diagnostics.record(
                candidate_memories=[f"{memory.key}={memory.value}" for memory in (result.memories or [])],
                ranking_scores="see hybrid retrieval scores in candidate order",
                selected_memory=result.memory_id or (result.affected_records or [""])[-1],
                validation_result="VALID",
                operation_executed=result.action,
                previous_value=result.previous_value,
                new_value=result.new_value,
                database_result=SUCCESS,
                structured_result=SUCCESS,
                ui_confirmation=result.confirmation or "SKIPPED",
                assistant_response=result.response,
            )
        else:
            diagnostics.record(
                candidate_memories=[f"{memory.key}={memory.value}" for memory in (result.memories or [])],
                ranking_scores="see hybrid retrieval scores in candidate order",
                selected_memory=result.memory_id or "",
                validation_result=result.status or result.error_code,
                operation_executed=result.action,
                database_result=result.status,
                structured_result=result.status or result.error_code,
                failed_stage="Validation" if result.status == MULTIPLE_MATCHES else "Memory Pipeline",
                failure_reason=result.failure_reason or result.response,
                ui_confirmation="SKIPPED",
                assistant_response=result.response,
            )
        diagnostics.emit()

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
            if not key and "color i like most" in cleaned.casefold():
                key = "favorite color"
            if not key:
                key = "it"
            value = clean_memory_value(match.group("value"))
            if not key or not value:
                return MemoryActionResult(
                    True,
                    "What memory should I update?",
                    status=INVALID_MEMORY_REQUEST,
                    action=UPDATE,
                    error_code=INVALID_MEMORY_REQUEST,
                    failure_reason="Missing update key or value.",
                    clarification_needed=True,
                )
            matches = self.find_update_matches(key)
            if not matches:
                return MemoryActionResult(
                    True,
                    f"I couldn't find a saved memory matching '{key}'.",
                    status=NO_MATCH,
                    action=UPDATE,
                    error_code=NO_MATCH,
                    canonical_key=canonical_key(key),
                    failure_reason="No active memory matched update request.",
                )
            if len(matches) > 1:
                lines = ["I found multiple matching memories. Which one should I update?"]
                lines.extend(f"- {memory.category} / {memory.key}: {memory.value}" for memory in matches[:5])
                return MemoryActionResult(
                    True,
                    "\n".join(lines),
                    status=MULTIPLE_MATCHES,
                    action=UPDATE,
                    error_code=MULTIPLE_MATCHES,
                    affected_records=[memory.id for memory in matches],
                    failure_reason="Multiple candidate memories matched update request.",
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
                    status=WRITE_FAILED,
                    action=UPDATE,
                    error_code=WRITE_FAILED,
                    canonical_key=canonical_key(existing.key),
                    previous_value=existing.value,
                    new_value=value,
                    affected_records=[existing.id],
                    failure_reason=str(error),
                    memories=[existing],
                )
            return MemoryActionResult(
                True,
                f"Memory updated: {memory.key} is {memory.value}.",
                status=SUCCESS,
                action=UPDATE,
                memory_id=memory.id,
                canonical_key=canonical_key(memory.key),
                previous_value=existing.value,
                new_value=memory.value,
                affected_records=[existing.id, memory.id],
                confirmation="Memory updated.",
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
                if cleaned.casefold().startswith(("i live in", "i am from", "i'm from", "im from")):
                    key = "location"
                elif cleaned.casefold().startswith("i like") or cleaned.casefold().startswith("i prefer"):
                    key = "preference"
                else:
                    key = "identity"
            value = clean_memory_value(groups["value"])
            break

        if key == "note":
            possessive = re.match(r"^my (?P<key>.+?) (?P<value>.+)$", cleaned, flags=re.IGNORECASE)
            if possessive:
                category = "User"
                key = possessive.group("key").strip()
                value = clean_memory_value(possessive.group("value"))

        if not value:
            return None
        if expires_at:
            category = "Temporary"
        return ParsedMemory(
            category=category,
            subject=subject,
            key=key.strip().casefold(),
            value=clean_memory_value(value),
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
        if cleaned.casefold() in {"it", "this", "that"}:
            active = self.store.list_memories()
            return active if len(active) <= 1 else active[:10]
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


def clean_memory_value(value: str) -> str:
    cleaned = " ".join(str(value or "").strip().split())
    while True:
        previous = cleaned
        cleaned = TRAILING_EMOTICON.sub("", cleaned).strip()
        cleaned = cleaned.rstrip(" \t\r\n.!?;:")
        if cleaned == previous:
            break
    return cleaned
