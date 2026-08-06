"""Chat rendering, scrolling, streaming display, and PNG icon tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QLabel, QWidget

from core.config import SYSTEM_PROMPT
from conversations.manager import ConversationStore
from ui import design
from ui.design import PNG_CONTROL_ICON_SIZE, asset_icon_path, icon
from ui.widgets import CodeBlock, MarkdownView, MessageActions, MessageBubble
from ui.window import MainWindow

try:
    from tests.base import BaseWindowTests
except ImportError:  # direct execution: python tests/test_chat_view.py
    from base import BaseWindowTests


class MessageBubbleTests(BaseWindowTests):
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


class ChatWindowTests(BaseWindowTests):
    def test_window_starts_empty_without_deleting_saved_conversations(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ConversationStore(Path(temp_dir))
            conversation = store.create(SYSTEM_PROMPT)
            conversation.messages.append({"role": "user", "content": "Keep me in sidebar"})
            store.save(conversation)
            with patch("ui.window.ConversationStore", return_value=store), patch.object(MainWindow, "_refresh_models"):
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
            window.chat_view.scroll_area.verticalScrollBar().setValue(0)
            window.input_box.setPlainText("Scroll now")
            window.send_message()
            self.app.processEvents()
            bar = window.chat_view.scroll_area.verticalScrollBar()
            self.assertEqual(bar.value(), bar.maximum())
        window.close()

    def test_auto_scroll_pauses_and_resumes_near_bottom(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        window.chat_view.scroll_area.verticalScrollBar().setRange(0, 100)
        window.chat_view.scroll_area.verticalScrollBar().setValue(0)
        window.chat_view._handle_scroll_change(0)
        self.assertFalse(window.chat_view._auto_scroll_enabled)
        window.chat_view.scroll_area.verticalScrollBar().setValue(100)
        window.chat_view._handle_scroll_change(100)
        self.assertTrue(window.chat_view._auto_scroll_enabled)
        window.close()

    def test_auto_scroll_during_streaming_uses_bottom_when_enabled(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        window.chat_view._auto_scroll_enabled = True
        window._receive_chunk("hello")
        self.app.processEvents()
        self.assertIsNotNone(window.chat_view.pending_bubble)
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


class IconAssetTests(unittest.TestCase):
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


class CodeBlockTests(BaseWindowTests):
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


if __name__ == "__main__":
    unittest.main()
