"""MemorySystem facade: the single entry point for VioletAI Memory V2.

The facade wires the extractor, operations, retriever, temporary-memory
lifecycle, and consolidator into one object the application talks to.

Rules enforced here:

* memory questions are strictly read-only (they never write or delete),
* suppression statements ("don't save that") are honored invisibly,
* mutations only flow through validated operations,
* retrieval injects only when it clears the threshold or is explicitly asked,
* temporary context expires by context distance, never purely by time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from memory_v2.consolidation import ConsolidationConfig, ConsolidationResult, Consolidator
from memory_v2.extract import Extractor
from memory_v2.models import (
    MemoryLayer,
    MutationKind,
    MutationOutcome,
    MutationStatus,
    ProvenanceKind,
    RetrievalOutcome,
    TurnAnalysis,
    TurnOutcome,
)
from memory_v2.operations import OperationContext, Operations
from memory_v2.retrieval import Retriever
from memory_v2.store import MemoryStore
from memory_v2.temporary import TemporaryConfig, TemporaryMemory


@dataclass(slots=True)
class MemorySystemConfig:
    consolidation_interval: int = 25
    max_results: int = 6
    injection_threshold: float = 6.0


@dataclass(slots=True)
class MemorySystemStats:
    durable: int = 0
    archived: int = 0
    temporary_active: int = 0
    schema_version: int = 0
    recent_events: list[dict] = field(default_factory=list)


class MemorySystem:
    def __init__(
        self,
        store: MemoryStore,
        config: MemorySystemConfig | None = None,
        temporary_config: TemporaryConfig | None = None,
        consolidation_config: ConsolidationConfig | None = None,
    ) -> None:
        self.store = store
        self.config = config or MemorySystemConfig()
        self.extractor = Extractor()
        self.operations = Operations(store)
        self.retriever = Retriever(
            store,
            max_results=self.config.max_results,
            injection_threshold=self.config.injection_threshold,
        )
        self.temporary = TemporaryMemory(store, temporary_config)
        self.consolidator = Consolidator(store, consolidation_config)
        self.turns = 0

    # ------------------------------------------------------------------ core

    def handle_user_message(
        self,
        user_text: str,
        *,
        conversation_id: str | None = None,
        message_id: str | None = None,
        previous_user_text: str | None = None,
        token_count: int = 0,
    ) -> TurnOutcome:
        token_count = token_count or _estimate_tokens(user_text)
        token_counter, conversation_index = self.temporary.begin_turn(conversation_id or "default", token_count)
        self.turns += 1

        analysis = self.extractor.analyze(user_text, previous_user_text)
        context = OperationContext(
            conversation_id=conversation_id,
            message_id=message_id,
            user_text=user_text,
            previous_user_text=previous_user_text,
            token_counter=token_counter,
            conversation_index=conversation_index,
        )

        if analysis.is_question_about_memory:
            if _is_meta_memory_question(user_text):
                retrieval = self.retriever.retrieve(user_text, include_all=True, mark_accessed=True)
            else:
                retrieval = self.retriever.retrieve(user_text, include_all=False, mark_accessed=True)
            return TurnOutcome(
                memory_related=True,
                messages=[],
                retrieval=retrieval,
                diagnostics=self._diagnostics(context),
            )

        if _is_suppressed(analysis):
            self.temporary.sweep()
            self._maybe_consolidate()
            return TurnOutcome(
                memory_related=True,
                messages=[],
                notice="write suppressed by user",
                diagnostics=self._diagnostics(context),
            )

        retrieval = self.retriever.retrieve(user_text, include_all=False, mark_accessed=True)

        outcome: MutationOutcome | None = None
        if analysis.command is not None:
            outcome = self.operations.apply(analysis.command, context)
        elif analysis.durable_fact is not None:
            context.provenance_kind = ProvenanceKind.AUTOMATIC
            outcome = self.operations.create_from_parsed(analysis.durable_fact, context)
        elif analysis.temporary_fact is not None:
            context.provenance_kind = ProvenanceKind.AUTOMATIC
            outcome = self.operations.create_temporary_from_parsed(analysis.temporary_fact, context)

        self.temporary.sweep()
        self._maybe_consolidate()

        return TurnOutcome(
            memory_related=analysis.memory_related,
            messages=[],
            action=outcome.kind if outcome else None,
            action_status=outcome.status if outcome else None,
            clarification_needed=bool(outcome and outcome.status == MutationStatus.MULTIPLE_MATCHES),
            retrieval=retrieval,
            diagnostics=self._diagnostics(context),
            extra={"analysis_reason": analysis.reason},
        )

    def retrieve(self, query: str, *, include_all: bool = False, mark_accessed: bool = True) -> RetrievalOutcome:
        return self.retriever.retrieve(query, include_all=include_all, mark_accessed=mark_accessed)

    # ------------------------------------------------------------ lifecycle

    def run_sweep(self) -> list[tuple[object, str]]:
        return self.temporary.sweep()

    def consolidate_now(self) -> ConsolidationResult:
        return self.consolidator.consolidate()

    def _maybe_consolidate(self) -> None:
        if self.config.consolidation_interval > 0 and self.turns % self.config.consolidation_interval == 0:
            self.consolidator.consolidate()

    def touch_temporary(self, memory_id: str) -> None:
        record = self.store.get_temporary(memory_id)
        if record is not None:
            self.store.touch_temporary(memory_id, self.temporary.token_counter, self.temporary.conversation_index)

    # -------------------------------------------------------- manager APIs

    def list_memories(self, include_archived: bool = False):
        return self.store.list_memories(include_archived=include_archived)

    def search_memories(self, query: str, **kwargs):
        return self.store.search_memories(query, **kwargs)

    def get_memory(self, memory_id: str):
        return self.store.get_memory(memory_id)

    def archive(self, memory_id: str, actor: str = "user") -> MutationOutcome:
        return self.operations.archive_by_id(memory_id, actor=actor)

    def restore(self, memory_id: str, actor: str = "user") -> MutationOutcome:
        return self.operations.restore_by_id(memory_id, actor=actor)

    def delete(self, memory_id: str, actor: str = "user") -> MutationOutcome:
        return self.operations.delete_by_id(memory_id, actor=actor)

    def clear_durable(self, actor: str = "user") -> MutationOutcome:
        return self.operations.clear_durable(actor=actor)

    # ------------------------------------------------------------- helpers

    def stats(self) -> MemorySystemStats:
        return MemorySystemStats(
            durable=len(self.store.list_memories(include_archived=False)),
            archived=len(self.store.list_memories(include_archived=True)) - len(self.store.list_memories(include_archived=False)),
            temporary_active=len(self.temporary.active_context()),
            schema_version=self.store.schema_version(),
            recent_events=self.store.recent_events(limit=20),
        )

    def _diagnostics(self, context: OperationContext) -> dict:
        return {
            "token_counter": context.token_counter,
            "conversation_index": context.conversation_index,
            "turns": self.turns,
        }


def _estimate_tokens(text: str) -> int:
    return max(1, round(len(text) / 4))


def _is_suppressed(analysis: TurnAnalysis) -> bool:
    return analysis.memory_related and not analysis.is_question_about_memory and analysis.command is None and analysis.durable_fact is None and analysis.temporary_fact is None and analysis.reason == "write suppressed by user"


_META_QUESTION_PATTERNS = (
    re.compile(r"memory\s+system", re.IGNORECASE),
    re.compile(r"^what\s+do\s+you\s+remember", re.IGNORECASE),
    re.compile(r"^what\s+memor", re.IGNORECASE),
    re.compile(r"^how\s+many\s+memor", re.IGNORECASE),
    re.compile(r"^(?:show|list)\b", re.IGNORECASE),
    re.compile(r"^(?:can\s+you\s+see)", re.IGNORECASE),
    re.compile(r"memory\s+(?:enabled|active|on)", re.IGNORECASE),
    re.compile(r"^do\s+you\s+remember\s+me\b", re.IGNORECASE),
)


def _is_meta_memory_question(text: str) -> bool:
    return any(pattern.search(text) for pattern in _META_QUESTION_PATTERNS)
