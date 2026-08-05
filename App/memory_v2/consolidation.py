"""Temporal-hierarchical consolidation for VioletAI Memory V2.

Inspired by TiMem's time-based memory organization, the consolidator runs in
batches and:

- merges near-duplicate durable facts (archiving older copies, never deleting),
- recomputes importance from recency and access frequency,
- archives stale facts that were never referenced and are no longer relevant,
- refuses to merge records whose values are meaningfully different (the
  favorite-color/favorite-drink class of corruption).

Consolidation is deliberately conservative: it archives instead of deleting,
never invents new values, and never merges conflicting ones.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from memory_v2.models import MemoryCategory, MemoryLayer, MemoryRecord
from memory_v2.normalize import attribute_core, canonical_text, subjects_overlap
from memory_v2.store import MemoryStore

_CATEGORY_WEIGHTS = {
    MemoryCategory.USER.value: 1.0,
    MemoryCategory.PREFERENCES.value: 1.2,
    MemoryCategory.PROJECTS.value: 1.0,
    MemoryCategory.PEOPLE.value: 1.0,
    MemoryCategory.FACTS.value: 0.9,
}


@dataclass(slots=True)
class ConsolidationConfig:
    stale_days: int = 120
    stale_max_importance: int = 4
    min_access_for_boost: int = 2
    importance_floor: int = 2
    recency_weight: float = 2.0
    access_weight: float = 0.5
    max_merges_per_run: int = 20
    max_importance_updates_per_run: int = 50
    conflict_modifier_keys: frozenset[str] = frozenset(
        {"favorite", "favourite", "fav", "preferred", "current", "old", "best", "worst"}
    )
    value_similarity_threshold: float = 0.9


@dataclass(slots=True)
class ConsolidationResult:
    merged: list[dict[str, object]] = field(default_factory=list)
    archived_stale: list[str] = field(default_factory=list)
    importance_updates: list[dict[str, object]] = field(default_factory=list)
    skipped_conflicts: list[dict[str, object]] = field(default_factory=list)
    ran_at: str = ""

    @property
    def changed(self) -> bool:
        return bool(self.merged or self.archived_stale or self.importance_updates)


class Consolidator:
    def __init__(self, store: MemoryStore, config: ConsolidationConfig | None = None) -> None:
        self.store = store
        self.config = config or ConsolidationConfig()

    def consolidate(self) -> ConsolidationResult:
        result = ConsolidationResult(ran_at=_utc_iso())
        self._merge_duplicates(result)
        self._archive_stale(result)
        self._recompute_importance(result)
        return result

    def _merge_duplicates(self, result: ConsolidationResult) -> None:
        groups = self.store.group_memories_by_canonical_key(include_archived=True)
        merged_count = 0
        for canonical, records in groups.items():
            if merged_count >= self.config.max_merges_per_run:
                break
            active = [r for r in records if r.layer == MemoryLayer.DURABLE]
            if len(active) < 2:
                continue
            active.sort(key=lambda r: r.updated_at, reverse=True)
            keeper = active[0]
            for other in active[1:]:
                if merged_count >= self.config.max_merges_per_run:
                    break
                if not self._values_compatible(keeper.value, other.value):
                    result.skipped_conflicts.append(
                        {
                            "keeper": keeper.id,
                            "other": other.id,
                            "key": keeper.key,
                            "reason": "values_conflict",
                            "keeper_value": keeper.value,
                            "other_value": other.value,
                        }
                    )
                    continue
                self.store.archive_memory(other.id)
                self.store.set_supersede(other.id, keeper.id)
                self.store.record_event(
                    "consolidation_merged",
                    MemoryLayer.DURABLE,
                    other.id,
                    detail={
                        "keeper_id": keeper.id,
                        "reason": "duplicate",
                        "key": keeper.key,
                        "value": keeper.value,
                    },
                )
                result.merged.append(
                    {
                        "keeper_id": keeper.id,
                        "archived_id": other.id,
                        "key": keeper.key,
                        "value": keeper.value,
                    }
                )
                merged_count += 1

    def _archive_stale(self, result: ConsolidationResult) -> None:
        cutoff = _utc_iso(days_ago=self.config.stale_days)
        for record in self.store.list_memories(include_archived=False):
            if record.layer != MemoryLayer.DURABLE:
                continue
            if record.importance > self.config.stale_max_importance:
                continue
            if record.access_count > 0:
                continue
            if record.updated_at >= cutoff:
                continue
            self.store.archive_memory(record.id)
            self.store.record_event(
                "consolidation_archived_stale",
                MemoryLayer.DURABLE,
                record.id,
                detail={"key": record.key, "value": record.value, "age_days": self.config.stale_days},
            )
            result.archived_stale.append(record.id)

    def _recompute_importance(self, result: ConsolidationResult) -> None:
        count = 0
        for record in self.store.list_memories(include_archived=False):
            if count >= self.config.max_importance_updates_per_run:
                break
            if record.layer != MemoryLayer.DURABLE:
                continue
            if record.access_count < self.config.min_access_for_boost:
                continue
            recency = self._recency_factor(record.last_accessed_at or record.updated_at)
            category_weight = _CATEGORY_WEIGHTS.get(record.category, 1.0)
            new_importance = round(
                record.importance * 0.85 + (self.config.recency_weight * recency + self.config.access_weight * min(record.access_count, 5)) * category_weight
            )
            new_importance = max(self.config.importance_floor, min(10, new_importance))
            if new_importance == record.importance:
                continue
            self.store.update_memory(record.id, importance=new_importance)
            self.store.record_event(
                "consolidation_importance",
                MemoryLayer.DURABLE,
                record.id,
                detail={"old": record.importance, "new": new_importance},
            )
            result.importance_updates.append(
                {"id": record.id, "key": record.key, "old": record.importance, "new": new_importance}
            )
            count += 1

    def _values_compatible(self, left: str, right: str) -> bool:
        left_n = canonical_text(left)
        right_n = canonical_text(right)
        if left_n == right_n:
            return True
        if len(left_n) >= 3 and (left_n in right_n or right_n in left_n):
            return True
        return _value_similarity(left, right) >= self.config.value_similarity_threshold

    def _recency_factor(self, timestamp: str) -> float:
        try:
            parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            days = max((datetime.now(UTC) - parsed).total_seconds() / 86400.0, 0.0)
        except ValueError:
            return 0.0
        if days <= 7:
            return 1.0
        if days <= 30:
            return 0.6
        if days <= 90:
            return 0.3
        return 0.1


def _value_similarity(left: str, right: str) -> float:
    left_tokens = set(canonical_text(left).split())
    right_tokens = set(canonical_text(right).split())
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _utc_iso(days_ago: int = 0) -> str:
    from datetime import timedelta

    return (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()
