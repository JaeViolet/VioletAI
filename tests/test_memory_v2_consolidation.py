"""Tests for temporal-hierarchical consolidation."""

import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from memory_v2.consolidation import ConsolidationConfig, Consolidator  # noqa: E402
from memory_v2.models import MemoryLayer, ParsedMemory, Provenance  # noqa: E402
from memory_v2.store import MemoryStore  # noqa: E402


def make_parsed(**overrides: object) -> ParsedMemory:
    defaults: dict[str, object] = {
        "category": "Preferences",
        "subject": "user",
        "key": "favorite color",
        "value": "violet",
        "content": "favorite color is violet",
        "importance": 5,
        "confidence": 0.95,
        "provenance": Provenance(conversation_id="c1", message_id="m1", user_text="remember favorite color is violet"),
    }
    defaults.update(overrides)
    return ParsedMemory(**defaults)  # type: ignore[arg-type]


def _set_age(store: MemoryStore, memory_id: str, days_old: int) -> None:
    stamp = (datetime.now(UTC) - timedelta(days=days_old)).isoformat()
    connection = sqlite3.connect(store.path)
    try:
        connection.execute("UPDATE v2_memories SET created_at=?, updated_at=? WHERE id=?", (stamp, stamp, memory_id))
        connection.commit()
    finally:
        connection.close()


def _events(store: MemoryStore, kind: str) -> list[dict]:
    return [e for e in store.recent_events(limit=500) if e["event"] == kind]


class ConsolidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "memory.db"
        self.store = MemoryStore(self.path)
        self.consolidator = Consolidator(self.store)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_merge_duplicates_archives_older(self) -> None:
        older = self.store.insert_memory(make_parsed(value="violet"))
        newer = self.store.insert_memory(make_parsed(value="violet"))
        result = self.consolidator.consolidate()
        self.assertEqual(len(result.merged), 1)
        self.assertEqual(result.merged[0]["keeper_id"], newer.id)
        self.assertEqual(result.merged[0]["archived_id"], older.id)
        self.assertEqual(self.store.get_memory(older.id).layer, MemoryLayer.ARCHIVED)
        self.assertEqual(self.store.get_memory(newer.id).layer, MemoryLayer.DURABLE)
        self.assertEqual(self.store.get_memory(older.id).superseded_by_id, newer.id)
        self.assertTrue(result.changed)

    def test_never_merges_conflicting_values(self) -> None:
        first = self.store.insert_memory(make_parsed(value="violet"))
        second = self.store.insert_memory(make_parsed(value="indigo"))
        result = self.consolidator.consolidate()
        self.assertEqual(result.merged, [])
        self.assertEqual(len(result.skipped_conflicts), 1)
        self.assertEqual(self.store.get_memory(first.id).layer, MemoryLayer.DURABLE)
        self.assertEqual(self.store.get_memory(second.id).layer, MemoryLayer.DURABLE)

    def test_favorite_color_and_favorite_drink_never_merge(self) -> None:
        color = self.store.insert_memory(make_parsed(key="favorite color", value="violet"))
        drink = self.store.insert_memory(make_parsed(key="favorite drink", value="coffee"))
        result = self.consolidator.consolidate()
        self.assertEqual(result.merged, [])
        self.assertEqual(result.skipped_conflicts, [])
        self.assertEqual(self.store.get_memory(color.id).layer, MemoryLayer.DURABLE)
        self.assertEqual(self.store.get_memory(drink.id).layer, MemoryLayer.DURABLE)

    def test_merges_when_value_contained(self) -> None:
        shorter = self.store.insert_memory(make_parsed(value="violet"))
        longer = self.store.insert_memory(make_parsed(value="violet blue"))
        result = self.consolidator.consolidate()
        self.assertEqual(len(result.merged), 1)
        self.assertEqual(result.merged[0]["keeper_id"], longer.id)

    def test_archive_stale_unaccessed_low_importance(self) -> None:
        stale = self.store.insert_memory(make_parsed(key="old note", value="some old detail", importance=2))
        fresh = self.store.insert_memory(make_parsed(key="note", value="recent detail", importance=2))
        _set_age(self.store, stale.id, days_old=200)
        result = self.consolidator.consolidate()
        self.assertIn(stale.id, result.archived_stale)
        self.assertNotIn(fresh.id, result.archived_stale)
        self.assertEqual(self.store.get_memory(stale.id).layer, MemoryLayer.ARCHIVED)
        self.assertEqual(self.store.get_memory(fresh.id).layer, MemoryLayer.DURABLE)
        self.assertEqual(len(_events(self.store, "consolidation_archived_stale")), 1)

    def test_accessed_or_important_stale_is_kept(self) -> None:
        accessed = self.store.insert_memory(make_parsed(key="old note", value="used detail", importance=2))
        important = self.store.insert_memory(make_parsed(key="old note 2", value="big detail", importance=9))
        _set_age(self.store, accessed.id, days_old=200)
        _set_age(self.store, important.id, days_old=200)
        self.store.mark_accessed([accessed.id])
        result = self.consolidator.consolidate()
        self.assertNotIn(accessed.id, result.archived_stale)
        self.assertNotIn(important.id, result.archived_stale)

    def test_importance_recompute_boost(self) -> None:
        record = self.store.insert_memory(make_parsed(value="violet", importance=3))
        for _ in range(4):
            self.store.mark_accessed([record.id])
        result = self.consolidator.consolidate()
        updated = next((u for u in result.importance_updates if u["id"] == record.id), None)
        self.assertIsNotNone(updated)
        self.assertGreater(updated["new"], updated["old"])
        stored = self.store.get_memory(record.id)
        self.assertEqual(stored.importance, updated["new"])
        self.assertLessEqual(stored.importance, 10)
        self.assertGreaterEqual(stored.importance, self.consolidator.config.importance_floor)

    def test_importance_never_drops_below_floor(self) -> None:
        record = self.store.insert_memory(make_parsed(value="violet", importance=1))
        for _ in range(10):
            self.store.mark_accessed([record.id])
        self.consolidator.consolidate()
        stored = self.store.get_memory(record.id)
        self.assertGreaterEqual(stored.importance, self.consolidator.config.importance_floor)

    def test_underused_fact_importance_ignored(self) -> None:
        record = self.store.insert_memory(make_parsed(value="violet", importance=3))
        result = self.consolidator.consolidate()
        self.assertNotIn(record.id, [u["id"] for u in result.importance_updates])

    def test_empty_store_result_unchanged(self) -> None:
        result = self.consolidator.consolidate()
        self.assertFalse(result.changed)
        self.assertEqual(result.merged, [])


if __name__ == "__main__":
    unittest.main()
