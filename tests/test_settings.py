"""Settings overlay, memory tab, theme tab, and click-outside-to-close tests."""

from __future__ import annotations

import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from memory.manager import CATEGORIES, MemoryRecord, utc_now
from ui.preferences import Preferences
from ui.settings import MemoryRow

try:
    from tests.base import BaseWindowTests
except ImportError:  # direct execution: python tests/test_settings.py
    from base import BaseWindowTests


class SettingsOverlayTests(BaseWindowTests):
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


class MemoryTabTests(BaseWindowTests):
    def _memory_record(self, **overrides) -> MemoryRecord:
        fields = {
            "id": uuid.uuid4().hex,
            "category": "Facts",
            "subject": "",
            "key": "",
            "value": "",
            "content": "",
            "created_at": utc_now(),
            "updated_at": utc_now(),
        }
        fields.update(overrides)
        return MemoryRecord(**fields)

    def _memory_rows(self, overlay) -> list[MemoryRow]:
        rows = []
        for index in range(overlay.rows_layout.count()):
            widget = overlay.rows_layout.itemAt(index).widget()
            if isinstance(widget, MemoryRow):
                rows.append(widget)
        return rows

    def test_memory_tab_lists_search_edit_archive_delete(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        window.show()
        store = window.settings_overlay.store
        record = store.save(
            self._memory_record(
                category="Preferences",
                subject="me",
                key="favorite color",
                value="purple",
                content="favorite color is purple",
            )
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
        self.assertTrue(store.search("favorite"))
        record.value = "blue"
        record.content = "favorite color is blue"
        edited = store.save(record)
        self.assertIsNotNone(edited)
        assert edited is not None
        self.assertEqual(edited.value, "blue")
        store.archive(record.id)
        self.assertNotIn(record.id, [memory.id for memory in store.search()])
        store.restore(record.id)
        self.assertIn(record.id, [memory.id for memory in store.search()])
        store.delete(record.id)
        self.assertNotIn(record.id, [memory.id for memory in store.search(include_archived=True)])
        window.close()

    def test_memory_tab_search_filters_rows_and_clear_all(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        window.show()
        store = window.settings_overlay.store
        store.save(self._memory_record(category="Facts", subject="sun", key="distance", value="93 million miles"))
        store.save(self._memory_record(category="People", subject="Alice", key="phone", value="555-0100"))
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
        self.assertEqual(store.search(), [])
        window.close()


class ThemeTabTests(BaseWindowTests):
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


if __name__ == "__main__":
    unittest.main()
