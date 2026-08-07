"""Shared test harness: headless Qt bootstrap, temp stores, and UI wait helpers.

Importing anything from here also prepares the environment for the rest of the
test suite, so every domain test file can stay small and focused.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication  # noqa: E402

from conversations.manager import ConversationStore  # noqa: E402
from ui.window import MainWindow  # noqa: E402


class BaseWindowTests(unittest.TestCase):
    """Tests that drive a real MainWindow against temp stores in offscreen mode."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _window_with_temp_store(self) -> tuple[MainWindow, tempfile.TemporaryDirectory]:
        temp_dir = tempfile.TemporaryDirectory()
        store = ConversationStore(Path(temp_dir.name))
        patcher = patch("ui.window.ConversationStore", return_value=store)
        refresh = patch.object(MainWindow, "_refresh_models")
        patcher.start()
        refresh.start()
        self.addCleanup(patcher.stop)
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
