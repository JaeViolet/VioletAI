"""Tests for the Memory V2 validated operations layer."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from memory_v2.models import (  # noqa: E402
    MemoryCommand,
    MemoryLayer,
    MutationKind,
    MutationStatus,
    ParsedMemory,
    Provenance,
)
from memory_v2.operations import OperationContext, Operations  # noqa: E402
from memory_v2.store import MemoryStore  # noqa: E402


def context(text: str = "", previous: str | None = None, conversation_id: str = "c1", token: int = 0) -> OperationContext:
    return OperationContext(
        conversation_id=conversation_id,
        message_id="m1",
        user_text=text,
        previous_user_text=previous,
        token_counter=token,
        conversation_index=1,
    )


def parsed(**overrides: object) -> ParsedMemory:
    defaults: dict[str, object] = {
        "category": "Preferences",
        "subject": "user",
        "key": "favorite color",
        "value": "violet",
        "content": "favorite color is violet",
        "confidence": 0.9,
        "provenance": Provenance(conversation_id="c1", message_id="m1", user_text="my favorite color is violet"),
    }
    defaults.update(overrides)
    return ParsedMemory(**defaults)  # type: ignore[arg-type]


class OperationsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = MemoryStore(Path(self.temp_dir.name) / "memory.db")
        self.operations = Operations(self.store)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_create_new_memory(self) -> None:
        outcome = self.operations.create_from_parsed(parsed(), context(text="my favorite color is violet"))
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.kind, MutationKind.CREATE)
        self.assertEqual(outcome.status, MutationStatus.SUCCESS)
        self.assertIsNotNone(outcome.record)
        self.assertEqual(len(self.store.list_memories()), 1)

    def test_create_same_value_no_duplicate(self) -> None:
        self.operations.create_from_parsed(parsed(), context(text="a"))
        outcome = self.operations.create_from_parsed(parsed(), context(text="b"))
        self.assertTrue(outcome.ok)
        self.assertEqual(len(self.store.list_memories()), 1)

    def test_create_new_value_supersedes_old(self) -> None:
        first = self.operations.create_from_parsed(parsed(value="violet"), context(text="a"))
        second = self.operations.create_from_parsed(parsed(value="indigo"), context(text="b"))
        self.assertTrue(second.ok)
        records = self.store.list_memories(include_archived=True)
        self.assertEqual(len(records), 2)
        archived = [r for r in records if r.layer == MemoryLayer.ARCHIVED]
        active = [r for r in records if r.layer == MemoryLayer.DURABLE]
        self.assertEqual(len(archived), 1)
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].value, "indigo")
        self.assertEqual(archived[0].superseded_by_id, active[0].id)
        self.assertEqual(active[0].supersedes_id, archived[0].id)

    def test_update_existing_memory(self) -> None:
        self.operations.create_from_parsed(parsed(value="violet"), context(text="a"))
        command = MemoryCommand(kind=MutationKind.UPDATE, confidence=0.9, key="favorite color", value="green", subject="user")
        outcome = self.operations.apply(command, context(text="update my favorite color to green"))
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.previous_value, "violet")
        self.assertEqual(outcome.new_value, "green")
        self.assertEqual(len(self.store.list_memories()), 1)
        self.assertEqual(self.store.list_memories()[0].value, "green")

    def test_update_no_match(self) -> None:
        command = MemoryCommand(kind=MutationKind.UPDATE, confidence=0.9, key="favorite color", value="green", subject="user")
        outcome = self.operations.apply(command, context(text="update my favorite color to green"))
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.status, MutationStatus.NO_MATCH)
        self.assertTrue(outcome.clarification)

    def test_delete_existing_memory_archives(self) -> None:
        self.operations.create_from_parsed(parsed(), context(text="a"))
        command = MemoryCommand(kind=MutationKind.DELETE, confidence=0.9, key="favorite color", subject="user")
        outcome = self.operations.apply(command, context(text="forget my favorite color"))
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.status, MutationStatus.SUCCESS)
        self.assertEqual(self.store.list_memories(), [])
        self.assertEqual(len(self.store.list_memories(include_archived=True)), 1)
        archived = self.store.list_memories(include_archived=True)[0]
        self.assertEqual(archived.layer, MemoryLayer.ARCHIVED)

    def test_delete_no_match(self) -> None:
        command = MemoryCommand(kind=MutationKind.DELETE, confidence=0.9, key="favorite color", subject="user")
        outcome = self.operations.apply(command, context(text="forget my favorite color"))
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.status, MutationStatus.NO_MATCH)

    def test_contextual_create_uses_previous(self) -> None:
        command = MemoryCommand(kind=MutationKind.CREATE, confidence=0.9, reference_previous=True, subject="user")
        outcome = self.operations.apply(command, context(text="remember that", previous="my favorite color is violet"))
        self.assertTrue(outcome.ok)
        self.assertEqual(len(self.store.list_memories()), 1)
        self.assertEqual(self.store.list_memories()[0].value, "violet")

    def test_contextual_create_without_previous_fails(self) -> None:
        command = MemoryCommand(kind=MutationKind.CREATE, confidence=0.9, reference_previous=True, subject="user")
        outcome = self.operations.apply(command, context(text="remember that"))
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.status, MutationStatus.INVALID_REFERENCE)

    def test_contextual_delete_uses_previous(self) -> None:
        self.operations.create_from_parsed(parsed(), context(text="a"))
        command = MemoryCommand(kind=MutationKind.DELETE, confidence=0.9, reference_previous=True, subject="user")
        outcome = self.operations.apply(command, context(text="forget that", previous="my favorite color is violet"))
        self.assertTrue(outcome.ok)
        self.assertEqual(self.store.list_memories(), [])

    def test_contextual_update_uses_previous(self) -> None:
        self.operations.create_from_parsed(parsed(), context(text="a"))
        command = MemoryCommand(kind=MutationKind.UPDATE, confidence=0.9, value="green", reference_previous=True, subject="user")
        outcome = self.operations.apply(command, context(text="change it to green", previous="my favorite color is violet"))
        self.assertTrue(outcome.ok)
        self.assertEqual(self.store.list_memories()[0].value, "green")

    def test_temporary_create_command(self) -> None:
        command = MemoryCommand(
            kind=MutationKind.TEMPORARY_CREATE, confidence=0.95, key="task", value="buy milk", subject="user"
        )
        outcome = self.operations.apply(command, context(text="remember to buy milk", token=100))
        self.assertTrue(outcome.ok)
        records = self.store.list_temporary()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].value, "buy milk")
        self.assertEqual(records[0].token_at_created, 100)

    def test_temporary_create_deduplicates(self) -> None:
        command = MemoryCommand(kind=MutationKind.TEMPORARY_CREATE, confidence=0.95, key="task", value="buy milk", subject="user")
        self.operations.apply(command, context(text="a", token=100))
        outcome = self.operations.apply(command, context(text="b", token=200))
        self.assertTrue(outcome.ok)
        self.assertEqual(len(self.store.list_temporary()), 1)

    def test_archive_restore_delete_by_id(self) -> None:
        created = self.operations.create_from_parsed(parsed(), context(text="a"))
        record_id = created.record.id
        archived = self.operations.archive_by_id(record_id)
        self.assertTrue(archived.ok)
        restored = self.operations.restore_by_id(record_id)
        self.assertTrue(restored.ok)
        self.assertEqual(self.store.get_memory(record_id).layer, MemoryLayer.DURABLE)
        deleted = self.operations.delete_by_id(record_id)
        self.assertTrue(deleted.ok)
        self.assertIsNone(self.store.get_memory(record_id))

    def test_clear_durable(self) -> None:
        self.operations.create_from_parsed(parsed(), context(text="a"))
        outcome = self.operations.clear_durable()
        self.assertTrue(outcome.ok)
        self.assertEqual(self.store.list_memories(), [])

    def test_delete_expires_matching_temporary(self) -> None:
        self.operations.create_temporary_from_parsed(
            parsed(category="Temporary", key="task", value="buy milk"),
            context(text="remember to buy milk", token=10),
        )
        command = MemoryCommand(kind=MutationKind.DELETE, confidence=0.9, key="buy milk", subject="user")
        self.operations.apply(command, context(text="forget that I need to buy milk"))
        self.assertEqual(len(self.store.list_temporary(status="active")), 0)
        self.assertEqual(len(self.store.list_temporary(status="expired")), 1)


class AmbiguityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = MemoryStore(Path(self.temp_dir.name) / "memory.db")
        self.operations = Operations(self.store)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_delete_ambiguous_matches_asks(self) -> None:
        self.operations.create_from_parsed(parsed(key="favorite color", value="violet"), context(text="a"))
        self.operations.create_from_parsed(parsed(key="favorite movie", value="Inception"), context(text="b"))
        command = MemoryCommand(kind=MutationKind.DELETE, confidence=0.9, key="favorite", subject="user")
        outcome = self.operations.apply(command, context(text="forget my favorite"))
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.status, MutationStatus.MULTIPLE_MATCHES)
        self.assertIn("multiple matching memories", outcome.clarification.lower())
        self.assertEqual(len(self.store.list_memories()), 2)

    def test_delete_specific_ambiguous_resolves(self) -> None:
        self.operations.create_from_parsed(parsed(key="favorite color", value="violet"), context(text="a"))
        self.operations.create_from_parsed(parsed(key="favorite movie", value="Inception"), context(text="b"))
        command = MemoryCommand(kind=MutationKind.DELETE, confidence=0.9, key="favorite color", subject="user")
        outcome = self.operations.apply(command, context(text="forget my favorite color"))
        self.assertTrue(outcome.ok)
        self.assertEqual(len(self.store.list_memories()), 1)


if __name__ == "__main__":
    unittest.main()
