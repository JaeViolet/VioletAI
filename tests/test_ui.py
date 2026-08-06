"""Lightweight tests that do not require a running Ollama server."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from time import perf_counter
from unittest.mock import Mock, patch

import requests

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent, QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QToolButton, QWidget  # noqa: E402

from config import DEFAULT_MODEL_NAME, SYSTEM_PROMPT  # noqa: E402
from conversation_store import ConversationStore  # noqa: E402
import design  # noqa: E402
from design import Colors, PNG_CONTROL_ICON_SIZE, asset_icon_path, icon  # noqa: E402
from main import MainWindow  # noqa: E402
from memory_manager import MemoryRow  # noqa: E402
from memory_store import CATEGORIES, MemoryStore  # noqa: E402
from ollama_client import InvalidStreamError, OllamaWorker, discover_models, iter_message_chunks  # noqa: E402
from preferences import Preferences  # noqa: E402
from prompts import build_ollama_messages  # noqa: E402
from sidebar import ChatSidebar  # noqa: E402
from widgets import AutoGrowingInput, CodeBlock, MarkdownView, MessageActions, MessageBubble  # noqa: E402


class ChatFoundationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _window_with_temp_store(self) -> tuple[MainWindow, tempfile.TemporaryDirectory]:
        temp_dir = tempfile.TemporaryDirectory()
        store = ConversationStore(Path(temp_dir.name))
        memory_store = MemoryStore(Path(temp_dir.name) / "memory.db")
        patcher = patch("main.ConversationStore", return_value=store)
        memory_patcher = patch("main.MemoryStore", return_value=memory_store)
        refresh = patch.object(MainWindow, "_refresh_models")
        patcher.start()
        memory_patcher.start()
        refresh.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(memory_patcher.stop)
        self.addCleanup(refresh.stop)
        self.addCleanup(temp_dir.cleanup)
        return MainWindow(), temp_dir

    def _process_composer_events(self, window: MainWindow, cycles: int = 4) -> None:
        for _ in range(cycles):
            self.app.processEvents()
            window._update_composer_mode()

    def _wait_until(self, predicate, timeout_seconds: float = 3.0) -> None:
        deadline = perf_counter() + timeout_seconds
        while perf_counter() < deadline:
            self.app.processEvents()
            if predicate():
                return
        self.fail("Timed out waiting for asynchronous UI work.")

    def _composer_wrap_boundary_text(self, window: MainWindow) -> tuple[str, str]:
        width = window._stable_composer_text_width()
        previous = "word"
        for count in range(2, 240):
            text = " ".join(["word"] * count)
            window.input_box.setPlainText(text)
            metrics = window.input_box.measured_document_metrics(width)
            if int(metrics["visual_lines"]) > 1:
                return previous, text
            previous = text
        self.fail("Could not find composer wrap boundary text.")

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

    def test_build_ollama_messages_prepends_system_and_drops_old_system(self) -> None:
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

    # ------------------------------------------------------------------ chat foundation

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

    def test_rebuilding_long_conversation_batches_row_resizes(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        window.messages[:] = [{"role": "system", "content": SYSTEM_PROMPT}]
        for index in range(80):
            window.messages.append({"role": "user" if index % 2 == 0 else "assistant", "content": f"message {index}"})
        with patch.object(window, "_resize_rows", wraps=window._resize_rows) as resize_rows:
            window._rebuild_messages()
        self.assertEqual(resize_rows.call_count, 1)
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
        self.assertLessEqual(bubble.width(), int(window.composer.maximumWidth() * 2 / 3))
        self.assertEqual(bubble.width(), bubble.parentWidget().width())
        self.assertEqual(bubble.parentWidget().geometry().right(), content.width() - 1)
        self.assertEqual(content.maximumWidth(), window.composer.maximumWidth())
        self.assertIsNone(bubble.parentWidget().parentWidget().graphicsEffect())
        window.close()

    def test_user_bubble_expands_to_two_thirds_before_wrapping(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        text = "word " * 70
        bubble = window._add_message(text, "user")
        window.resize(1000, 700)
        window._resize_rows()
        self.app.processEvents()
        expected_max = int(window.composer.maximumWidth() * 2 / 3)
        self.assertEqual(bubble.width(), expected_max)
        window.close()

    def test_long_user_bubble_wraps_without_internal_scrollbar(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        text = "hello " * 90
        bubble = window._add_message(text, "user")
        window.resize(1000, 700)
        window._resize_rows()
        self.app.processEvents()
        markdown = bubble.findChild(MarkdownView)
        self.assertIsNotNone(markdown)
        assert markdown is not None
        self.assertLessEqual(bubble.width(), int(window.composer.maximumWidth() * 2 / 3))
        self.assertGreater(bubble.height(), 60)
        self.assertEqual(markdown.verticalScrollBar().maximum(), 0)
        window.close()

    def test_mixed_long_messages_do_not_overlap_or_clip(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        window.show()
        messages = [
            ("# Heading\n\n" + "Assistant markdown paragraph. " * 80, "assistant"),
            ("hello " * 120, "user"),
            ("- one\n- two\n- three\n\n| A | B |\n| - | - |\n| 1 | 2 |\n\n" + "More text. " * 60, "assistant"),
            ("```python\n" + "\n".join(f"print({index})" for index in range(30)) + "\n```", "assistant"),
        ]
        rows = []
        for text, role in messages:
            bubble = window._add_message(text, role)
            rows.append(bubble.parentWidget().parentWidget().parentWidget())
        window._resize_rows()
        self.app.processEvents()
        last_bottom = -1
        for row in rows:
            self.assertGreaterEqual(row.y(), last_bottom)
            self.assertGreater(row.height(), 0)
            last_bottom = row.geometry().bottom()
        for bubble in window.findChildren(MessageBubble):
            self.assertGreaterEqual(bubble.parentWidget().height(), bubble.height())
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
        layout = sidebar.collapsed_container.layout()
        self.assertIs(layout.itemAt(0).widget(), sidebar.collapsed_expand_button)
        self.assertIs(layout.itemAt(1).widget(), sidebar.collapsed_search_button)
        self.assertIs(layout.itemAt(2).widget(), sidebar.collapsed_new_chat_button)

    def test_conversation_rows_are_compact_without_permanent_action_buttons(self) -> None:
        sidebar = ChatSidebar()
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ConversationStore(Path(temp_dir))
            conversation = store.create(SYSTEM_PROMPT)
            conversation.messages.append({"role": "user", "content": "A very long row title that should be elided neatly"})
            store.save(conversation)
            sidebar.rebuild(store.grouped(), conversation.id)
            sidebar.resize(sidebar.EXPANDED_WIDTH, 500)
            sidebar.show()
            self.app.processEvents()
        rows = sidebar.findChildren(QWidget, "conversationRow")
        self.assertEqual(rows[0].height(), 34)
        self.assertEqual(rows[0].findChildren(QToolButton), [])
        self.assertEqual(rows[0].x(), 0)
        self.assertEqual(rows[0].width(), sidebar.new_chat_button.width())
        sidebar.close()

    def test_input_placeholder_and_cursor_are_vertically_centered(self) -> None:
        editor = AutoGrowingInput()
        editor.resize(500, editor.MIN_HEIGHT)
        editor._update_height()
        self.assertGreater(editor.document().documentMargin(), 0)
        editor.setPlainText("line one\nline two\nline three")
        self.assertGreater(editor.height(), editor.MIN_HEIGHT)

    def test_composer_switches_between_compact_and_multiline_layouts(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        window.show()
        self.assertFalse(window._composer_multiline)
        self.assertFalse(window.toolbar_widget.isVisible())
        self.assertEqual(window.input_row.itemAt(0).widget(), window.tools_button)
        self.assertEqual(window.input_row.itemAt(1).widget(), window.input_box)
        self.assertTrue(window.tools_button.isVisible())
        window.input_box.setPlainText("first paragraph\n\nsecond paragraph")
        self.app.processEvents()
        window._update_composer_mode()
        self.assertTrue(window._composer_multiline)
        self.assertTrue(window.toolbar_widget.isVisible())
        self.assertFalse(window.tools_button.isVisible())
        self.assertFalse(window.model_selector.isVisible())
        self.assertEqual(window.input_row.itemAt(1).widget(), window.input_box)
        self.assertEqual(window.toolbar_layout.itemAt(0).widget(), window.toolbar_tools_button)
        self.assertEqual(window.toolbar_layout.itemAt(window.toolbar_layout.count() - 3).widget(), window.toolbar_model_selector)
        window.input_box.setPlainText("short")
        self.app.processEvents()
        window._update_composer_mode()
        self.assertFalse(window._composer_multiline)
        window.close()

    def test_response_error_is_stage_specific_not_generic_unable_to_respond(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        window._receive_error("Ollama completed the stream before sending visible assistant text.")
        error_bubble = [bubble for bubble in window.findChildren(MessageBubble) if bubble.role == "error"][-1]
        self.assertIn("**Response failed**", error_bubble.text())
        self.assertIn("Stage: before first token", error_bubble.text())
        self.assertNotIn("Unable to respond", error_bubble.text())
        window.close()

    def test_partial_response_is_preserved_when_stream_later_fails(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        window._receive_chunk("Hello")
        window._receive_error("network dropped")
        self.assertIn("Hello", window.messages[-1]["content"])
        self.assertIn("_Response stopped._", window.messages[-1]["content"])
        error_bubble = [bubble for bubble in window.findChildren(MessageBubble) if bubble.role == "error"][-1]
        self.assertIn("**Generation stopped**", error_bubble.text())
        self.assertNotIn("Unable to respond", error_bubble.text())
        window.close()

    def test_composer_stays_compact_at_wrap_threshold(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        window.show()
        self.app.processEvents()
        threshold_text, _wrapped_text = self._composer_wrap_boundary_text(window)
        window._composer_layout_diagnostics.clear()
        window._composer_total_state_changes = 0
        window.input_box.setPlainText(threshold_text)
        self._process_composer_events(window, 8)
        self.assertFalse(window._composer_multiline)
        self.assertEqual(window._composer_total_state_changes, 0)
        self.assertTrue(window._composer_layout_diagnostics)
        last = window._composer_layout_diagnostics[-1]
        self.assertIn("document_height", last)
        self.assertIn("available_text_width", last)
        self.assertIn("target_height", last)
        self.assertEqual(last["requested_next_state"], "compact")
        window.close()

    def test_composer_expands_once_when_crossing_from_one_visual_line_to_two(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        window.show()
        self.app.processEvents()
        threshold_text, wrapped_text = self._composer_wrap_boundary_text(window)
        window.input_box.setPlainText(threshold_text)
        self._process_composer_events(window)
        window._composer_total_state_changes = 0
        window.input_box.setPlainText(wrapped_text)
        self._process_composer_events(window, 10)
        self.assertTrue(window._composer_multiline)
        self.assertEqual(window._composer_total_state_changes, 1)
        self.assertLessEqual(window._composer_state_changes_this_edit, 1)
        window.close()

    def test_composer_collapses_once_after_deleting_back_to_one_line(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        window.show()
        self.app.processEvents()
        threshold_text, wrapped_text = self._composer_wrap_boundary_text(window)
        window.input_box.setPlainText(wrapped_text)
        self._process_composer_events(window)
        self.assertTrue(window._composer_multiline)
        window._composer_total_state_changes = 0
        window.input_box.setPlainText(threshold_text)
        self._process_composer_events(window, 10)
        self.assertFalse(window._composer_multiline)
        self.assertEqual(window._composer_total_state_changes, 1)
        window.close()

    def test_composer_pasting_long_text_expands_without_recursive_toggling(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        window.show()
        self.app.processEvents()
        initial_width = window.composer.width()
        window._composer_total_state_changes = 0
        window.input_box.setPlainText(" ".join(["longtext"] * 120))
        self._process_composer_events(window, 12)
        self.assertTrue(window._composer_multiline)
        self.assertEqual(window._composer_total_state_changes, 1)
        self.assertEqual(window.composer.width(), initial_width)
        window.close()

    def test_composer_rapid_typing_near_threshold_does_not_loop(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        window.show()
        self.app.processEvents()
        threshold_text, wrapped_text = self._composer_wrap_boundary_text(window)
        initial_width = window.composer.width()
        window._composer_total_state_changes = 0
        for index in range(18):
            window.input_box.setPlainText(wrapped_text if index % 2 else threshold_text)
            self.app.processEvents()
        self._process_composer_events(window, 20)
        self.assertLessEqual(window._composer_total_state_changes, 18)
        self.assertLessEqual(window._composer_state_changes_this_edit, 1)
        self.assertFalse(window._composer_mode_timer.isActive())
        self.assertEqual(window.composer.width(), initial_width)
        window.close()

    def test_composer_same_text_does_not_toggle_on_repeated_geometry_updates(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        window.show()
        self.app.processEvents()
        _threshold_text, wrapped_text = self._composer_wrap_boundary_text(window)
        window.input_box.setPlainText(wrapped_text)
        self._process_composer_events(window)
        changes = window._composer_total_state_changes
        for _ in range(20):
            window._resize_rows()
            self.app.processEvents()
            window._update_composer_mode()
        self.assertEqual(window._composer_total_state_changes, changes)
        self.assertTrue(window._composer_multiline)
        window.close()

    def test_tools_menu_has_independent_placeholder_actions(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        actions = window.tools_button.menu().actions()
        self.assertEqual([action.data() for action in actions], [
            "Web Search",
            "Upload Files",
            "Upload Images",
            "Deep Research",
            "Image Generation",
        ])
        self.assertTrue(all(not action.isEnabled() for action in actions))
        window.close()

    def test_settings_overlay_opens_on_settings_tab(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        window.show()
        window.open_settings_overlay()
        self.assertTrue(window.settings_overlay.isVisible())
        self.assertIs(
            window.settings_overlay.content_stack.currentWidget(),
            window.settings_overlay.tab_pages["Settings"],
        )
        window.close()

    def test_settings_button_toggles_overlay_open_and_close(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        window.show()
        self.assertFalse(window.settings_overlay.isVisible())
        window.sidebar.settings_button.click()
        self._wait_until(lambda: window.settings_overlay.isVisible())
        window.sidebar.settings_button.click()
        self._wait_until(lambda: not window.settings_overlay.isVisible())
        window.sidebar.settings_button.click()
        self._wait_until(lambda: window.settings_overlay.isVisible())
        window.close()

    def test_settings_tabs_clickable_and_switch_pages(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        window.show()
        window.open_settings_overlay()
        self.assertTrue(all(button.isEnabled() for button in window.settings_overlay.tab_buttons))
        for name in window.settings_overlay.tab_pages:
            window.settings_overlay.select_tab(name)
            self.assertIs(
                window.settings_overlay.content_stack.currentWidget(),
                window.settings_overlay.tab_pages[name],
            )
        window.settings_overlay.tab_buttons[1].click()
        self.assertIs(
            window.settings_overlay.content_stack.currentWidget(),
            window.settings_overlay.tab_pages["Theme"],
        )
        window.close()

    def test_memory_tab_lists_search_edit_archive_delete(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        window.show()
        store = window.settings_overlay.store
        record = store.insert_memory(
            category="Preferences",
            subject="me",
            key="favorite color",
            value="purple",
            content="favorite color is purple",
        )
        window.open_settings_overlay()
        self.assertTrue(window.settings_overlay.isVisible())
        self.assertEqual(
            [
                window.settings_overlay.category_filter.itemText(index)
                for index in range(window.settings_overlay.category_filter.count())
            ],
            ["All", *CATEGORIES],
        )
        window.settings_overlay.search_input.setText("favorite")
        self.app.processEvents()
        self.assertTrue(store.search_memories("favorite"))
        edited = store.update_memory(record.id, value="blue", content="favorite color is blue", manual=True)
        self.assertIsNotNone(edited)
        assert edited is not None
        self.assertEqual(edited.value, "blue")
        store.archive_memory(record.id)
        self.assertNotIn(record.id, [memory.id for memory in store.list_memories()])
        store.restore_memory(record.id)
        self.assertIn(record.id, [memory.id for memory in store.list_memories()])
        store.delete_memory(record.id)
        self.assertNotIn(record.id, [memory.id for memory in store.list_memories(include_archived=True)])
        window.close()

    def test_memory_tab_search_filters_rows_and_clear_all(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        window.show()
        store = window.settings_overlay.store
        store.insert_memory(category="Facts", subject="sun", key="distance", value="93 million miles")
        store.insert_memory(category="People", subject="Alice", key="phone", value="555-0100")
        window.open_settings_overlay()
        window.settings_overlay.select_tab("Memory")
        self.app.processEvents()
        self.assertEqual(len(self._memory_rows(window.settings_overlay)), 2)
        window.settings_overlay.search_input.setText("sun")
        self.app.processEvents()
        self.assertEqual(len(self._memory_rows(window.settings_overlay)), 1)
        window.settings_overlay.search_input.setText("")
        self.app.processEvents()
        window.settings_overlay.clear_all()
        self.assertTrue(window.settings_overlay.confirm_panel.isVisible())
        window.settings_overlay._confirmed_clear_all()
        self.app.processEvents()
        self.assertEqual(store.list_memories(), [])
        window.close()

    def _memory_rows(self, overlay) -> list[MemoryRow]:
        rows = []
        for index in range(overlay.rows_layout.count()):
            widget = overlay.rows_layout.itemAt(index).widget()
            if isinstance(widget, MemoryRow):
                rows.append(widget)
        return rows

    def test_theme_tab_applies_presets_and_manages_custom_presets(self) -> None:
        window, temp_dir = self._window_with_temp_store()
        window.show()
        window.preferences.path = Path(temp_dir.name) / "prefs.json"
        window.preferences.theme_name = "Violet"
        window.preferences.theme_accent = "#8b5cf6"
        window.preferences.custom_themes = []
        window.preferences.save()
        window.open_settings_overlay()
        window.settings_overlay.select_tab("Theme")
        self.assertIs(
            window.settings_overlay.content_stack.currentWidget(),
            window.settings_overlay.tab_pages["Theme"],
        )
        window.settings_overlay._apply_theme("Ocean")
        self.assertEqual(window.preferences.theme_name, "Ocean")
        self.assertEqual(window.preferences.theme_accent, "#0ea5e9")
        window.settings_overlay._set_custom_accent("#112233")
        self.assertEqual(window.preferences.theme_name, "Custom")
        self.assertEqual(window.preferences.theme_accent, "#112233")
        window.settings_overlay.preset_name_input.setText("My Theme")
        window.settings_overlay._save_preset()
        self.assertIn({"name": "My Theme", "accent": "#112233"}, window.preferences.custom_themes)
        self.assertEqual(window.preferences.theme_name, "My Theme")
        window.settings_overlay._delete_preset("My Theme")
        self.assertNotIn({"name": "My Theme", "accent": "#112233"}, window.preferences.custom_themes)
        self.assertEqual(window.preferences.theme_name, "Violet")
        self.assertEqual(window.preferences.theme_accent, "#8b5cf6")
        reloaded = Preferences()
        reloaded.path = Path(temp_dir.name) / "prefs.json"
        reloaded.load()
        self.assertEqual(reloaded.theme_name, "Violet")
        self.assertEqual(reloaded.theme_accent, "#8b5cf6")
        window.close()

    def test_settings_overlay_closes_on_outside_click_but_not_inside(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        window.show()
        window.open_settings_overlay()
        self.assertTrue(window.settings_overlay.isVisible())
        rect = window.settings_overlay.geometry()
        inside = QPointF(rect.center().x(), rect.center().y())
        inside_event = QMouseEvent(
            QEvent.Type.MouseButtonPress, inside, inside,
            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(window.chat_panel, inside_event)
        self.assertTrue(window.settings_overlay.isVisible())
        outside = QPointF(rect.center().x(), 0)
        outside_event = QMouseEvent(
            QEvent.Type.MouseButtonPress, outside, outside,
            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(window.chat_panel, outside_event)
        self._wait_until(lambda: not window.settings_overlay.isVisible())
        window.close()

    def test_sidebar_pinned_section_fixed_above_scroll_list(self) -> None:
        sidebar = ChatSidebar()
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ConversationStore(Path(temp_dir))
            pinned = store.create(SYSTEM_PROMPT)
            pinned.messages.append({"role": "user", "content": "Pinned one"})
            store.save(pinned)
            store.set_pinned(pinned.id, True)
            normal = store.create(SYSTEM_PROMPT)
            normal.messages.append({"role": "user", "content": "Normal one"})
            store.save(normal)
            sidebar.rebuild(store.grouped(), normal.id)

            def rows_in(layout) -> list:
                rows = []
                for index in range(layout.count()):
                    widget = layout.itemAt(index).widget()
                    if widget is not None and widget.objectName() == "conversationRow":
                        rows.append(widget)
                return rows

            pinned_rows = rows_in(sidebar.pinned_layout)
            list_rows = rows_in(sidebar.list_layout)
            self.assertEqual(len(pinned_rows), 1)
            self.assertEqual(pinned_rows[0].conversation.id, pinned.id)
            self.assertEqual(len(list_rows), 1)
            self.assertEqual(list_rows[0].conversation.id, normal.id)
            self.assertNotIn(pinned.id, [row.conversation.id for row in list_rows])
        sidebar.close()

    def test_set_active_moves_active_property_and_title_across_rows(self) -> None:
        sidebar = ChatSidebar()
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ConversationStore(Path(temp_dir))
            a = store.create(SYSTEM_PROMPT)
            store.rename(a.id, "Alpha")
            a.messages.append({"role": "user", "content": "a"})
            store.save(a)
            b = store.create(SYSTEM_PROMPT)
            store.rename(b.id, "Beta")
            b.messages.append({"role": "user", "content": "b"})
            store.save(b)
            sidebar.rebuild(store.grouped(), a.id)

            def rows() -> list:
                found = []
                for layout in (sidebar.pinned_layout, sidebar.list_layout):
                    for index in range(layout.count()):
                        widget = layout.itemAt(index).widget()
                        if widget is not None and widget.objectName() == "conversationRow":
                            found.append(widget)
                return found

            sidebar.set_active(b.id)
            all_rows = rows()
            active_rows = [row for row in all_rows if row.property("active")]
            self.assertEqual(len(active_rows), 1)
            self.assertEqual(active_rows[0].conversation.id, b.id)
            self.assertEqual(active_rows[0].title.property("active"), True)
            previous = [row for row in all_rows if row.conversation.id == a.id][0]
            self.assertEqual(previous.property("active"), False)
            self.assertEqual(previous.title.property("active"), False)
        sidebar.close()

    def test_selecting_active_conversation_does_not_rebuild(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        first = window.store.create(SYSTEM_PROMPT)
        first.messages.append({"role": "user", "content": "Hello"})
        window.store.save(first)
        window.show()
        window.select_conversation(first.id)
        self._wait_until(lambda: not window._rebuild_finish_active)
        with patch.object(MainWindow, "_rebuild_messages") as rebuild:
            window.select_conversation(first.id)
            rebuild.assert_not_called()
        window.close()

    def test_selecting_different_conversation_lands_at_bottom(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        conversation = window.store.create(SYSTEM_PROMPT)
        conversation.messages.append({"role": "user", "content": "Prompt"})
        conversation.messages.append({"role": "assistant", "content": "Answer. " * 400})
        window.store.save(conversation)
        window.show()
        window.select_conversation(conversation.id)
        self._wait_until(lambda: not window._rebuild_finish_active)
        bar = window.scroll_area.verticalScrollBar()
        self.assertEqual(bar.value(), bar.maximum())
        self.assertTrue(window.updatesEnabled())
        window.close()

    def test_rebuild_reenables_updates_and_does_not_loop_resize(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        conversation = window.store.create(SYSTEM_PROMPT)
        conversation.messages.append({"role": "user", "content": "Prompt"})
        conversation.messages.append({"role": "assistant", "content": "Answer. " * 400})
        window.store.save(conversation)
        window.show()
        with patch.object(MainWindow, "_resize_rows", wraps=window._resize_rows) as resize:
            window.select_conversation(conversation.id)
            self._wait_until(lambda: not window._rebuild_finish_active)
            self.assertTrue(window.updatesEnabled())
            self.assertLessEqual(resize.call_count, 3)
        window.close()

    def test_send_and_stop_buttons_have_identical_larger_geometry(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        window.show()
        self.assertEqual(window.send_button.minimumWidth(), 38)
        self.assertEqual(window.send_button.minimumHeight(), 38)
        self.assertEqual(window.send_button.iconSize().width(), PNG_CONTROL_ICON_SIZE)
        self.assertEqual(window.stop_button.iconSize().width(), PNG_CONTROL_ICON_SIZE)
        self.assertEqual(window.stop_button.minimumWidth(), window.send_button.minimumWidth())
        self.assertEqual(window.stop_button.minimumHeight(), window.send_button.minimumHeight())
        self.assertEqual(window.toolbar_send_button.minimumWidth(), window.send_button.minimumWidth())
        self.assertEqual(window.toolbar_stop_button.minimumHeight(), window.send_button.minimumHeight())
        window.close()

    def test_model_selectors_keep_consistent_geometry_across_composer_modes(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        window.show()
        window._set_model_selector([DEFAULT_MODEL_NAME, "qwen3.5:9b"])
        compact_metrics = (
            window.model_selector.minimumWidth(),
            window.model_selector.maximumWidth(),
            window.model_selector.height(),
            window.model_selector.font().pointSize(),
        )
        window.input_box.setPlainText("line one\nline two\nline three")
        self.app.processEvents()
        window._update_composer_mode()
        expanded_metrics = (
            window.toolbar_model_selector.minimumWidth(),
            window.toolbar_model_selector.maximumWidth(),
            window.toolbar_model_selector.height(),
            window.toolbar_model_selector.font().pointSize(),
        )
        self.assertEqual(compact_metrics, expanded_metrics)
        window.close()

    def test_composer_keeps_pill_radius_and_styled_background(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        window.show()
        self.assertTrue(window.composer.testAttribute(Qt.WidgetAttribute.WA_StyledBackground))
        self.assertEqual(window.composer.frameShape(), QFrame.Shape.NoFrame)
        self.assertTrue(window.composer.property("compact"))
        self.assertIn('border-radius: 25px', window.styleSheet())
        window.input_box.setPlainText("one\ntwo\nthree\nfour")
        self.app.processEvents()
        window._update_composer_mode()
        self.assertFalse(window.composer.property("compact"))
        self.assertTrue(window.composer.testAttribute(Qt.WidgetAttribute.WA_StyledBackground))
        self.assertIn("border-radius: 28px", window.styleSheet())
        window.input_box.setPlainText("one")
        self.app.processEvents()
        window._update_composer_mode()
        self.assertTrue(window.composer.property("compact"))
        window.close()

    def test_png_icon_assets_resolve_and_render_at_multiple_sizes(self) -> None:
        for name in ("copy", "regen", "send", "stop"):
            self.assertTrue(asset_icon_path(name).exists())
            for size in (16, 18, 21, 28):
                rendered = icon(name, "white", size)
                self.assertFalse(rendered.isNull())
                pixmap = rendered.pixmap(size, size)
                self.assertFalse(pixmap.isNull(), f"{name} at {size}px did not render")

    def test_missing_png_icon_asset_fails_gracefully(self) -> None:
        with patch.object(design, "ICON_ASSETS_DIR", Path("missing-assets")):
            missing_icon = design.icon("send", "white", 21)
        self.assertTrue(missing_icon.isNull())

    def test_message_action_icons_use_png_assets(self) -> None:
        actions = MessageActions()
        self.assertFalse(actions.copy_button.icon().isNull())
        self.assertFalse(actions.regenerate_button.icon().isNull())
        self.assertEqual(actions.copy_button.iconSize().width(), PNG_CONTROL_ICON_SIZE)
        self.assertEqual(actions.regenerate_button.iconSize().width(), PNG_CONTROL_ICON_SIZE)

    def test_model_selector_chevron_visible_in_compact_expanded_and_disabled(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        window.show()
        self.assertEqual(window.model_selector.arrow_color(), Colors.TEXT_MUTED)
        window.input_box.setPlainText("line one\nline two\nline three")
        self.app.processEvents()
        window._update_composer_mode()
        self.assertEqual(window.toolbar_model_selector.arrow_color(), Colors.TEXT_MUTED)
        window._set_controls_generating(True)
        self.assertEqual(window.toolbar_model_selector.arrow_color(), Colors.TEXT_FAINT)
        window._set_controls_generating(False)
        window.input_box.setPlainText("one")
        self.app.processEvents()
        window._update_composer_mode()
        window._set_controls_generating(True)
        self.assertEqual(window.model_selector.arrow_color(), Colors.TEXT_FAINT)
        window.close()

    def test_composer_width_only_changes_on_window_resize(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        window.resize(1180, 820)
        window.show()
        self.app.processEvents()
        window._resize_rows()
        initial_width = window.composer.width()
        window.input_box.setPlainText("short single line")
        self.app.processEvents()
        window._update_composer_mode()
        self.assertEqual(window.composer.width(), initial_width)
        window.input_box.setPlainText("one\ntwo\nthree\nfour")
        self.app.processEvents()
        window._update_composer_mode()
        self.assertEqual(window.composer.width(), initial_width)
        window.input_box.setPlainText("short")
        self.app.processEvents()
        window._update_composer_mode()
        self.assertEqual(window.composer.width(), initial_width)
        window._set_controls_generating(True)
        self.assertEqual(window.composer.width(), initial_width)
        window._set_controls_generating(False)
        self.assertEqual(window.composer.width(), initial_width)
        window.resize(900, 700)
        self.app.processEvents()
        window._resize_rows()
        self.assertNotEqual(window.composer.width(), initial_width)
        window.close()

    def test_welcome_content_centers_to_composer_column_after_sidebar_and_resize(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        window.resize(1180, 820)
        window.show()
        self.app.processEvents()
        window._resize_rows()

        def centers() -> tuple[int, int]:
            welcome_column = window.findChild(QWidget, "welcomeContentColumn")
            self.assertIsNotNone(welcome_column)
            assert welcome_column is not None
            composer_center = window.composer.mapTo(window, window.composer.rect().center()).x()
            welcome_center = welcome_column.mapTo(window, welcome_column.rect().center()).x()
            return composer_center, welcome_center

        composer_center, welcome_center = centers()
        self.assertAlmostEqual(composer_center, welcome_center, delta=2)
        window.sidebar.set_expanded(False)
        self.app.processEvents()
        window._resize_rows()
        composer_center, welcome_center = centers()
        self.assertAlmostEqual(composer_center, welcome_center, delta=2)
        window.sidebar.set_expanded(True)
        window.resize(960, 720)
        self.app.processEvents()
        window._resize_rows()
        composer_center, welcome_center = centers()
        self.assertAlmostEqual(composer_center, welcome_center, delta=2)
        window.close()

    def test_code_blocks_have_no_internal_scrollbars_and_contribute_height(self) -> None:
        code = "def demo():\n" + "\n".join("    print('hello world')" for _ in range(30))
        block = CodeBlock("python", code)
        block.resize(420, 100)
        block.show()
        self.app.processEvents()
        self.assertEqual(block.editor.verticalScrollBar().maximum(), 0)
        self.assertEqual(block.editor.horizontalScrollBar().maximum(), 0)
        self.assertGreater(block.height(), 300)
        block.close()

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
        window.show()
        window.messages.append({"role": "user", "content": "delete me"})
        window.store.save(window.conversation)
        deleted_id = window.conversation.id
        window.delete_conversation(deleted_id)
        self.assertTrue(window.confirm_overlay.isVisible())
        window.confirm_overlay.confirm_button.click()
        self.assertFalse(window.confirm_overlay.isVisible())
        self.assertIsNone(window.store.load_by_id(deleted_id))
        self.assertEqual(len(window.messages), 1)
        self.assertEqual(window.messages[0]["role"], "system")
        window.close()

    # ------------------------------------------------------------------ ollama client

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
        self.assertNotIn("think", post.call_args.kwargs["json"])
        response.close.assert_called_once()

    @patch("ollama_client.requests.post")
    def test_ollama_worker_many_empty_chunks_then_valid_content(self, post: Mock) -> None:
        response = Mock()
        response.status_code = 200
        response.iter_lines.return_value = [
            "",
            '{"message":{"content":""},"done":false}',
            '{"message":{"content":"Visible"},"done":true}',
        ]
        post.return_value = response
        worker = OllamaWorker([{"role": "user", "content": "Hi"}], DEFAULT_MODEL_NAME)
        answers: list[str] = []
        worker.finished.connect(answers.append)
        worker.run()
        self.assertEqual(answers, ["Visible"])
        response.close.assert_called_once()

    @patch("ollama_client.requests.post")
    def test_ollama_worker_http_200_empty_stream_reports_specific_failure(self, post: Mock) -> None:
        response = Mock()
        response.status_code = 200
        response.iter_lines.return_value = []
        post.return_value = response
        worker = OllamaWorker([{"role": "user", "content": "Hi"}], DEFAULT_MODEL_NAME)
        failures: list[str] = []
        worker.failed.connect(failures.append)
        worker.run()
        self.assertEqual(failures, ["Ollama returned no stream events before the response ended."])
        response.close.assert_called_once()

    @patch("ollama_client.requests.post")
    def test_ollama_worker_done_event_with_no_content_reports_empty_visible_text(self, post: Mock) -> None:
        response = Mock()
        response.status_code = 200
        response.iter_lines.return_value = ['{"message":{"content":""},"done":true}']
        post.return_value = response
        worker = OllamaWorker([{"role": "user", "content": "Hi"}], DEFAULT_MODEL_NAME)
        failures: list[str] = []
        worker.failed.connect(failures.append)
        worker.run()
        self.assertEqual(
            failures,
            ["Ollama completed the stream before sending visible assistant text (events=1, empty_events=1, done=True)."],
        )
        response.close.assert_called_once()

    @patch("ollama_client.requests.post")
    def test_ollama_worker_partial_content_followed_by_error_preserves_chunk_signal(self, post: Mock) -> None:
        response = Mock()
        response.status_code = 200
        response.iter_lines.return_value = [
            '{"message":{"content":"Hello"},"done":false}',
            '{"error":"stream broke"}',
        ]
        post.return_value = response
        worker = OllamaWorker([{"role": "user", "content": "Hi"}], DEFAULT_MODEL_NAME)
        chunks: list[str] = []
        failures: list[str] = []
        worker.chunk_received.connect(chunks.append)
        worker.failed.connect(failures.append)
        worker.run()
        self.assertEqual(chunks, ["Hello"])
        self.assertEqual(failures, ["stream broke"])
        response.close.assert_called_once()

    @patch("ollama_client.requests.post")
    def test_ollama_worker_timeout_before_first_token_is_stage_specific(self, post: Mock) -> None:
        post.side_effect = requests.Timeout()
        worker = OllamaWorker([{"role": "user", "content": "Hi"}], DEFAULT_MODEL_NAME, read_timeout_seconds=1)
        failures: list[str] = []
        worker.failed.connect(failures.append)
        worker.run()
        self.assertEqual(failures, ["Ollama request timed out before first event after 1 seconds."])

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

    @patch("ollama_client.requests.post")
    def test_ollama_worker_cancellation_after_partial_output(self, post: Mock) -> None:
        response = Mock()
        response.status_code = 200
        response.iter_lines.return_value = [
            '{"message":{"content":"Hello"},"done":false}',
            '{"message":{"content":" there"},"done":true}',
        ]
        post.return_value = response
        worker = OllamaWorker([{"role": "user", "content": "Hi"}], DEFAULT_MODEL_NAME)
        chunks: list[str] = []
        cancelled: list[bool] = []
        finished: list[str] = []
        worker.chunk_received.connect(chunks.append)
        worker.chunk_received.connect(lambda _chunk: worker.cancel())
        worker.cancelled.connect(lambda: cancelled.append(True))
        worker.finished.connect(finished.append)
        worker.run()
        self.assertEqual(chunks, ["Hello"])
        self.assertEqual(cancelled, [True])
        self.assertEqual(finished, [])
        response.close.assert_called()

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
        self.assertEqual(window.toolbar_model_selector.currentText(), "other:1")
        window._set_controls_generating(True)
        self.assertTrue(window.model_selector.isVisible())
        self.assertFalse(window.model_selector.isEnabled())
        self.assertFalse(window.toolbar_model_selector.isEnabled())
        window._set_controls_generating(False)
        self.assertTrue(window.model_selector.isEnabled())
        self.assertTrue(window.toolbar_model_selector.isEnabled())
        window.close()


if __name__ == "__main__":
    unittest.main()
