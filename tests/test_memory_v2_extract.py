"""Tests for the Memory V2 extractor (intent + fact extraction)."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from memory_v2.extract import Extractor, clean_value  # noqa: E402
from memory_v2.models import MutationKind  # noqa: E402


class QuestionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.extractor = Extractor()

    def assert_question_no_command(self, text: str) -> None:
        analysis = self.extractor.analyze(text)
        self.assertTrue(analysis.is_question_about_memory, text)
        self.assertIsNone(analysis.command, text)
        self.assertIsNone(analysis.durable_fact, text)
        self.assertIsNone(analysis.temporary_fact, text)

    def test_questions_are_read_only(self) -> None:
        questions = [
            "how does memory work",
            "how does your memory work",
            "what do you remember about me",
            "what do you remember",
            "what memories do you have",
            "do you remember me",
            "what is my favorite color",
            "what's my name",
            "do you remember my name",
            "tell me about my preferences",
            "what have you saved",
            "how many memories do you have",
            "show me my memories",
            "can you see my memory",
        ]
        for question in questions:
            self.assert_question_no_command(question)


class SuppressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.extractor = Extractor()

    def test_negative_imperatives_suppress_writes(self) -> None:
        messages = [
            "don't save that",
            "do not save this",
            "don't remember that",
            "don't store this",
            "please don't add this to memory",
            "do not add this to your memory",
        ]
        for message in messages:
            analysis = self.extractor.analyze(message)
            self.assertIsNone(analysis.command, message)
            self.assertIsNone(analysis.durable_fact, message)
            self.assertIsNone(analysis.temporary_fact, message)

    def test_i_do_not_remember_is_not_a_delete(self) -> None:
        analysis = self.extractor.analyze("I don't remember my password")
        self.assertIsNone(analysis.command)
        self.assertFalse(analysis.is_question_about_memory)


class CreateCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.extractor = Extractor()

    def test_remember_with_fact(self) -> None:
        analysis = self.extractor.analyze("remember that my favorite color is violet")
        self.assertEqual(analysis.command.kind, MutationKind.CREATE)
        self.assertEqual(analysis.command.key, "favorite color")
        self.assertEqual(analysis.command.value, "violet")

    def test_remember_free_form(self) -> None:
        analysis = self.extractor.analyze("remember that the wifi password is sunflower")
        self.assertEqual(analysis.command.kind, MutationKind.CREATE)
        self.assertEqual(analysis.command.key, "note")
        self.assertEqual(analysis.command.value, "the wifi password is sunflower")

    def test_contextual_create(self) -> None:
        analysis = self.extractor.analyze("remember that")
        self.assertEqual(analysis.command.kind, MutationKind.CREATE)
        self.assertTrue(analysis.command.reference_previous)

    def test_save_wrapper(self) -> None:
        analysis = self.extractor.analyze("please save my favorite drink is lemonade")
        self.assertEqual(analysis.command.kind, MutationKind.CREATE)
        self.assertEqual(analysis.command.value, "lemonade")

    def test_temporary_task_command(self) -> None:
        analysis = self.extractor.analyze("remember to buy milk")
        self.assertEqual(analysis.command.kind, MutationKind.TEMPORARY_CREATE)
        self.assertEqual(analysis.command.value, "buy milk")

    def test_dont_forget_task(self) -> None:
        analysis = self.extractor.analyze("don't forget to water the plants")
        self.assertEqual(analysis.command.kind, MutationKind.TEMPORARY_CREATE)
        self.assertEqual(analysis.command.value, "water the plants")

    def test_remember_this_without_body_refers_previous(self) -> None:
        analysis = self.extractor.analyze("remember this")
        self.assertTrue(analysis.command.reference_previous)


class UpdateCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.extractor = Extractor()

    def test_update_my(self) -> None:
        analysis = self.extractor.analyze("update my favorite color to indigo")
        self.assertEqual(analysis.command.kind, MutationKind.UPDATE)
        self.assertEqual(analysis.command.key, "favorite color")
        self.assertEqual(analysis.command.value, "indigo")

    def test_change_my(self) -> None:
        analysis = self.extractor.analyze("change my name to Ada")
        self.assertEqual(analysis.command.key, "name")
        self.assertEqual(analysis.command.value, "Ada")

    def test_natural_now_update(self) -> None:
        analysis = self.extractor.analyze("my favorite color is now green")
        self.assertEqual(analysis.command.kind, MutationKind.UPDATE)
        self.assertEqual(analysis.command.value, "green")

    def test_change_it_to(self) -> None:
        analysis = self.extractor.analyze("change it to blue")
        self.assertEqual(analysis.command.kind, MutationKind.UPDATE)
        self.assertEqual(analysis.command.key, "")
        self.assertEqual(analysis.command.value, "blue")
        self.assertTrue(analysis.command.reference_previous)

    def test_in_your_memory_it_says(self) -> None:
        analysis = self.extractor.analyze("in your memory it says my favorite color is red, change it to green")
        self.assertEqual(analysis.command.kind, MutationKind.UPDATE)
        self.assertEqual(analysis.command.value, "green")


class DeleteCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.extractor = Extractor()

    def test_forget_key(self) -> None:
        analysis = self.extractor.analyze("forget my favorite color")
        self.assertEqual(analysis.command.kind, MutationKind.DELETE)
        self.assertEqual(analysis.command.key, "favorite color")

    def test_forget_without_my(self) -> None:
        analysis = self.extractor.analyze("forget the color i like most")
        self.assertEqual(analysis.command.kind, MutationKind.DELETE)

    def test_delete_memory_about(self) -> None:
        analysis = self.extractor.analyze("delete the memory about project Apollo")
        self.assertEqual(analysis.command.kind, MutationKind.DELETE)
        self.assertEqual(analysis.command.key, "project Apollo")

    def test_remove_from_memory(self) -> None:
        analysis = self.extractor.analyze("remove my name from memory")
        self.assertEqual(analysis.command.kind, MutationKind.DELETE)
        self.assertEqual(analysis.command.key, "name")

    def test_contextual_delete(self) -> None:
        analysis = self.extractor.analyze("forget that")
        self.assertEqual(analysis.command.kind, MutationKind.DELETE)
        self.assertTrue(analysis.command.reference_previous)

    def test_delete_it_is_contextual(self) -> None:
        analysis = self.extractor.analyze("delete it")
        self.assertEqual(analysis.command.kind, MutationKind.DELETE)
        self.assertTrue(analysis.command.reference_previous)

    def test_forget_about_it_contextual(self) -> None:
        analysis = self.extractor.analyze("forget about it")
        self.assertEqual(analysis.command.kind, MutationKind.DELETE)
        self.assertTrue(analysis.command.reference_previous)

    def test_i_forgot_statement_is_not_delete(self) -> None:
        analysis = self.extractor.analyze("I forgot my umbrella")
        self.assertIsNone(analysis.command)


class DurableFactTests(unittest.TestCase):
    def setUp(self) -> None:
        self.extractor = Extractor()

    def test_my_key_is_value(self) -> None:
        analysis = self.extractor.analyze("my favorite color is violet")
        self.assertIsNotNone(analysis.durable_fact)
        self.assertEqual(analysis.durable_fact.category, "Preferences")
        self.assertEqual(analysis.durable_fact.value, "violet")

    def test_name(self) -> None:
        analysis = self.extractor.analyze("my name is Ada Lovelace")
        self.assertEqual(analysis.durable_fact.key, "name")
        self.assertEqual(analysis.durable_fact.value, "Ada Lovelace")

    def test_location(self) -> None:
        analysis = self.extractor.analyze("I live in Montreal")
        self.assertEqual(analysis.durable_fact.key, "location")
        self.assertEqual(analysis.durable_fact.value, "Montreal")

    def test_from(self) -> None:
        analysis = self.extractor.analyze("I'm from Paris")
        self.assertEqual(analysis.durable_fact.key, "location")
        self.assertEqual(analysis.durable_fact.value, "Paris")

    def test_device(self) -> None:
        analysis = self.extractor.analyze("I have an iPhone")
        self.assertEqual(analysis.durable_fact.key, "device")
        self.assertEqual(analysis.durable_fact.value, "iPhone")

    def test_handedness(self) -> None:
        analysis = self.extractor.analyze("I am left-handed")
        self.assertEqual(analysis.durable_fact.key, "handedness")
        self.assertEqual(analysis.durable_fact.value, "left-handed")

    def test_occupation(self) -> None:
        analysis = self.extractor.analyze("I work as a software engineer")
        self.assertEqual(analysis.durable_fact.key, "occupation")
        self.assertEqual(analysis.durable_fact.value, "a software engineer")

    def test_interest_regularly(self) -> None:
        analysis = self.extractor.analyze("I play Archero 2 regularly")
        self.assertEqual(analysis.durable_fact.key, "interest")
        self.assertEqual(analysis.durable_fact.value, "Archero 2")

    def test_love(self) -> None:
        analysis = self.extractor.analyze("I love hiking")
        self.assertEqual(analysis.durable_fact.key, "interest")
        self.assertEqual(analysis.durable_fact.value, "hiking")

    def test_project(self) -> None:
        analysis = self.extractor.analyze("I am building a memory system")
        self.assertEqual(analysis.durable_fact.key, "project")
        self.assertEqual(analysis.durable_fact.category, "Projects")

    def test_project_description(self) -> None:
        analysis = self.extractor.analyze("project Apollo is a lunar lander")
        self.assertEqual(analysis.durable_fact.key, "description")
        self.assertEqual(analysis.durable_fact.subject, "project Apollo")

    def test_current_activity_is_not_durable(self) -> None:
        analysis = self.extractor.analyze("I am playing Archero 2")
        self.assertIsNone(analysis.durable_fact)
        self.assertTrue(analysis.memory_related)
        self.assertEqual(analysis.temporary_fact.key, "current activity")
        self.assertEqual(analysis.temporary_fact.value, "Archero 2")

    def test_question_marker_blocks_fact(self) -> None:
        analysis = self.extractor.analyze("is my favorite color violet")
        self.assertIsNone(analysis.durable_fact)


class TemporaryFactTests(unittest.TestCase):
    def setUp(self) -> None:
        self.extractor = Extractor()

    def test_working_on(self) -> None:
        analysis = self.extractor.analyze("I am working on the sidebar")
        self.assertIsNotNone(analysis.temporary_fact)
        self.assertEqual(analysis.temporary_fact.key, "current project")
        self.assertEqual(analysis.temporary_fact.value, "the sidebar")

    def test_stuck_on(self) -> None:
        analysis = self.extractor.analyze("I'm stuck on this bug in main.py")
        self.assertEqual(analysis.temporary_fact.key, "issue")
        self.assertTrue(analysis.temporary_fact.unresolved)

    def test_plan(self) -> None:
        analysis = self.extractor.analyze("I am going to refactor the store")
        self.assertEqual(analysis.temporary_fact.key, "plan")
        self.assertFalse(analysis.temporary_fact.unresolved)


class NotMemoryRelatedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.extractor = Extractor()

    def test_plain_conversation(self) -> None:
        messages = [
            "Hello there",
            "what's the weather like today",
            "can you explain quantum computing",
            "that's funny",
            "thanks!",
            "tell me a joke",
        ]
        for message in messages:
            analysis = self.extractor.analyze(message)
            self.assertFalse(analysis.memory_related, message)
            self.assertIsNone(analysis.command)
            self.assertIsNone(analysis.durable_fact)
            self.assertIsNone(analysis.temporary_fact)

    def test_assistant_output_not_extracted(self) -> None:
        analysis = self.extractor.analyze("according to the article my favorite color is blue")
        self.assertIsNone(analysis.durable_fact)


class CleanValueTests(unittest.TestCase):
    def test_clean_value(self) -> None:
        self.assertEqual(clean_value("  violet!  "), "violet")
        self.assertEqual(clean_value("lemonade :)"), "lemonade")
        self.assertEqual(clean_value("  spaced   out  "), "spaced out")


if __name__ == "__main__":
    unittest.main()
