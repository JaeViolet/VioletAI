"""End-to-end tests for the MemorySystem facade."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from memory_v2.models import MemoryLayer, ParsedMemory, Provenance, MutationKind, MutationStatus  # noqa: E402
from memory_v2.pipeline import MemorySystem, MemorySystemConfig  # noqa: E402
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
        "provenance": Provenance(conversation_id="c1", message_id="m1"),
    }
    defaults.update(overrides)
    return ParsedMemory(**defaults)  # type: ignore[arg-type]


class MemorySystemTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "memory.db"
        self.store = MemoryStore(self.path)
        self.system = MemorySystem(self.store)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    # ------------------------------------------------------------- questions

    def test_question_about_memory_is_read_only(self) -> None:
        self.system.handle_user_message("my favorite color is violet", conversation_id="c1")
        before_events = len(self.store.recent_events(limit=500))
        outcome = self.system.handle_user_message("what is my favorite color", conversation_id="c2")
        self.assertTrue(outcome.memory_related)
        self.assertIsNone(outcome.action)
        self.assertTrue(outcome.retrieval.injected)
        self.assertTrue(outcome.retrieval.selected)
        self.assertEqual(len(self.store.recent_events(limit=500)), before_events)

    def test_memory_question_never_writes(self) -> None:
        outcome = self.system.handle_user_message("what do you remember about me", conversation_id="c1")
        self.assertTrue(outcome.memory_related)
        self.assertIsNone(outcome.action)
        self.assertEqual(self.system.list_memories(), [])

    # --------------------------------------------------------------- writes

    def test_remember_command_creates_durable(self) -> None:
        outcome = self.system.handle_user_message(
            "remember that my favorite color is violet", conversation_id="c1"
        )
        self.assertEqual(outcome.action, MutationKind.CREATE)
        self.assertEqual(outcome.action_status, MutationStatus.SUCCESS)
        memories = self.system.list_memories()
        self.assertEqual(len(memories), 1)
        self.assertEqual(memories[0].value, "violet")
        self.assertEqual(memories[0].layer, MemoryLayer.DURABLE)

    def test_automatic_durable_fact(self) -> None:
        outcome = self.system.handle_user_message("my name is violet", conversation_id="c1")
        self.assertEqual(outcome.action, MutationKind.CREATE)
        records = [r for r in self.system.list_memories() if r.key == "name"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].value, "violet")
        self.assertEqual(records[0].provenance.kind.value, "automatic")

    def test_temporary_task_command(self) -> None:
        outcome = self.system.handle_user_message("remember to buy milk", conversation_id="c1")
        self.assertEqual(outcome.action, MutationKind.TEMPORARY_CREATE)
        active = self.system.temporary.active_context()
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].key, "task")
        self.assertEqual(active[0].value, "buy milk")
        self.assertEqual(active[0].unresolved, 1)

    def test_suppression_prevents_write(self) -> None:
        outcome = self.system.handle_user_message("don't save that", conversation_id="c1")
        self.assertIsNone(outcome.action)
        self.assertEqual(self.system.list_memories(), [])
        self.assertEqual(outcome.notice, "write suppressed by user")

    def test_update_command(self) -> None:
        self.system.handle_user_message("my favorite color is violet", conversation_id="c1")
        outcome = self.system.handle_user_message(
            "update my favorite color to indigo", conversation_id="c2"
        )
        self.assertEqual(outcome.action, MutationKind.UPDATE)
        self.assertEqual(outcome.action_status, MutationStatus.SUCCESS)
        memories = self.system.list_memories()
        self.assertEqual(len(memories), 1)
        self.assertEqual(memories[0].value, "indigo")

    def test_delete_command_archives(self) -> None:
        self.system.handle_user_message("my favorite color is violet", conversation_id="c1")
        outcome = self.system.handle_user_message("forget my favorite color", conversation_id="c2")
        self.assertEqual(outcome.action, MutationKind.DELETE)
        self.assertEqual(self.system.list_memories(), [])
        self.assertEqual(len(self.system.list_memories(include_archived=True)), 1)

    def test_ambiguous_update_asks_for_clarification(self) -> None:
        self.system.handle_user_message("my favorite color is violet", conversation_id="c1")
        self.system.handle_user_message("my favorite drink is coffee", conversation_id="c2")
        outcome = self.system.handle_user_message("update my favorite to orange", conversation_id="c3")
        self.assertEqual(outcome.action, MutationKind.UPDATE)
        self.assertEqual(outcome.action_status, MutationStatus.MULTIPLE_MATCHES)
        self.assertTrue(outcome.clarification_needed)
        color = [r for r in self.system.list_memories() if r.key == "favorite color"]
        drink = [r for r in self.system.list_memories() if r.key == "favorite drink"]
        self.assertEqual(color[0].value, "violet")
        self.assertEqual(drink[0].value, "coffee")

    # ------------------------------------------------------------ retrieval

    def test_retrieval_injects_relevant_memory(self) -> None:
        self.system.handle_user_message("my favorite color is violet", conversation_id="c1")
        outcome = self.system.handle_user_message("what is my favorite color", conversation_id="c2")
        self.assertTrue(outcome.retrieval.selected)
        self.assertTrue(any(item.layer == MemoryLayer.DURABLE for item in outcome.retrieval.selected))

    def test_unrelated_query_injects_nothing(self) -> None:
        self.system.handle_user_message("my favorite color is violet", conversation_id="c1")
        outcome = self.system.handle_user_message("how does the weather affect plants", conversation_id="c2")
        self.assertFalse(outcome.retrieval.selected)
        self.assertFalse(outcome.retrieval.injected)

    def test_current_state_retrieves_temporary(self) -> None:
        self.system.handle_user_message("i am working on the chat UI", conversation_id="c1")
        outcome = self.system.handle_user_message("what am i working on", conversation_id="c1")
        self.assertTrue(outcome.retrieval.selected)
        self.assertTrue(any(item.layer == MemoryLayer.TEMPORARY for item in outcome.retrieval.selected))

    def test_favorite_color_never_retrieves_favorite_drink(self) -> None:
        self.system.handle_user_message("my favorite color is violet", conversation_id="c1")
        self.system.handle_user_message("my favorite drink is coffee", conversation_id="c2")
        outcome = self.system.handle_user_message("what is my favorite color", conversation_id="c3")
        selected_values = {item.record.value for item in outcome.retrieval.selected}
        self.assertIn("violet", selected_values)
        self.assertNotIn("coffee", selected_values)

    # ----------------------------------------------------------- lifecycle

    def test_consolidation_runs_periodically(self) -> None:
        first = self.store.insert_memory(make_parsed(value="violet"))
        second = self.store.insert_memory(make_parsed(value="violet"))
        system = MemorySystem(self.store, MemorySystemConfig(consolidation_interval=1))
        system.handle_user_message("hello there", conversation_id="c1")
        self.assertEqual(self.store.get_memory(first.id).layer, MemoryLayer.ARCHIVED)
        self.assertEqual(self.store.get_memory(second.id).layer, MemoryLayer.DURABLE)

    def test_stats_counts(self) -> None:
        self.system.handle_user_message("my favorite color is violet", conversation_id="c1")
        self.system.handle_user_message("remember to buy milk", conversation_id="c2")
        stats = self.system.stats()
        self.assertEqual(stats.durable, 1)
        self.assertEqual(stats.temporary_active, 1)
        self.assertEqual(stats.schema_version, 2)

    def test_manager_apis(self) -> None:
        self.system.handle_user_message("my favorite color is violet", conversation_id="c1")
        memory_id = self.system.list_memories()[0].id
        self.assertTrue(self.system.archive(memory_id).ok)
        self.assertEqual(self.system.get_memory(memory_id).layer, MemoryLayer.ARCHIVED)
        self.assertTrue(self.system.restore(memory_id).ok)
        self.assertEqual(self.system.get_memory(memory_id).layer, MemoryLayer.DURABLE)
        self.assertTrue(self.system.delete(memory_id).ok)
        self.assertIsNone(self.system.get_memory(memory_id))
        self.system.handle_user_message("my favorite drink is coffee", conversation_id="c2")
        self.assertTrue(self.system.clear_durable().ok)
        self.assertEqual(self.system.list_memories(), [])

    def test_retrieve_marks_accessed(self) -> None:
        self.system.handle_user_message("my favorite color is violet", conversation_id="c1")
        record = self.system.list_memories()[0]
        self.system.retrieve("favorite color", include_all=True, mark_accessed=True)
        self.assertEqual(self.system.get_memory(record.id).access_count, 1)


if __name__ == "__main__":
    unittest.main()
