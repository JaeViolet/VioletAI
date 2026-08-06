"""Composer input, prompt history, compact/multiline modes, and model selector tests."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QFrame

from core.config import DEFAULT_MODEL_NAME
from ui.design import Colors, PNG_CONTROL_ICON_SIZE
from ui.widgets import AutoGrowingInput

try:
    from tests.base import BaseWindowTests
except ImportError:  # direct execution: python tests/test_composer.py
    from base import BaseWindowTests


class InputWidgetTests(unittest.TestCase):
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

    def test_input_placeholder_and_cursor_are_vertically_centered(self) -> None:
        editor = AutoGrowingInput()
        editor.resize(500, editor.MIN_HEIGHT)
        editor._update_height()
        self.assertGreater(editor.document().documentMargin(), 0)
        editor.setPlainText("line one\nline two\nline three")
        self.assertGreater(editor.height(), editor.MIN_HEIGHT)


class ComposerModeTests(BaseWindowTests):
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


class ComposerStyleTests(BaseWindowTests):
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
