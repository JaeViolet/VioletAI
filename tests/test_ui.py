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
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QToolButton, QWidget  # noqa: E402

from config import DEFAULT_MODEL_NAME, SYSTEM_PROMPT  # noqa: E402
from conversation_store import ConversationStore  # noqa: E402
from main import MainWindow  # noqa: E402
from ollama_client import InvalidStreamError, OllamaWorker, discover_models, iter_message_chunks  # noqa: E402
from sidebar import ChatSidebar  # noqa: E402
from widgets import AutoGrowingInput, MarkdownView, MessageBubble  # noqa: E402


class ChatFoundationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _window_with_temp_store(self) -> tuple[MainWindow, tempfile.TemporaryDirectory]:
        temp_dir = tempfile.TemporaryDirectory()
        store = ConversationStore(Path(temp_dir.name))
        patcher = patch("main.ConversationStore", return_value=store)
        refresh = patch.object(MainWindow, "_refresh_models")
        patcher.start()
        refresh.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(refresh.stop)
        self.addCleanup(temp_dir.cleanup)
        return MainWindow(), temp_dir

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
        enter = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)
        editor.keyPressEvent(enter)
        self.assertEqual(emissions, [True])

        shifted_enter = QKeyEvent(
            QKeyEvent.Type.KeyPress,
            Qt.Key.Key_Return,
            Qt.KeyboardModifier.ShiftModifier,
        )
        editor.keyPressEvent(shifted_enter)
        self.assertEqual(editor.toPlainText(), "\n")

    def test_prompt_history_navigation_preserves_draft(self) -> None:
        editor = AutoGrowingInput()
        editor.remember_prompt("first")
        editor.remember_prompt("second")
        editor.setPlainText("draft")
        up = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Up, Qt.KeyboardModifier.NoModifier)
        down = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Down, Qt.KeyboardModifier.NoModifier)
        editor.keyPressEvent(up)
        self.assertEqual(editor.toPlainText(), "second")
        editor.keyPressEvent(up)
        self.assertEqual(editor.toPlainText(), "first")
        editor.keyPressEvent(down)
        self.assertEqual(editor.toPlainText(), "second")
        editor.keyPressEvent(down)
        self.assertEqual(editor.toPlainText(), "draft")

    def test_stream_parser_combines_chunks_and_rejects_bad_json(self) -> None:
        lines = [
            '{"message":{"content":"Hello"},"done":false}',
            '{"message":{"content":" there"},"done":true}',
        ]
        self.assertEqual(list(iter_message_chunks(lines)), [("Hello", False), (" there", True)])
        with self.assertRaises(InvalidStreamError):
            list(iter_message_chunks(["not-json"]))

    def test_stream_parser_accepts_lines_as_they_arrive(self) -> None:
        lines = (line for line in ['{"message":{"content":"A"},"done":false}', '{"done":true}'])
        self.assertEqual(list(iter_message_chunks(lines)), [("A", False), ("", True)])

    def test_conversation_persistence_round_trips_unicode(self) -> None:
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

    def test_conversation_grouping_search_rename_and_delete(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ConversationStore(Path(temp_dir))
            conversation = store.create(SYSTEM_PROMPT)
            conversation.messages.append({"role": "user", "content": "Write Python code"})
            store.save(conversation)
            self.assertEqual(store.search("python")[0].id, conversation.id)
            self.assertTrue(any(item.id == conversation.id for item in store.grouped()["Today"]))
            renamed = store.rename(conversation.id, "Code notes")
            self.assertIsNotNone(renamed)
            assert renamed is not None
            self.assertEqual(renamed.title, "Code notes")
            self.assertTrue(store.delete(conversation.id))
            self.assertEqual(store.list_conversations(), [])

    def test_existing_conversation_file_compatibility_adds_title(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "old.json"
            path.write_text(
                '{"id":"old","created_at":"2026-08-02T00:00:00+00:00",'
                '"updated_at":"2026-08-02T00:00:00+00:00","messages":['
                '{"role":"system","content":"s"},{"role":"user","content":"First prompt here"}]}',
                encoding="utf-8",
            )
            conversation = ConversationStore(Path(temp_dir)).load(path)
            self.assertIsNotNone(conversation)
            assert conversation is not None
            self.assertEqual(conversation.title, "First prompt here")

    def test_window_starts_empty_without_deleting_saved_conversations(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ConversationStore(Path(temp_dir))
            conversation = store.create(SYSTEM_PROMPT)
            conversation.messages.append({"role": "user", "content": "Keep me in sidebar"})
            store.save(conversation)
            with patch("main.ConversationStore", return_value=store), patch.object(MainWindow, "_refresh_models"):
                window = MainWindow()
            self.assertEqual(len(window.messages), 1)
            self.assertEqual(window.messages[0]["role"], "system")
            self.assertEqual(store.load_by_id(conversation.id).messages[-1]["content"], "Keep me in sidebar")
            self.assertIn("VioletAI can make mistakes", window.footer_status.text())
            self.assertTrue(window.send_button.isEnabled())
            window.close()

    def test_empty_chat_branding_has_no_auto_assistant_message(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        labels = [label.text() for label in window.findChildren(QLabel)]
        self.assertEqual(len(window.messages), 1)
        self.assertEqual(window.messages[0]["role"], "system")
        self.assertEqual(window.windowTitle(), "VioletAI")
        self.assertIn("VioletAI", labels)
        window.close()

    def test_immediate_scroll_after_user_message(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        with patch.object(window, "_start_generation"):
            for index in range(20):
                window._add_message(f"old {index}", "user")
            self.app.processEvents()
            window.scroll_area.verticalScrollBar().setValue(0)
            window.input_box.setPlainText("Scroll now")
            window.send_message()
            self.app.processEvents()
            bar = window.scroll_area.verticalScrollBar()
            self.assertEqual(bar.value(), bar.maximum())
        window.close()

    def test_user_bubble_is_compact_and_right_aligned_in_content_column(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        bubble = window._add_message("short", "user")
        window.resize(1000, 700)
        window._resize_rows()
        self.app.processEvents()
        content = bubble.parentWidget().parentWidget()
        self.assertLess(bubble.width(), 220)
        self.assertEqual(bubble.geometry().right(), bubble.parentWidget().geometry().right())
        self.assertEqual(content.maximumWidth(), window.composer.maximumWidth())
        window.close()

    def test_auto_scroll_pauses_and_resumes_near_bottom(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        window.scroll_area.verticalScrollBar().setRange(0, 100)
        window.scroll_area.verticalScrollBar().setValue(0)
        window._handle_scroll_change(0)
        self.assertFalse(window._auto_scroll_enabled)
        window.scroll_area.verticalScrollBar().setValue(100)
        window._handle_scroll_change(100)
        self.assertTrue(window._auto_scroll_enabled)
        window.close()

    def test_auto_scroll_during_streaming_uses_bottom_when_enabled(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        window._auto_scroll_enabled = True
        window._receive_chunk("hello")
        self.app.processEvents()
        self.assertIsNotNone(window.pending_bubble)
        window.close()

    def test_sidebar_filters_conversations_live(self) -> None:
        sidebar = ChatSidebar()
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ConversationStore(Path(temp_dir))
            conversation = store.create(SYSTEM_PROMPT)
            conversation.messages.append({"role": "user", "content": "Find me"})
            store.save(conversation)
            sidebar.rebuild(store.grouped("find"), conversation.id)
            self.assertGreater(sidebar.list_layout.count(), 1)

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

    def test_sidebar_collapses_to_icon_rail(self) -> None:
        sidebar = ChatSidebar()
        sidebar.set_expanded(False)
        self.assertEqual(sidebar.minimumWidth(), sidebar.COLLAPSED_WIDTH)
        self.assertFalse(sidebar.brand_label.isVisible())
        self.assertFalse(sidebar.expanded_container.isVisible())
        self.assertFalse(sidebar.collapsed_container.isHidden())

    def test_conversation_rows_are_compact_without_permanent_action_buttons(self) -> None:
        sidebar = ChatSidebar()
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ConversationStore(Path(temp_dir))
            conversation = store.create(SYSTEM_PROMPT)
            conversation.messages.append({"role": "user", "content": "A very long row title that should be elided neatly"})
            store.save(conversation)
            sidebar.rebuild(store.grouped(), conversation.id)
        rows = sidebar.findChildren(QWidget, "conversationRow")
        self.assertEqual(rows[0].height(), 34)
        self.assertEqual(rows[0].findChildren(QToolButton), [])

    def test_search_overlay_filters_and_closes(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        window.show()
        window.open_search_overlay()
        self.assertTrue(window.search_overlay.isVisible())
        self.assertIs(window.search_overlay.parentWidget(), window.chat_panel)
        parent_rect = window.chat_panel.rect()
        self.assertAlmostEqual(
            window.search_overlay.geometry().center().x(),
            parent_rect.center().x(),
            delta=2,
        )
        window.search_overlay.close_overlay()
        self.assertFalse(window.search_overlay.isVisible())
        window.close()

    def test_delete_active_conversation_confirms_and_returns_to_empty_chat(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        window.messages.append({"role": "user", "content": "delete me"})
        window.store.save(window.conversation)
        deleted_id = window.conversation.id
        with patch("main.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            window.delete_conversation(deleted_id)
        self.assertIsNone(window.store.load_by_id(deleted_id))
        self.assertEqual(len(window.messages), 1)
        self.assertEqual(window.messages[0]["role"], "system")
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
        worker = OllamaWorker([{"role": "user", "content": "Hi"}], DEFAULT_MODEL_NAME)
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
        worker = OllamaWorker([{"role": "user", "content": "Hi"}], DEFAULT_MODEL_NAME)
        cancelled: list[bool] = []
        finished: list[str] = []
        worker.cancelled.connect(lambda: cancelled.append(True))
        worker.finished.connect(finished.append)
        worker.cancel()
        worker.run()
        self.assertEqual(cancelled, [True])
        self.assertEqual(finished, [])
        response.close.assert_called_once()

    @patch("ollama_client.requests.get")
    def test_model_discovery_reads_ollama_tags(self, get: Mock) -> None:
        response = Mock()
        response.json.return_value = {"models": [{"name": "a:1"}, {"name": "b:2"}]}
        get.return_value = response
        self.assertEqual(discover_models(), ["a:1", "b:2"])

    def test_model_switching_and_disabled_visible_during_generation(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        window.show()
        window._set_model_selector([DEFAULT_MODEL_NAME, "other:1"])
        window.model_selector.setCurrentText("other:1")
        self.assertEqual(window.active_model, "other:1")
        window._set_controls_generating(True)
        self.assertTrue(window.model_selector.isVisible())
        self.assertFalse(window.model_selector.isEnabled())
        window._set_controls_generating(False)
        self.assertTrue(window.model_selector.isEnabled())
        window.close()


if __name__ == "__main__":
    unittest.main()
