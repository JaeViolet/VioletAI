"""Native Memory Manager UI for VioletAI settings."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from design import Colors, icon
from memory_models import CATEGORIES, MemoryRecord
from memory_store import MemoryStore
from preferences import MEMORY_MODES, Preferences
from widgets import apply_interaction_cursors


class SettingsOverlay(QFrame):
    closed = Signal()

    def __init__(self, store: MemoryStore, preferences: Preferences | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.store = store
        self.preferences = preferences
        self.setObjectName("searchOverlay")
        self.setWindowFlags(Qt.WindowType.Widget)
        self.setMaximumSize(860, 580)
        self.resize(860, 580)

        root = QHBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(14)

        self.tabs_scroll = QScrollArea()
        self.tabs_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.tabs_scroll.setWidgetResizable(True)
        self.tabs_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tabs_scroll.setFixedWidth(180)
        tabs_widget = QWidget()
        self.tabs_layout = QVBoxLayout(tabs_widget)
        self.tabs_layout.setContentsMargins(0, 0, 0, 0)
        self.tabs_layout.setSpacing(6)
        for name, icon_name, enabled in [
            ("Settings", "settings", True),
            ("Theme", "theme", True),
            ("Memory", "memory", True),
            ("Tools", "new", False),
            ("Web Search", "search", False),
            ("Files", "copy", False),
            ("Images", "copy", False),
            ("Voice", "new", False),
            ("Models", "new", False),
            ("Automation", "regen", False),
            ("Plugins", "new", False),
        ]:
            button = QPushButton(name)
            button.setIcon(icon(icon_name))
            button.setEnabled(enabled)
            button.setObjectName("sidebarNewChat")
            self.tabs_layout.addWidget(button)
        self.tabs_layout.addStretch()
        self.tabs_scroll.setWidget(tabs_widget)
        root.addWidget(self.tabs_scroll)

        self.content_scroll = QScrollArea()
        self.content_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.content_scroll.setWidgetResizable(True)
        content = QWidget()
        self.content_layout = QVBoxLayout(content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(10)
        root.addWidget(self.content_scroll, 1)
        self.content_scroll.setWidget(content)

        top = QHBoxLayout()
        title = QLabel("Memory")
        title.setObjectName("welcomeTitle")
        close = QToolButton(objectName="sidebarIconButton")
        close.setIcon(icon("close"))
        close.clicked.connect(self.close_overlay)
        top.addWidget(title)
        top.addStretch()
        top.addWidget(close)
        self.content_layout.addLayout(top)

        controls = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setObjectName("overlaySearchInput")
        self.search_input.setPlaceholderText("Search memories")
        self.category_filter = QComboBox()
        self.category_filter.setObjectName("modelSelector")
        self.category_filter.addItems(["All", *CATEGORIES])
        self.include_archived = QComboBox()
        self.include_archived.setObjectName("modelSelector")
        self.include_archived.addItems(["Active", "All"])
        self.sort_order = QComboBox()
        self.sort_order.setObjectName("modelSelector")
        self.sort_order.addItems(["Updated", "Created", "Category", "Accessed"])
        self.memory_mode = QComboBox()
        self.memory_mode.setObjectName("modelSelector")
        self.memory_mode.addItems(MEMORY_MODES)
        self.diagnostics_enabled = QCheckBox("Enable Memory Diagnostics")
        self.diagnostics_enabled.setObjectName("welcomeSubtitle")
        if self.preferences is not None:
            self.memory_mode.setCurrentText(self.preferences.memory_mode)
            self.diagnostics_enabled.setChecked(self.preferences.memory_diagnostics)
        clear = QPushButton("Clear all")
        clear.setObjectName("sidebarNewChat")
        clear.clicked.connect(self.clear_all)
        controls.addWidget(self.search_input, 1)
        controls.addWidget(self.category_filter)
        controls.addWidget(self.include_archived)
        controls.addWidget(self.sort_order)
        controls.addWidget(self.memory_mode)
        controls.addWidget(clear)
        self.content_layout.addLayout(controls)
        self.content_layout.addWidget(self.diagnostics_enabled)

        self.rows = QWidget()
        self.rows_layout = QVBoxLayout(self.rows)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(8)
        self.rows_layout.addStretch()
        self.content_layout.addWidget(self.rows)

        self.search_input.textChanged.connect(self.refresh)
        self.category_filter.currentTextChanged.connect(self.refresh)
        self.include_archived.currentTextChanged.connect(self.refresh)
        self.sort_order.currentTextChanged.connect(self.refresh)
        self.memory_mode.currentTextChanged.connect(self._save_memory_mode)
        self.diagnostics_enabled.toggled.connect(self._save_diagnostics_enabled)
        apply_interaction_cursors(self)

    def show_overlay(self) -> None:
        if self.parentWidget() is not None:
            parent_rect = self.parentWidget().rect()
            width = min(860, max(640, parent_rect.width() - 120))
            height = min(580, max(440, parent_rect.height() - 120))
            self.resize(width, height)
            self.move(
                (parent_rect.width() - width) // 2,
                (parent_rect.height() - height) // 2,
            )
        self.refresh()
        self.show()
        self.raise_()

    def close_overlay(self) -> None:
        self.hide()
        self.closed.emit()

    def refresh(self) -> None:
        while self.rows_layout.count() > 1:
            item = self.rows_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        records = self.store.search(
            self.search_input.text().strip(),
            self.category_filter.currentText(),
            include_archived=self.include_archived.currentText() == "All",
        )
        records = self._sort_records(records)
        for record in records:
            self.rows_layout.insertWidget(self.rows_layout.count() - 1, MemoryRow(record, self.store, self.refresh))
        if not records:
            empty = QLabel("No memories found.")
            empty.setObjectName("welcomeSubtitle")
            self.rows_layout.insertWidget(0, empty)
        apply_interaction_cursors(self)

    def _sort_records(self, records: list[MemoryRecord]) -> list[MemoryRecord]:
        order = self.sort_order.currentText()
        if order == "Created":
            return sorted(records, key=lambda record: record.created_at, reverse=True)
        if order == "Category":
            return sorted(records, key=lambda record: (record.category, record.subject, record.key))
        if order == "Accessed":
            return sorted(records, key=lambda record: (record.last_accessed_at or "", record.access_count), reverse=True)
        return sorted(records, key=lambda record: record.updated_at, reverse=True)

    def _save_memory_mode(self, mode: str) -> None:
        if self.preferences is None or mode not in MEMORY_MODES:
            return
        self.preferences.memory_mode = mode
        self.preferences.save()

    def _save_diagnostics_enabled(self, enabled: bool) -> None:
        if self.preferences is None:
            return
        self.preferences.memory_diagnostics = enabled
        self.preferences.save()

    def clear_all(self) -> None:
        if QMessageBox.question(self, "Clear memories", "Permanently delete all memories?") == QMessageBox.StandardButton.Yes:
            self.store.clear_all()
            self.refresh()


class MemoryRow(QFrame):
    def __init__(self, record: MemoryRecord, store: MemoryStore, refresh_callback) -> None:
        super().__init__()
        self.record = record
        self.store = store
        self.refresh_callback = refresh_callback
        self.setObjectName("conversationRow")
        layout = QGridLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        title = QLabel(f"{record.category} / {record.subject} / {record.key}")
        title.setObjectName("conversationTitle")
        value = QLabel(record.value)
        value.setWordWrap(True)
        source = QLabel(f"Source: {record.source_user_text}")
        source.setWordWrap(True)
        source.setObjectName("welcomeSubtitle")
        dates = QLabel(f"Created: {record.created_at}  Updated: {record.updated_at}")
        dates.setObjectName("welcomeSubtitle")
        status = QLabel("Active" if record.active else "Archived")
        status.setObjectName("welcomeSubtitle")
        layout.addWidget(title, 0, 0, 1, 4)
        layout.addWidget(value, 1, 0, 1, 4)
        layout.addWidget(source, 2, 0, 1, 4)
        layout.addWidget(dates, 3, 0, 1, 3)
        layout.addWidget(status, 3, 3)

        edit = QPushButton("Edit")
        archive = QPushButton("Archive" if record.active else "Restore")
        delete = QPushButton("Delete")
        for button in (edit, archive, delete):
            button.setObjectName("sidebarNewChat")
        edit.clicked.connect(self.edit)
        archive.clicked.connect(self.toggle_archive)
        delete.clicked.connect(self.delete)
        layout.addWidget(edit, 4, 1)
        layout.addWidget(archive, 4, 2)
        layout.addWidget(delete, 4, 3)

    def edit(self) -> None:
        value, ok = QInputDialog.getText(self, "Edit memory", "Value:", text=self.record.value)
        if not ok:
            return
        value = value.strip()
        if not value:
            QMessageBox.warning(self, "Invalid memory", "Memory value cannot be empty.")
            return
        content = f"{self.record.key}: {value}"
        self.store.edit(
            self.record.id,
            self.record.category,
            self.record.subject,
            self.record.key,
            value,
            content,
        )
        self.refresh_callback()

    def toggle_archive(self) -> None:
        if self.record.active:
            self.store.archive(self.record.id)
        else:
            self.store.restore(self.record.id)
        self.refresh_callback()

    def delete(self) -> None:
        if QMessageBox.question(self, "Delete memory", "Permanently delete this memory?") == QMessageBox.StandardButton.Yes:
            self.store.delete(self.record.id)
            self.refresh_callback()
