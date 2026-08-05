"""Temporary cross-chat memory for VioletAI Memory V2.

Temporary memories persist across chats but are never permanent, are never
shown in the Memory Manager, and require no confirmation. They remember the
current project, open issues, recent plans, and pending tasks so the assistant
can resume naturally.

Expiry is deliberately NOT purely time-based. Each active temporary memory is
scored from token distance, conversation distance, recency, importance, reuse
frequency, and unresolved status. Hard caps on token distance and conversation
distance guarantee eventual cleanup for unused context, while a memory stays
alive while the user remains in the same conversational thread.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from memory_v2.models import MemoryLayer, TemporaryRecord
from memory_v2.store import MemoryStore


@dataclass(slots=True)
class TemporaryConfig:
    max_token_distance: int = 100_000
    max_conversation_distance: int = 15
    max_active: int = 30
    expiry_threshold: float = 2.0
    weight_recency: float = 1.0
    weight_importance: float = 0.7
    weight_conversation_distance: float = 1.2
    weight_reuse: float = 0.4
    weight_unresolved: float = 1.2
    token_penalty_scale: float = 3.0
    recency_half_life_hours: float = 168.0
    conversation_span: int = 10


class TemporaryMemory:
    def __init__(self, store: MemoryStore, config: TemporaryConfig | None = None) -> None:
        self.store = store
        self.config = config or TemporaryConfig()
        self.token_counter = 0
        self.conversation_index = 0
        self._last_conversation_id: str | None = None

    def begin_turn(self, conversation_id: str, token_count: int = 0) -> tuple[int, int]:
        self.token_counter += token_count
        if conversation_id != self._last_conversation_id:
            self.conversation_index += 1
            self._last_conversation_id = conversation_id
        return self.token_counter, self.conversation_index

    def sweep(self) -> list[tuple[TemporaryRecord, str]]:
        expired: list[tuple[TemporaryRecord, str]] = []
        for record in self.store.list_temporary(status="active"):
            reason = self._should_expire(record)
            if reason is None:
                continue
            self.store.expire_temporary(record.id)
            self.store.record_event(
                "temporary_expired",
                MemoryLayer.TEMPORARY,
                record.id,
                detail={"reason": reason, "key": record.key, "value": record.value},
            )
            expired.append((record, reason))
        self._enforce_budget()
        return expired

    def _should_expire(self, record: TemporaryRecord) -> str | None:
        token_distance = max(self.token_counter - record.token_at_last_seen, 0)
        conversation_distance = max(self.conversation_index - record.conversation_at_last_seen, 0)
        if token_distance > self.config.max_token_distance:
            return "token_distance_exceeded"
        if conversation_distance > self.config.max_conversation_distance:
            return "conversation_distance_exceeded"
        if conversation_distance == 0:
            return None
        score = self._score(record, token_distance, conversation_distance)
        if score < self.config.expiry_threshold:
            return f"decayed_score_{score:.2f}"
        return None

    def _score(
        self,
        record: TemporaryRecord,
        token_distance: int,
        conversation_distance: int,
        now: datetime | None = None,
    ) -> float:
        now = now or datetime.now(UTC)
        age_hours = _age_hours(record.updated_at, now)
        recency = self.config.weight_recency * max(0.0, 1.0 - age_hours / self.config.recency_half_life_hours)
        importance = self.config.weight_importance * (min(record.importance, 10) / 10.0)
        conversation = self.config.weight_conversation_distance * max(
            0.0, 1.0 - conversation_distance / self.config.conversation_span
        )
        reuse = self.config.weight_reuse * (min(record.access_count, 5) / 5.0)
        unresolved = self.config.weight_unresolved * record.unresolved
        token_decay = -self.config.token_penalty_scale * min(token_distance / self.config.max_token_distance, 1.0)
        return round(recency + importance + conversation + reuse + unresolved + token_decay, 3)

    def _enforce_budget(self) -> None:
        active = self.store.list_temporary(status="active")
        if len(active) <= self.config.max_active:
            return
        scored: list[tuple[float, TemporaryRecord]] = []
        for record in active:
            token_distance = max(self.token_counter - record.token_at_last_seen, 0)
            conversation_distance = max(self.conversation_index - record.conversation_at_last_seen, 0)
            scored.append((self._score(record, token_distance, conversation_distance), record))
        scored.sort(key=lambda item: (item[0], _age_hours(item[1].updated_at)))
        excess = len(scored) - self.config.max_active
        for _score_value, record in scored[:excess]:
            self.store.expire_temporary(record.id)
            self.store.record_event(
                "temporary_expired",
                MemoryLayer.TEMPORARY,
                record.id,
                detail={"reason": "budget_eviction", "key": record.key, "value": record.value},
            )

    def active_context(self) -> list[TemporaryRecord]:
        return self.store.list_temporary(status="active")

    def scores(self) -> dict[str, dict[str, float]]:
        result: dict[str, dict[str, float]] = {}
        for record in self.store.list_temporary(status="active"):
            token_distance = max(self.token_counter - record.token_at_last_seen, 0)
            conversation_distance = max(self.conversation_index - record.conversation_at_last_seen, 0)
            result[record.id] = {
                "score": self._score(record, token_distance, conversation_distance),
                "token_distance": float(token_distance),
                "conversation_distance": float(conversation_distance),
            }
        return result


def _age_hours(value: str, now: datetime | None = None) -> float:
    now = now or datetime.now(UTC)
    try:
        created = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
    except ValueError:
        return 0.0
    return max((now - created).total_seconds() / 3600.0, 0.0)
