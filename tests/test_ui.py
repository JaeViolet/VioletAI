"""Lightweight tests that do not require a running Ollama server."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from config import SYSTEM_PROMPT  # noqa: E402
from conversation_store import ConversationStore  # noqa: E402
from main import MainWindow  # noqa: E402
from ollama_client import InvalidStreamError, OllamaWorker, iter_message_chunks  # noqa: E402
from widgets import AutoGrowingInput, MarkdownView, MessageBubble  # noqa: E402


class ChatFoundationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_markdown_message_splits_out_code_block(self) -> None:
        bubble = MessageBubble(
            "Text before.\n\n```python\nprint('hello')\n```\n\nText after.",
            "assistant",
        )
        self.assertEqual(bubble.layout.count(), 3)

    def test_long_assistant_message_grows_without_vertical_scrollbar(self) -> None:
        bubble = MessageBubble("# Heading\n\n" + "A wrapped assistant sentence. " * 120, "assistant")
        bubble.setFixedWidth(520)
        bubble.show()
        self.app.processEvents()
        markdown = bubble.findChild(MarkdownView)
        self.assertIsNotNone(markdown)
        assert markdown is not None
        self.assertGreater(markdown.height(), 100)
        self.assertEqual(markdown.verticalScrollBar().maximum(), 0)
        bubble.close()

    def test_enter_sends_and_shift_enter_adds_newline(self) -> None:
        editor = AutoGrowingInput()
        emissions: list[bool] = []
        editor.send_requested.connect(lambda: emissions.append(True))
        enter = QKeyEvent(
            QKeyEvent.Type.KeyPress,
            Qt.Key.Key_Return,
            Qt.KeyboardModifier.NoModifier,
        )
        editor.keyPressEvent(enter)
        self.assertEqual(emissions, [True])

        shifted_enter = QKeyEvent(
            QKeyEvent.Type.KeyPress,
            Qt.Key.Key_Return,
            Qt.KeyboardModifier.ShiftModifier,
        )
        editor.keyPressEvent(shifted_enter)
        self.assertEqual(editor.toPlainText(), "\n")

    def test_stream_parser_combines_chunks_and_rejects_bad_json(self) -> None:
        lines = [
            '{"message":{"content":"Hello"},"done":false}',
            '{"message":{"content":" there"},"done":true}',
        ]
        self.assertEqual(list(iter_message_chunks(lines)), [("Hello", False), (" there", True)])
        with self.assertRaises(InvalidStreamError):
            list(iter_message_chunks(["not-json"]))

    def test_conversation_persistence_round_trips_unicode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ConversationStore(Path(temp_dir))
            conversation = store.create(SYSTEM_PROMPT)
            conversation.messages.append({"role": "user", "content": "Hello, Violet ✨"})
            conversation.messages.append({"role": "assistant", "content": "Saved as UTF-8."})
            store.save(conversation)

            restored = store.load_latest()
            self.assertIsNotNone(restored)
            assert restored is not None
            self.assertEqual(restored.id, conversation.id)
            self.assertEqual(restored.messages[1]["content"], "Hello, Violet ✨")

    @patch("main.ConversationStore")
    def test_window_restores_latest_conversation(self, store_class: Mock) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ConversationStore(Path(temp_dir))
            conversation = store.create(SYSTEM_PROMPT)
            conversation.messages.append({"role": "user", "content": "Restore me"})
            store.save(conversation)
            store_class.return_value = store

            window = MainWindow()
            self.assertEqual(window.messages[-1]["content"], "Restore me")
            self.assertEqual(window.status.text(), "Ready")
            self.assertTrue(window.send_button.isEnabled())
            window.close()

    @patch("ollama_client.requests.post")
    def test_ollama_worker_combines_streamed_chunks(self, post: Mock) -> None:
        response = Mock()
        response.status_code = 200
        response.iter_lines.return_value = [
            '{"message":{"content":"Hello"},"done":false}',
            '{"message":{"content":" there"},"done":true}',
        ]
        post.return_value = response
        worker = OllamaWorker([{"role": "user", "content": "Hi"}])
        chunks: list[str] = []
        answers: list[str] = []
        worker.chunk_received.connect(chunks.append)
        worker.finished.connect(answers.append)

        worker.run()

        self.assertEqual(chunks, ["Hello", " there"])
        self.assertEqual(answers, ["Hello there"])
        response.close.assert_called_once()

    @patch("ollama_client.requests.post")
    def test_ollama_worker_cancellation_closes_response(self, post: Mock) -> None:
        response = Mock()
        response.status_code = 200
        response.iter_lines.return_value = ['{"message":{"content":"Hello"},"done":false}']
        post.return_value = response
        worker = OllamaWorker([{"role": "user", "content": "Hi"}])
        cancelled: list[bool] = []
        finished: list[str] = []
        worker.cancelled.connect(lambda: cancelled.append(True))
        worker.finished.connect(finished.append)

        worker.cancel()
        worker.run()

        self.assertEqual(cancelled, [True])
        self.assertEqual(finished, [])
        response.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
