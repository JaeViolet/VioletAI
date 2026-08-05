"""Adversarial end-to-end checks that every listed memory-corruption source is eliminated.

Each test drives the MemorySystem facade exactly the way the application does and
asserts one of the non-negotiable rules:

* exact meaning over loose similarity (favorite color vs favorite drink),
* old facts never override current facts (old job vs current job),
* questions about memory are strictly read-only,
* mutations flow only through validated operations,
* deletion is extremely conservative (never claims success on a no-match),
* retrieval only injects when it genuinely helps (unrelated queries inject nothing),
* uncertainty asks instead of guessing (ambiguous updates never mutate).
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from memory_v2.models import MemoryLayer, MutationKind, MutationStatus  # noqa: E402
from memory_v2.pipeline import MemorySystem  # noqa: E402
from memory_v2.store import MemoryStore  # noqa: E402


class AdversarialMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.system = MemorySystem(MemoryStore(Path(self.temp_dir.name) / "memory.db"))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _event_count(self) -> int:
        return len(self.system.store.recent_events(limit=500))

    def _active_values(self, key: str) -> list[str]:
        return [m.value for m in self.system.list_memories() if m.key == key]

    def _selected_values(self, query: str) -> list[str]:
        outcome = self.system.handle_user_message(query, conversation_id=f"q-{query}")
        self.assertIsNotNone(outcome.retrieval)
        return [item.record.value for item in outcome.retrieval.selected]

    # -------------------------------------------------------- exact meaning

    def test_favorite_color_never_retrieves_favorite_drink(self) -> None:
        self.system.handle_user_message("my favorite drink is water", conversation_id="c1")
        self.system.handle_user_message("my favorite color is purple", conversation_id="c2")
        color_values = self._selected_values("what is my favorite color")
        self.assertIn("purple", color_values)
        self.assertNotIn("water", color_values)
        drink_values = self._selected_values("what is my favorite drink")
        self.assertIn("water", drink_values)
        self.assertNotIn("purple", drink_values)

    def test_favorite_slot_generic_key_never_cross_retrieves(self) -> None:
        self.system.handle_user_message("my favorite movie is interstellar", conversation_id="c1")
        self.system.handle_user_message("my favorite song is nocturne", conversation_id="c2")
        self.assertNotIn(
            "interstellar",
            self._selected_values("what is my favorite song"),
        )
        self.assertNotIn(
            "nocturne",
            self._selected_values("what is my favorite movie"),
        )

    # ------------------------------------------------------------- current beats old

    def test_old_job_never_overrides_current_job(self) -> None:
        self.system.handle_user_message("my job is data entry", conversation_id="c1")
        self.system.handle_user_message("my job is software engineer", conversation_id="c2")
        active = [m for m in self.system.list_memories() if m.key == "occupation"]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].value, "software engineer")
        archived = [
            m
            for m in self.system.list_memories(include_archived=True)
            if m.key == "occupation" and m.layer == MemoryLayer.ARCHIVED
        ]
        self.assertEqual([m.value for m in archived], ["data entry"])
        self.assertTrue(archived[0].superseded_by_id is not None)
        selected = self._selected_values("my job")
        self.assertIn("software engineer", selected)
        self.assertNotIn("data entry", selected)

    def test_new_favorite_replaces_old_favorite_slot(self) -> None:
        self.system.handle_user_message("my favorite color is purple", conversation_id="c1")
        self.system.handle_user_message("my favorite color is teal", conversation_id="c2")
        self.assertEqual(self._active_values("favorite color"), ["teal"])
        self.assertEqual(self._selected_values("what is my favorite color"), ["teal"])

    # ---------------------------------------------------------------- read-only

    def test_memory_question_never_writes_or_deletes(self) -> None:
        self.system.handle_user_message("my favorite color is purple", conversation_id="c1")
        events_before = self._event_count()
        outcome = self.system.handle_user_message("what is my favorite color", conversation_id="c2")
        self.assertIsNone(outcome.action)
        self.assertIsNone(outcome.action_status)
        self.assertEqual(self._event_count(), events_before)
        self.assertEqual(self._active_values("favorite color"), ["purple"])

    def test_meta_question_about_memory_never_writes(self) -> None:
        events_before = self._event_count()
        outcome = self.system.handle_user_message("what do you remember about me", conversation_id="c1")
        self.assertIsNone(outcome.action)
        self.assertEqual(self._event_count(), events_before)
        self.assertEqual(self.system.list_memories(), [])

    # ------------------------------------------------- conservative mutations

    def test_delete_of_missing_memory_never_claims_success(self) -> None:
        events_before = self._event_count()
        outcome = self.system.handle_user_message(
            "delete the memory about my favorite color",
            conversation_id="c1",
        )
        self.assertEqual(outcome.action, MutationKind.DELETE)
        self.assertEqual(outcome.action_status, MutationStatus.NO_MATCH)
        self.assertEqual(self._event_count(), events_before)
        self.assertEqual(self.system.list_memories(), [])

    def test_ambiguous_update_asks_instead_of_guessing(self) -> None:
        self.system.handle_user_message("my favorite color is purple", conversation_id="c1")
        self.system.handle_user_message("my favorite drink is water", conversation_id="c2")
        outcome = self.system.handle_user_message("update my favorite to green", conversation_id="c3")
        self.assertEqual(outcome.action_status, MutationStatus.MULTIPLE_MATCHES)
        self.assertTrue(outcome.clarification_needed)
        self.assertEqual(self._active_values("favorite color"), ["purple"])
        self.assertEqual(self._active_values("favorite drink"), ["water"])

    def test_suppression_statement_writes_nothing(self) -> None:
        events_before = self._event_count()
        outcome = self.system.handle_user_message("don't save that", conversation_id="c1")
        self.assertIsNone(outcome.action)
        self.assertEqual(self._event_count(), events_before)
        self.assertEqual(self.system.list_memories(), [])

    # ------------------------------------------------- retrieval only when useful

    def test_unrelated_query_injects_nothing(self) -> None:
        self.system.handle_user_message("my favorite color is purple", conversation_id="c1")
        outcome = self.system.handle_user_message(
            "how does photosynthesis work in plants",
            conversation_id="c2",
        )
        self.assertIsNotNone(outcome.retrieval)
        self.assertFalse(outcome.retrieval.injected)
        self.assertEqual(outcome.retrieval.selected, [])

    def test_durable_and_temporary_layers_never_cross_inject(self) -> None:
        self.system.handle_user_message("my favorite color is purple", conversation_id="c1")
        outcome = self.system.handle_user_message("what am i working on right now", conversation_id="c2")
        self.assertIsNotNone(outcome.retrieval)
        selected_layers = {item.layer for item in outcome.retrieval.selected}
        self.assertNotIn(MemoryLayer.TEMPORARY, selected_layers)


if __name__ == "__main__":
    unittest.main()
