"""Pure-logic tests: stream parser, prompt assembly, conversation store."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from conversations.manager import ConversationStore
from core.config import SYSTEM_PROMPT
from core.prompts import build_ollama_messages
from models.ollama import InvalidStreamError, iter_message_chunks


class StreamParserTests(unittest.TestCase):
    def test_combines_chunks_and_rejects_bad_json(self) -> None:
        lines = [
            '{"message":{"content":"Hello"},"done":false}',
            '{"message":{"content":" there"},"done":true}',
        ]
        self.assertEqual(list(iter_message_chunks(lines)), [("Hello", False), (" there", True)])
        with self.assertRaises(InvalidStreamError):
            list(iter_message_chunks(["not-json"]))

    def test_accepts_lines_as_they_arrive(self) -> None:
        lines = (line for line in ['{"message":{"content":"A"},"done":false}', '{"done":true}'])
        self.assertEqual(list(iter_message_chunks(lines)), [("A", False), ("", True)])


class PromptAssemblyTests(unittest.TestCase):
    def test_prepends_system_and_drops_old_system(self) -> None:
        messages = [
            {"role": "system", "content": "old"},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
        ]
        assembled = build_ollama_messages(messages, "custom prompt")
        self.assertEqual(assembled, [
            {"role": "system", "content": "custom prompt"},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
        ])
        self.assertNotIn({"role": "system", "content": "old"}, assembled)


class ConversationStoreTests(unittest.TestCase):
    def test_persistence_round_trips_unicode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ConversationStore(Path(temp_dir))
            conversation = store.create(SYSTEM_PROMPT)
            conversation.messages.append({"role": "user", "content": "Hello, Violet"})
            conversation.messages.append({"role": "assistant", "content": "Saved as UTF-8."})
            store.save(conversation)
            restored = store.load_latest()
            self.assertIsNotNone(restored)
            assert restored is not None
            self.assertEqual(restored.id, conversation.id)
            self.assertEqual(restored.messages[1]["content"], "Hello, Violet")

    def test_empty_chats_are_not_saved(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ConversationStore(Path(temp_dir))
            conversation = store.create(SYSTEM_PROMPT)
            store.save(conversation)
            self.assertEqual(store.list_conversations(), [])

    def test_pinned_conversations_group_first(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ConversationStore(Path(temp_dir))
            conversation = store.create(SYSTEM_PROMPT)
            conversation.messages.append({"role": "user", "content": "Pin me"})
            store.save(conversation)
            store.set_pinned(conversation.id, True)
            groups = store.grouped()
            self.assertEqual(groups["Pinned"][0].id, conversation.id)


if __name__ == "__main__":
    unittest.main()
