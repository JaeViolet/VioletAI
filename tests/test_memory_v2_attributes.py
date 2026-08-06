"""Attribute-precision tests for VioletAI Memory V2.

Generic modifiers ("favorite", "current", "personal", ...) must never bridge
unrelated attributes. Occupation paraphrase queries must resolve to the stored
"occupation" slot. Person-scoped references must never touch another person's
memories. These tests lock in the precision rules the application relies on.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from memory_v2.attributes import (  # noqa: E402
    attribute_identity,
    parse_reference,
    references_record,
    subject_matches,
)
from memory_v2.models import MutationStatus  # noqa: E402
from memory_v2.pipeline import MemorySystem  # noqa: E402
from memory_v2.store import MemoryStore  # noqa: E402


class AttributeIdentityTests(unittest.TestCase):
    def test_generic_modifiers_stripped(self) -> None:
        self.assertEqual(attribute_identity("favorite color"), "color")
        self.assertEqual(attribute_identity("favourite colour"), "color")
        self.assertEqual(attribute_identity("preferred color"), "color")
        self.assertEqual(attribute_identity("current project"), "project")

    def test_occupation_aliases(self) -> None:
        for phrase in ("job", "work", "profession", "career", "my occupation"):
            self.assertEqual(attribute_identity(phrase), "occupation", phrase)

    def test_phone_family_separates(self) -> None:
        self.assertEqual(attribute_identity("personal phone"), "phone number")
        self.assertEqual(attribute_identity("work phone"), "work phone number")
        self.assertNotEqual(attribute_identity("work phone"), attribute_identity("personal phone"))
        self.assertEqual(attribute_identity("mobile"), "phone number")

    def test_unrelated_attributes_never_merge(self) -> None:
        self.assertNotEqual(attribute_identity("favorite color"), attribute_identity("favorite drink"))
        self.assertNotEqual(attribute_identity("favorite color"), attribute_identity("hair color"))
        self.assertNotEqual(attribute_identity("birthday"), attribute_identity("occupation"))


class ParseReferenceTests(unittest.TestCase):
    def test_first_person_question(self) -> None:
        ref = parse_reference("what is my job")
        self.assertIsNotNone(ref)
        self.assertEqual(ref.subject, "user")
        self.assertEqual(ref.identity, "occupation")
        self.assertTrue(ref.explicit_subject)

    def test_occupation_paraphrases(self) -> None:
        for text in (
            "what is my job",
            "what is my profession",
            "what is my occupation",
            "what do you do for work",
            "what do you do",
        ):
            ref = parse_reference(text)
            self.assertIsNotNone(ref, text)
            self.assertEqual(ref.identity, "occupation", text)

    def test_person_reference(self) -> None:
        ref = parse_reference("when is alice's birthday")
        self.assertIsNotNone(ref)
        self.assertEqual(ref.subject, "alice")
        self.assertEqual(ref.identity, "birthday")

    def test_generic_only_reference(self) -> None:
        ref = parse_reference("favorite")
        self.assertIsNotNone(ref)
        self.assertTrue(ref.is_generic_only)
        self.assertEqual(ref.identity, "")

    def test_negative_control_not_a_reference(self) -> None:
        ref = parse_reference("what job is alice applying for")
        self.assertIsNotNone(ref)
        self.assertNotEqual(ref.identity, "occupation")

    def test_references_record(self) -> None:
        color_ref = parse_reference("favorite color")
        self.assertTrue(references_record(color_ref, "favorite color"))
        self.assertFalse(references_record(color_ref, "favorite drink"))
        self.assertFalse(references_record(color_ref, "hair color"))

    def test_subject_matches(self) -> None:
        self.assertTrue(subject_matches("user", "user"))
        self.assertTrue(subject_matches("alice", "Alice"))
        self.assertFalse(subject_matches("alice", "user"))


class PrecisionEndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.system = MemorySystem(MemoryStore(Path(self.temp_dir.name) / "memory.db"))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _selected(self, query: str) -> list[str]:
        outcome = self.system.handle_user_message(query, conversation_id=f"q-{query}")
        self.assertIsNotNone(outcome.retrieval)
        return [(item.record.subject, item.record.key, item.record.value) for item in outcome.retrieval.selected]

    def _active(self) -> list[tuple[str, str, str]]:
        return [(m.subject, m.key, m.value) for m in self.system.list_memories()]

    def test_occupation_paraphrase_retrieval(self) -> None:
        self.system.handle_user_message("my job is software engineer", conversation_id="c1")
        for query in (
            "what is my job",
            "what is my profession",
            "what is my occupation",
            "what do you do for work",
        ):
            selected = self._selected(query)
            self.assertIn(("user", "occupation", "software engineer"), selected, query)

    def test_occupation_negative_controls_inject_nothing(self) -> None:
        self.system.handle_user_message("my job is software engineer", conversation_id="c1")
        for query in ("what job is alice applying for", "what project am i working on"):
            selected = self._selected(query)
            self.assertNotIn(("user", "occupation", "software engineer"), selected, query)

    def test_favorite_color_delete_leaves_others_intact(self) -> None:
        self.system.handle_user_message("my favorite color is violet", conversation_id="c1")
        self.system.handle_user_message("my favorite drink is water", conversation_id="c2")
        self.system.handle_user_message("my hair color is brown", conversation_id="c3")
        outcome = self.system.handle_user_message("forget my favorite color", conversation_id="c4")
        self.assertNotEqual(outcome.action_status, MutationStatus.MULTIPLE_MATCHES)
        self.assertEqual(
            sorted(self._active()),
            sorted([("user", "favorite drink", "water"), ("user", "hair color", "brown")]),
        )

    def test_generic_favorite_delete_is_ambiguous(self) -> None:
        self.system.handle_user_message("my favorite color is violet", conversation_id="c1")
        self.system.handle_user_message("my favorite drink is water", conversation_id="c2")
        outcome = self.system.handle_user_message("forget my favorite", conversation_id="c3")
        self.assertEqual(outcome.action_status, MutationStatus.MULTIPLE_MATCHES)
        self.assertEqual(len(self._active()), 2)

    def test_person_scoped_delete(self) -> None:
        self.system.handle_user_message("alice's birthday is june 5th", conversation_id="c1")
        self.system.handle_user_message("bob's birthday is march 3rd", conversation_id="c2")
        outcome = self.system.handle_user_message("forget alice's birthday", conversation_id="c3")
        self.assertNotEqual(outcome.action_status, MutationStatus.MULTIPLE_MATCHES)
        self.assertEqual(self._active(), [("bob", "birthday", "march 3rd")])

    def test_person_question_does_not_touch_user_memories(self) -> None:
        self.system.handle_user_message("my phone number is 555-0000", conversation_id="c1")
        self.system.handle_user_message("alice's phone number is 555-0100", conversation_id="c2")
        alice_selected = self._selected("what is alice's phone number")
        self.assertIn(("alice", "phone number", "555-0100"), alice_selected)
        self.assertNotIn(("user", "phone number", "555-0000"), alice_selected)
        user_selected = self._selected("what is my phone number")
        self.assertIn(("user", "phone number", "555-0000"), user_selected)
        self.assertNotIn(("alice", "phone number", "555-0100"), user_selected)

    def test_work_personal_phone_separate(self) -> None:
        self.system.handle_user_message("my work phone is 555-1000", conversation_id="c1")
        self.system.handle_user_message("my personal phone is 555-2000", conversation_id="c2")
        outcome = self.system.handle_user_message("forget my work phone", conversation_id="c3")
        self.assertNotEqual(outcome.action_status, MutationStatus.MULTIPLE_MATCHES)
        self.assertEqual(self._active(), [("user", "personal phone", "555-2000")])


if __name__ == "__main__":
    unittest.main()
