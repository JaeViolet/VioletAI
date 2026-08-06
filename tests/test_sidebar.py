"""Sidebar, conversation navigation, tools menu, and delete-confirmation tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QToolButton, QWidget

from core.config import SYSTEM_PROMPT
from conversations.manager import ConversationStore
from ui.sidebar import ChatSidebar
from ui.window import MainWindow

try:
    from tests.base import BaseWindowTests
except ImportError:  # direct execution: python tests/test_sidebar.py
    from base import BaseWindowTests


class SidebarWidgetTests(BaseWindowTests):
    def test_sidebar_filters_conversations_live(self) -> None:
        sidebar = ChatSidebar()
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ConversationStore(Path(temp_dir))
            conversation = store.create(SYSTEM_PROMPT)
            conversation.messages.append({"role": "user", "content": "Find me"})
            store.save(conversation)
            sidebar.rebuild(store.grouped("find"), conversation.id)
            self.assertGreater(sidebar.list_layout.count(), 1)

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


class ConversationNavigationTests(BaseWindowTests):
    def test_selecting_active_conversation_does_not_rebuild(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        first = window.store.create(SYSTEM_PROMPT)
        first.messages.append({"role": "user", "content": "Hello"})
        window.store.save(first)
        window.show()
        window.select_conversation(first.id)
        self._wait_until(lambda: not window.chat_view._rebuild_finish_active)
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
        self._wait_until(lambda: not window.chat_view._rebuild_finish_active)
        bar = window.chat_view.scroll_area.verticalScrollBar()
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
            self._wait_until(lambda: not window.chat_view._rebuild_finish_active)
            self.assertTrue(window.updatesEnabled())
            self.assertLessEqual(resize.call_count, 3)
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


class ToolsMenuTests(BaseWindowTests):
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


if __name__ == "__main__":
    unittest.main()
