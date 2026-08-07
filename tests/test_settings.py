"""Settings overlay, theme tab, Memory tab placeholder, and click-outside-to-close tests."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QLabel

from ui.preferences import Preferences

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

    def test_memory_tab_remains_as_placeholder(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        window.show()
        window.open_settings_overlay()
        self.assertIn("Memory", window.settings_overlay.tab_pages)
        window.settings_overlay.select_tab("Memory")
        self.assertIs(
            window.settings_overlay.content_stack.currentWidget(),
            window.settings_overlay.tab_pages["Memory"],
        )
        labels = [label.text() for label in window.settings_overlay.tab_pages["Memory"].findChildren(QLabel)]
        self.assertTrue(any(text.startswith("This section is not implemented yet.") for text in labels))
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
