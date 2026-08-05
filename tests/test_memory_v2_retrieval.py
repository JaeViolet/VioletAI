"""Tests for the Memory V2 hybrid retrieval layer."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from memory_v2.models import MemoryLayer, ParsedMemory, Provenance  # noqa: E402
from memory_v2.operations import OperationContext, Operations  # noqa: E402
from memory_v2.retrieval import Retriever  # noqa: E402
from memory_v2.store import MemoryStore  # noqa: E402


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


class RetrieverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = MemoryStore(Path(self.temp_dir.name) / "memory.db")
        self.operations = Operations(self.store)
        self.retriever = Retriever(self.store)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def seed(self, memories: list[ParsedMemory]) -> None:
        for memory in memories:
            self.operations.create_from_parsed(
                memory, OperationContext(conversation_id="c1", message_id="m1", user_text=memory.content)
            )

    def test_exact_key_retrieval(self) -> None:
        self.seed([parsed()])
        outcome = self.retriever.retrieve("my favorite color")
        self.assertTrue(outcome.injected)
        self.assertEqual(outcome.selected[0].record.value, "violet")

    def test_value_retrieval(self) -> None:
        self.seed([parsed()])
        outcome = self.retriever.retrieve("violet")
        self.assertTrue(outcome.injected)
        self.assertEqual(outcome.selected[0].record.value, "violet")

    def test_favorite_color_does_not_match_favorite_drink(self) -> None:
        self.seed([parsed(key="favorite color", value="violet"), parsed(key="favorite drink", value="lemonade")])
        color_outcome = self.retriever.retrieve("what is my favorite color")
        selected_keys = {item.record.key for item in color_outcome.selected}
        self.assertIn("favorite color", selected_keys)
        self.assertNotIn("favorite drink", selected_keys)
        drink_outcome = self.retriever.retrieve("what is my favorite drink")
        drink_keys = {item.record.key for item in drink_outcome.selected}
        self.assertIn("favorite drink", drink_keys)
        self.assertNotIn("favorite color", drink_keys)

    def test_unrelated_query_injects_nothing(self) -> None:
        self.seed([parsed()])
        outcome = self.retriever.retrieve("explain quantum entanglement")
        self.assertFalse(outcome.injected)
        self.assertEqual(outcome.selected, [])

    def test_archived_memory_not_injected(self) -> None:
        self.operations.create_from_parsed(parsed(key="job", value="data entry"), OperationContext(conversation_id="c1", user_text="a"))
        self.operations.create_from_parsed(parsed(key="job", value="software engineer"), OperationContext(conversation_id="c1", user_text="b"))
        outcome = self.retriever.retrieve("my job")
        self.assertTrue(outcome.injected)
        self.assertEqual(len(outcome.selected), 1)
        self.assertEqual(outcome.selected[0].record.value, "software engineer")

    def test_temporary_memory_retrieved(self) -> None:
        self.operations.create_temporary_from_parsed(
            parsed(category="Temporary", key="current project", value="the sidebar rewrite"),
            OperationContext(conversation_id="c2", user_text="i am working on the sidebar rewrite", token_counter=100),
        )
        outcome = self.retriever.retrieve("what am i working on")
        temporary = [item for item in outcome.selected if item.layer == MemoryLayer.TEMPORARY]
        self.assertTrue(temporary)
        self.assertEqual(temporary[0].record.value, "the sidebar rewrite")

    def test_include_all_returns_durable(self) -> None:
        self.seed([parsed(), parsed(key="name", value="Ada", category="User")])
        outcome = self.retriever.retrieve("what do you remember about me", include_all=True)
        self.assertTrue(outcome.injected)
        self.assertGreaterEqual(len(outcome.selected), 2)

    def test_project_subject_match(self) -> None:
        self.seed([parsed(category="Projects", subject="project Apollo", key="description", value="lunar lander")])
        outcome = self.retriever.retrieve("what is project Apollo")
        self.assertTrue(outcome.injected)
        self.assertEqual(outcome.selected[0].record.subject, "project Apollo")

    def test_max_results_limit(self) -> None:
        self.seed(
            [
                parsed(key="favorite color", value="violet"),
                parsed(key="favorite drink", value="lemonade"),
                parsed(key="favorite movie", value="Inception"),
                parsed(key="favorite food", value="pizza"),
                parsed(key="favorite game", value="Archero"),
                parsed(key="favorite band", value="Radiohead"),
                parsed(key="favorite city", value="Montreal"),
                parsed(key="favorite sport", value="tennis"),
            ]
        )
        outcome = self.retriever.retrieve("favorite", max_results=4)
        self.assertLessEqual(len(outcome.selected), 4)


if __name__ == "__main__":
    unittest.main()
