"""Tests for the Memory V2 SQLite store."""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from memory_v2.models import MemoryLayer, ParsedMemory, Provenance, ProvenanceKind  # noqa: E402
from memory_v2.store import SCHEMA_VERSION, MemoryStore, utc_now  # noqa: E402


def make_parsed(**overrides: object) -> ParsedMemory:
    defaults: dict[str, object] = {
        "category": "Preferences",
        "subject": "user",
        "key": "favorite color",
        "value": "violet",
        "content": "favorite color is violet",
        "importance": 5,
        "confidence": 0.9,
        "provenance": Provenance(conversation_id="c1", message_id="m1", user_text="my favorite color is violet"),
    }
    defaults.update(overrides)
    return ParsedMemory(**defaults)  # type: ignore[arg-type]


class MemoryStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "memory.db"
        self.store = MemoryStore(self.path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_schema_version(self) -> None:
        self.assertEqual(self.store.schema_version(), SCHEMA_VERSION)
        self.assertEqual(SCHEMA_VERSION, 2)

    def test_insert_and_get_memory(self) -> None:
        record = self.store.insert_memory(make_parsed())
        self.assertIsNotNone(record)
        self.assertEqual(record.category, "Preferences")
        self.assertEqual(record.canonical_key, "preferences:user:favorite color")
        self.assertEqual(record.layer, MemoryLayer.DURABLE)
        fetched = self.store.get_memory(record.id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.value, "violet")

    def test_active_by_canonical_key(self) -> None:
        first = self.store.insert_memory(make_parsed(value="violet"))
        second = self.store.insert_memory(make_parsed(value="indigo"))
        found = self.store.active_by_canonical_key("preferences:user:favorite color")
        self.assertIsNotNone(found)
        self.assertEqual(found.id, second.id)
        self.assertNotEqual(first.id, second.id)

    def test_update_memory_recomputes_canonical(self) -> None:
        record = self.store.insert_memory(make_parsed())
        updated = self.store.update_memory(record.id, value="indigo", key="favorite colour")
        self.assertIsNotNone(updated)
        self.assertEqual(updated.value, "indigo")
        self.assertEqual(updated.canonical_key, "preferences:user:favorite color")
        self.assertEqual(updated.edit_count, 1)

    def test_update_missing_memory_returns_none(self) -> None:
        self.assertIsNone(self.store.update_memory("missing", value="x"))

    def test_archive_restore(self) -> None:
        record = self.store.insert_memory(make_parsed())
        archived = self.store.archive_memory(record.id)
        self.assertIsNotNone(archived)
        self.assertEqual(archived.layer, MemoryLayer.ARCHIVED)
        self.assertTrue(self.store.get_memory(record.id).active is False)
        self.assertEqual(self.store.list_memories(), [])
        self.assertEqual(len(self.store.list_memories(include_archived=True)), 1)
        restored = self.store.restore_memory(record.id)
        self.assertEqual(restored.layer, MemoryLayer.DURABLE)
        self.assertEqual(len(self.store.list_memories()), 1)

    def test_delete_memory(self) -> None:
        record = self.store.insert_memory(make_parsed())
        self.assertTrue(self.store.delete_memory(record.id))
        self.assertFalse(self.store.delete_memory(record.id))
        self.assertIsNone(self.store.get_memory(record.id))

    def test_search_memories(self) -> None:
        self.store.insert_memory(make_parsed(key="favorite color", value="violet"))
        self.store.insert_memory(
            make_parsed(
                category="User",
                key="name",
                value="Ada",
                content="name is Ada",
                provenance=Provenance(conversation_id="c2", message_id="m2", user_text="my name is Ada"),
            )
        )
        self.assertEqual(len(self.store.search_memories(query="violet")), 1)
        self.assertEqual(len(self.store.search_memories(query="Ada")), 1)
        self.assertEqual(len(self.store.search_memories(query="missing")), 0)
        self.assertEqual(len(self.store.search_memories(category="User")), 1)

    def test_mark_accessed(self) -> None:
        record = self.store.insert_memory(make_parsed())
        self.store.mark_accessed([record.id])
        fetched = self.store.get_memory(record.id)
        self.assertEqual(fetched.access_count, 1)
        self.assertIsNotNone(fetched.last_accessed_at)

    def test_clear_durable(self) -> None:
        self.store.insert_memory(make_parsed())
        self.store.clear_durable()
        self.assertEqual(self.store.list_memories(), [])

    def test_supersede_links(self) -> None:
        old = self.store.insert_memory(make_parsed(value="red"))
        new = self.store.insert_memory(make_parsed(value="blue"))
        self.store.set_supersede(old.id, new.id)
        fetched_old = self.store.get_memory(old.id)
        fetched_new = self.store.get_memory(new.id)
        self.assertEqual(fetched_old.superseded_by_id, new.id)
        self.assertEqual(fetched_new.supersedes_id, old.id)

    def test_events_recorded(self) -> None:
        record = self.store.insert_memory(make_parsed())
        self.store.record_event(
            "created", MemoryLayer.DURABLE, record.id,
            source_conversation_id="c1", detail={"key": "favorite color"},
        )
        events = self.store.recent_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "created")
        self.assertEqual(events[0]["detail"]["key"], "favorite color")

    def test_conversation_tracker(self) -> None:
        self.store.touch_conversation("c1", token_count=10)
        self.store.touch_conversation("c1", token_count=5)
        activity = self.store.conversation_activity()
        self.assertEqual(activity["c1"], 2)


class TemporaryStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "memory.db"
        self.store = MemoryStore(self.path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_insert_and_get(self) -> None:
        record = self.store.insert_temporary(
            make_parsed(category="Temporary", key="bug", value="sidebar flickers", unresolved=True),
            token_counter=100,
            conversation_index=2,
        )
        self.assertIsNotNone(record)
        self.assertEqual(record.token_at_created, 100)
        self.assertEqual(record.conversation_at_created, 2)
        self.assertEqual(record.unresolved, 1)
        self.assertEqual(record.status, "active")

    def test_active_by_canonical_key(self) -> None:
        self.store.insert_temporary(make_parsed(category="Temporary", key="bug", value="a"))
        self.store.insert_temporary(make_parsed(category="Temporary", key="bug", value="b"))
        found = self.store.active_temporary_by_canonical_key("temporary:user:bug")
        self.assertIsNotNone(found)
        self.assertEqual(found.value, "b")

    def test_touch_and_update(self) -> None:
        record = self.store.insert_temporary(make_parsed(category="Temporary", key="bug", value="a"))
        self.store.touch_temporary(record.id, token_counter=250, conversation_index=5)
        fetched = self.store.get_temporary(record.id)
        self.assertEqual(fetched.access_count, 1)
        self.assertEqual(fetched.token_at_last_seen, 250)
        self.assertEqual(fetched.conversation_at_last_seen, 5)
        updated = self.store.update_temporary(record.id, value="b")
        self.assertEqual(updated.value, "b")

    def test_expire_and_delete(self) -> None:
        record = self.store.insert_temporary(make_parsed(category="Temporary", key="bug", value="a"))
        self.assertEqual(len(self.store.list_temporary()), 1)
        expired = self.store.expire_temporary(record.id)
        self.assertEqual(expired.status, "expired")
        self.assertEqual(len(self.store.list_temporary(status="expired")), 1)
        self.assertEqual(len(self.store.list_temporary(status="active")), 0)
        self.assertTrue(self.store.delete_temporary(record.id))
        self.assertEqual(len(self.store.list_temporary()), 0)

    def test_clear_temporary(self) -> None:
        self.store.insert_temporary(make_parsed(category="Temporary", key="bug", value="a"))
        self.store.clear_temporary()
        self.assertEqual(len(self.store.list_temporary()), 0)

    def test_group_memories_by_canonical_key(self) -> None:
        self.store.insert_memory(make_parsed(key="favorite color", value="violet"))
        self.store.insert_memory(make_parsed(key="favorite color", value="indigo"))
        self.store.insert_memory(make_parsed(category="User", key="name", value="Ada"))
        groups = self.store.group_memories_by_canonical_key()
        self.assertEqual(len(groups["preferences:user:favorite color"]), 2)
        self.assertEqual(len(groups["user:user:name"]), 1)


class LegacyMigrationTests(unittest.TestCase):
    def test_migration_from_legacy_v1(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        try:
            path = Path(temp_dir.name) / "memory.db"
            connection = sqlite3.connect(path)
            connection.execute(
                "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            connection.execute("INSERT INTO schema_meta(key, value) VALUES('version', '1')")
            connection.execute(
                "CREATE TABLE memories (id TEXT PRIMARY KEY, category TEXT, subject TEXT, key TEXT)"
            )
            connection.execute("INSERT INTO memories(id, category) VALUES('legacy1', 'User')")
            connection.commit()
            connection.close()

            store = MemoryStore(path)
            self.assertEqual(store.schema_version(), 2)

            connection = sqlite3.connect(path)
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            connection.close()
            self.assertNotIn("memories", tables)
            self.assertIn("v2_memories", tables)
            self.assertIn("v2_temporary_memories", tables)
            self.assertIn("v2_memory_events", tables)
            self.assertIn("v2_conversation_trackers", tables)

            backups = list(Path(temp_dir.name).glob("*.legacy-backup-*.db"))
            self.assertEqual(len(backups), 1)
        finally:
            temp_dir.cleanup()

    def test_existing_v2_db_is_untouched(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        try:
            path = Path(temp_dir.name) / "memory.db"
            store = MemoryStore(path)
            record = store.insert_memory(make_parsed())
            reopened = MemoryStore(path)
            self.assertEqual(reopened.schema_version(), 2)
            self.assertEqual(len(reopened.list_memories()), 1)
            self.assertEqual(reopened.get_memory(record.id).value, "violet")
        finally:
            temp_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
