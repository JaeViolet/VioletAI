"""Native Memory Manager UI for VioletAI settings."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QPropertyAnimation, QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from design import Motion, icon
from memory_v2.models import CATEGORIES, MemoryRecord
from memory_v2.store import MemoryStore
from themes import BUILTIN_THEMES, DEFAULT_ACCENT, DEFAULT_THEME_NAME, is_builtin
from widgets import apply_interaction_cursors

TAB_NAMES = [
    "Settings",
    "Theme",
    "Memory",
    "Tools",
    "Web Search",
    "Files",
    "Images",
    "Voice",
    "Models",
    "Automation",
    "Plugins",
]

TAB_ICONS = [
    "settings",
    "theme",
    "memory",
    "new",
    "search",
    "copy",
    "copy",
    "new",
    "new",
    "regen",
    "new",
]


def _local_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone()


def _format_memory_stamp(value: str) -> str:
    try:
        local = _local_datetime(value)
    except (ValueError, TypeError):
        return ""
    if local.date() == datetime.now().astimezone().date():
        return f"{local.hour}:{local.minute:02d}"
    return f"{local.day:02d}-{local.month:02d}-{local.year}"


class SettingsOverlay(QFrame):
    closed = Signal()
    theme_changed = Signal()

    def __init__(self, store: MemoryStore, preferences: Preferences | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.store = store
        self.preferences = preferences
        self.setObjectName("settingsPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setWindowFlags(Qt.WindowType.Widget)
        self.setMaximumSize(880, 600)
        self.resize(880, 600)

        self.opacity = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.opacity)
        self.fade = QPropertyAnimation(self.opacity, b"opacity", self)
        self.fade.setDuration(Motion.NORMAL)
        self.fade.finished.connect(self._fade_finished)
        self._fading_out = False

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_nav())

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        header = QWidget(objectName="settingsHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(28, 18, 22, 16)
        header_layout.setSpacing(8)
        self.header_title = QLabel("Settings", objectName="settingsHeaderTitle")
        close = QToolButton(objectName="sidebarIconButton")
        close.setIcon(icon("close"))
        close.setToolTip("Close settings")
        close.clicked.connect(self.close_overlay)
        header_layout.addWidget(self.header_title)
        header_layout.addStretch()
        header_layout.addWidget(close)
        right_layout.addWidget(header)

        self.content_scroll = QScrollArea(objectName="settingsScroll")
        self.content_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.content_scroll.setWidgetResizable(True)
        self.content_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.content_stack = QStackedWidget(objectName="settingsStack")
        self.content_scroll.setWidget(self.content_stack)
        right_layout.addWidget(self.content_scroll, 1)
        root.addWidget(right, 1)

        self._build_memory_page()
        self._build_theme_page()

        self.tab_pages: dict[str, QWidget] = {
            "Settings": self._build_placeholder_page(),
            "Theme": self.theme_page,
            "Memory": self.memory_page,
            "Tools": self._build_placeholder_page(),
            "Web Search": self._build_placeholder_page(),
            "Files": self._build_placeholder_page(),
            "Images": self._build_placeholder_page(),
            "Voice": self._build_placeholder_page(),
            "Models": self._build_placeholder_page(),
            "Automation": self._build_placeholder_page(),
            "Plugins": self._build_placeholder_page(),
        }
        for name in TAB_NAMES:
            self.content_stack.addWidget(self.tab_pages[name])

        apply_interaction_cursors(self)
        self.hide()

    def _build_nav(self) -> QFrame:
        nav = QFrame(objectName="settingsNav")
        nav.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        nav_layout = QVBoxLayout(nav)
        nav_layout.setContentsMargins(0, 20, 0, 0)
        nav_layout.setSpacing(2)
        brand = QLabel("Settings", objectName="settingsNavBrand")
        nav_layout.addWidget(brand)
        self.tabs_scroll = QScrollArea(objectName="settingsTabsScroll")
        self.tabs_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.tabs_scroll.setWidgetResizable(True)
        self.tabs_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tabs_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        tabs_widget = QWidget()
        tabs_widget.setMinimumWidth(212)
        self.tabs_layout = QVBoxLayout(tabs_widget)
        self.tabs_layout.setContentsMargins(0, 0, 0, 0)
        self.tabs_layout.setSpacing(2)
        self.tab_buttons: list[QPushButton] = []
        for name, icon_name in zip(TAB_NAMES, TAB_ICONS):
            button = QPushButton(name)
            button.setObjectName("settingsTab")
            button.setIcon(icon(icon_name, size=22, right_pad=10))
            button.setIconSize(QSize(32, 22))
            button.setProperty("active", False)
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            button.clicked.connect(lambda _checked=False, tab=name: self.select_tab(tab))
            self.tabs_layout.addWidget(button)
            self.tab_buttons.append(button)
        self.tabs_layout.addStretch()
        self.tabs_scroll.setWidget(tabs_widget)
        nav_layout.addWidget(self.tabs_scroll, 1)
        nav.setFixedWidth(220)
        return nav

    def _build_memory_page(self) -> None:
        self.memory_page = QWidget(objectName="settingsPage")
        layout = QVBoxLayout(self.memory_page)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        search_row = QHBoxLayout()
        search_row.setSpacing(10)
        self.search_input = QLineEdit(objectName="settingsSearchInput")
        self.search_input.setPlaceholderText("Search memories")
        clear = QPushButton("Clear all", objectName="settingsClearButton")
        clear.clicked.connect(self.clear_all)
        search_row.addWidget(self.search_input, 1)
        search_row.addWidget(clear)
        layout.addLayout(search_row)

        self.confirm_panel = QFrame(objectName="settingsConfirm")
        confirm_layout = QVBoxLayout(self.confirm_panel)
        confirm_layout.setContentsMargins(16, 12, 16, 12)
        confirm_layout.setSpacing(6)
        confirm_title = QLabel("Clear all memories?", objectName="settingsConfirmTitle")
        confirm_text = QLabel("This permanently deletes every memory. This cannot be undone.", objectName="settingsConfirmText")
        confirm_text.setWordWrap(True)
        confirm_buttons = QHBoxLayout()
        confirm_buttons.setSpacing(8)
        confirm_buttons.addStretch()
        cancel_btn = QPushButton("Cancel", objectName="settingsActionButton")
        confirm_btn = QPushButton("Clear all", objectName="settingsDangerButton")
        cancel_btn.clicked.connect(self._hide_confirmation)
        confirm_btn.clicked.connect(self._confirmed_clear_all)
        confirm_buttons.addWidget(cancel_btn)
        confirm_buttons.addWidget(confirm_btn)
        confirm_layout.addWidget(confirm_title)
        confirm_layout.addWidget(confirm_text)
        confirm_layout.addLayout(confirm_buttons)
        self.confirm_panel.hide()
        layout.addWidget(self.confirm_panel)

        self.edit_panel = QFrame(objectName="settingsConfirm")
        edit_layout = QVBoxLayout(self.edit_panel)
        edit_layout.setContentsMargins(16, 12, 16, 12)
        edit_layout.setSpacing(8)
        edit_title = QLabel("Edit memory", objectName="settingsConfirmTitle")
        self.edit_input = QLineEdit(objectName="settingsSearchInput")
        self.edit_error = QLabel("Memory value cannot be empty.", objectName="settingsErrorText")
        self.edit_error.hide()
        edit_buttons = QHBoxLayout()
        edit_buttons.setSpacing(8)
        edit_buttons.addStretch()
        cancel_edit = QPushButton("Cancel", objectName="settingsActionButton")
        save_edit = QPushButton("Save", objectName="settingsActionButton")
        cancel_edit.clicked.connect(self._cancel_edit)
        save_edit.clicked.connect(self._save_edit)
        self.edit_input.returnPressed.connect(self._save_edit)
        edit_buttons.addWidget(cancel_edit)
        edit_buttons.addWidget(save_edit)
        edit_layout.addWidget(edit_title)
        edit_layout.addWidget(self.edit_input)
        edit_layout.addWidget(self.edit_error)
        edit_layout.addLayout(edit_buttons)
        self.edit_panel.hide()
        layout.addWidget(self.edit_panel)

        self.delete_panel = QFrame(objectName="settingsConfirm")
        delete_layout = QVBoxLayout(self.delete_panel)
        delete_layout.setContentsMargins(16, 12, 16, 12)
        delete_layout.setSpacing(6)
        delete_title = QLabel("Delete this memory?", objectName="settingsConfirmTitle")
        delete_text = QLabel("This permanently deletes this memory. This cannot be undone.", objectName="settingsConfirmText")
        delete_text.setWordWrap(True)
        delete_buttons = QHBoxLayout()
        delete_buttons.setSpacing(8)
        delete_buttons.addStretch()
        cancel_delete = QPushButton("Cancel", objectName="settingsActionButton")
        confirm_delete = QPushButton("Delete", objectName="settingsDangerButton")
        cancel_delete.clicked.connect(self._cancel_delete)
        confirm_delete.clicked.connect(self._confirmed_delete)
        delete_buttons.addWidget(cancel_delete)
        delete_buttons.addWidget(confirm_delete)
        delete_layout.addWidget(delete_title)
        delete_layout.addWidget(delete_text)
        delete_layout.addLayout(delete_buttons)
        self.delete_panel.hide()
        layout.addWidget(self.delete_panel)

        filters = QHBoxLayout()
        filters.setSpacing(8)
        self.category_filter = self._make_combo(["All", *CATEGORIES], 7)
        self.include_archived = self._make_combo(["Enabled", "Disabled", "All"], 8)
        self.sort_order = self._make_combo(["Created", "Updated", "Category", "Accessed"], 7)
        filters.addWidget(self.category_filter)
        filters.addWidget(self.include_archived)
        filters.addWidget(self.sort_order)
        filters.addStretch()
        layout.addLayout(filters)

        self.rows = QWidget()
        self.rows_layout = QVBoxLayout(self.rows)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(6)
        self.rows_layout.addStretch()
        layout.addWidget(self.rows, 1)

        self.search_input.textChanged.connect(self.refresh)
        self.category_filter.currentTextChanged.connect(self.refresh)
        self.include_archived.currentTextChanged.connect(self.refresh)
        self.sort_order.currentTextChanged.connect(self.refresh)

    def _make_combo(self, items: list[str], min_chars: int) -> QComboBox:
        combo = QComboBox(objectName="modelSelector")
        combo.addItems(items)
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumContentsLength(min_chars)
        return combo

    def _build_placeholder_page(self) -> QWidget:
        page = QWidget(objectName="settingsPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 22, 28, 22)
        layout.setSpacing(6)
        text = QLabel(
            "This section is not implemented yet. It will appear in a future release.",
            objectName="settingsPlaceholderText",
        )
        text.setWordWrap(True)
        layout.addWidget(text)
        layout.addStretch()
        return page

    def _build_theme_page(self) -> None:
        self.theme_page = QWidget(objectName="settingsPage")
        layout = QVBoxLayout(self.theme_page)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        presets_title = QLabel("Presets", objectName="settingsSectionTitle")
        layout.addWidget(presets_title)
        self.presets_layout = QGridLayout()
        self.presets_layout.setSpacing(8)
        layout.addLayout(self.presets_layout)

        customize_title = QLabel("Customize", objectName="settingsSectionTitle")
        layout.addWidget(customize_title)
        self.swatch_row = QHBoxLayout()
        self.swatch_row.setSpacing(8)
        self.swatch_buttons: list[QPushButton] = []
        self.swatch_colors: list[str] = []
        swatch_colors = list(dict.fromkeys(theme["accent"] for theme in BUILTIN_THEMES))
        for color in swatch_colors:
            swatch = QPushButton()
            swatch.setObjectName("themeSwatch")
            swatch.setFixedSize(26, 26)
            swatch.setCursor(Qt.CursorShape.PointingHandCursor)
            swatch.setStyleSheet(f"background: {color}; border: 1px solid #000000; border-radius: 13px;")
            swatch.clicked.connect(lambda _checked=False, c=color: self._set_custom_accent(c))
            self.swatch_buttons.append(swatch)
            self.swatch_colors.append(color)
            self.swatch_row.addWidget(swatch)
        self.swatch_row.addStretch()
        layout.addLayout(self.swatch_row)

        self.sliders: dict[str, QSlider] = {}
        self.slider_values: dict[str, QLabel] = {}
        for channel in ("R", "G", "B"):
            row = QHBoxLayout()
            row.setSpacing(8)
            label = QLabel(channel, objectName="settingsSectionTitle")
            row.addWidget(label)
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setObjectName("themeSlider")
            slider.setRange(0, 255)
            slider.setCursor(Qt.CursorShape.PointingHandCursor)
            slider.valueChanged.connect(self._slider_changed)
            value = QLabel(objectName="themeValueLabel")
            row.addWidget(slider, 1)
            row.addWidget(value)
            self.sliders[channel] = slider
            self.slider_values[channel] = value
            layout.addLayout(row)

        save_row = QHBoxLayout()
        save_row.setSpacing(8)
        self.preset_name_input = QLineEdit(objectName="settingsSearchInput")
        self.preset_name_input.setPlaceholderText("Preset name")
        save_preset = QPushButton("Save preset", objectName="settingsActionButton")
        save_preset.clicked.connect(self._save_preset)
        save_row.addWidget(self.preset_name_input, 1)
        save_row.addWidget(save_preset)
        layout.addLayout(save_row)
        self.preset_feedback = QLabel(objectName="settingsConfirmText")
        self.preset_feedback.setWordWrap(True)
        self.preset_feedback.hide()
        layout.addWidget(self.preset_feedback)
        layout.addStretch()

        self._rebuild_presets()
        self._sync_accent_controls()

    def _rebuild_presets(self) -> None:
        while self.presets_layout.count():
            item = self.presets_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        themes = [*BUILTIN_THEMES, *self.preferences.custom_themes]
        active_name = self.preferences.theme_name
        row = 0
        col = 0
        for theme in themes:
            card = ThemePresetCard(
                theme["name"],
                theme["accent"],
                custom=not is_builtin(theme["name"]),
                active=theme["name"] == active_name,
            )
            card.clicked.connect(lambda _=None, name=theme["name"]: self._apply_theme(name))
            if not is_builtin(theme["name"]):
                card.delete_requested.connect(lambda _=None, name=theme["name"]: self._delete_preset(name))
            self.presets_layout.addWidget(card, row, col)
            col += 1
            if col >= 3:
                col = 0
                row += 1

    def _find_theme(self, name: str) -> dict | None:
        for theme in [*self.preferences.custom_themes, *BUILTIN_THEMES]:
            if theme["name"] == name:
                return theme
        return None

    def _apply_theme(self, name: str) -> None:
        theme = self._find_theme(name)
        if theme is None:
            return
        self.preferences.theme_name = name
        self.preferences.theme_accent = theme["accent"]
        self.preferences.save()
        self._rebuild_presets()
        self._sync_accent_controls()
        self.theme_changed.emit()

    def _set_custom_accent(self, color: str) -> None:
        if self.preferences.theme_name != "Custom":
            theme = self._find_theme(self.preferences.theme_name)
            if theme is not None and theme["accent"].lower() == color.lower():
                return
            self.preferences.theme_name = "Custom"
            self._rebuild_presets()
        self.preferences.theme_accent = color
        self.preferences.save()
        self._sync_accent_controls()
        self.theme_changed.emit()

    def _slider_changed(self) -> None:
        color = "#{:02x}{:02x}{:02x}".format(
            self.sliders["R"].value(),
            self.sliders["G"].value(),
            self.sliders["B"].value(),
        )
        self._set_custom_accent(color)

    def _sync_accent_controls(self) -> None:
        accent = self.preferences.theme_accent
        qcolor = QColor(accent)
        values = (qcolor.red(), qcolor.green(), qcolor.blue())
        for channel, value in zip(("R", "G", "B"), values):
            self.sliders[channel].blockSignals(True)
            self.sliders[channel].setValue(value)
            self.slider_values[channel].setText(str(value))
            self.sliders[channel].blockSignals(False)
        for swatch, color in zip(self.swatch_buttons, self.swatch_colors):
            if color.lower() == accent.lower():
                swatch.setStyleSheet(f"background: {color}; border: 2px solid #ffffff; border-radius: 12px;")
            else:
                swatch.setStyleSheet(f"background: {color}; border: 1px solid #000000; border-radius: 13px;")

    def _save_preset(self) -> None:
        name = self.preset_name_input.text().strip()
        if not name:
            self.preset_feedback.setStyleSheet("color: #ff5a4f; font-size: 12px;")
            self.preset_feedback.setText("Enter a preset name.")
            self.preset_feedback.show()
            return
        self.preferences.custom_themes = [
            theme for theme in self.preferences.custom_themes if theme["name"] != name
        ]
        self.preferences.custom_themes.append(
            {"name": name, "accent": self.preferences.theme_accent}
        )
        self.preferences.theme_name = name
        self.preferences.save()
        self.preset_name_input.clear()
        self.preset_feedback.setStyleSheet("")
        self.preset_feedback.setText("Preset saved.")
        self.preset_feedback.show()
        self._rebuild_presets()
        self._sync_accent_controls()
        self.theme_changed.emit()

    def _delete_preset(self, name: str) -> None:
        self.preferences.custom_themes = [
            theme for theme in self.preferences.custom_themes if theme["name"] != name
        ]
        if self.preferences.theme_name == name:
            self.preferences.theme_name = DEFAULT_THEME_NAME
            self.preferences.theme_accent = DEFAULT_ACCENT
        self.preferences.save()
        self._rebuild_presets()
        self._sync_accent_controls()
        self.theme_changed.emit()

    def select_tab(self, name: str) -> None:
        if name not in self.tab_pages:
            return
        for button in self.tab_buttons:
            active = button.text() == name
            button.setProperty("active", active)
            button.style().unpolish(button)
            button.style().polish(button)
        self.content_stack.setCurrentWidget(self.tab_pages[name])
        self.header_title.setText(name)
        if name == "Memory":
            self.refresh()
        elif name == "Theme":
            self._sync_accent_controls()
            self._rebuild_presets()
        else:
            self.confirm_panel.hide()

    def show_overlay(self) -> None:
        if self.parentWidget() is not None:
            parent_rect = self.parentWidget().rect()
            width = min(880, max(660, parent_rect.width() - 120))
            height = min(600, max(460, parent_rect.height() - 120))
            self.resize(width, height)
            self.move(
                (parent_rect.width() - width) // 2,
                (parent_rect.height() - height) // 2,
            )
        self.select_tab("Settings")
        self.refresh()
        self._fading_out = False
        self.fade.stop()
        self.opacity.setOpacity(0)
        self.show()
        self.raise_()
        self.fade.setStartValue(0)
        self.fade.setEndValue(1)
        self.fade.start()

    def close_overlay(self) -> None:
        if self._fading_out:
            return
        self._fading_out = True
        self.fade.stop()
        self.fade.setStartValue(self.opacity.opacity())
        self.fade.setEndValue(0)
        self.fade.start()

    def _fade_finished(self) -> None:
        if self._fading_out:
            self._finish_close()

    def _finish_close(self) -> None:
        self._fading_out = False
        self.hide()
        self.closed.emit()

    def refresh(self) -> None:
        while self.rows_layout.count() > 1:
            item = self.rows_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        status = self.include_archived.currentText()
        self.confirm_panel.hide()
        self.edit_panel.hide()
        self.delete_panel.hide()
        records = self.store.search_memories(
            self.search_input.text().strip(),
            self.category_filter.currentText(),
            include_archived=status != "Enabled",
        )
        if status == "Disabled":
            records = [record for record in records if not record.active]
        records = self._sort_records(records)
        for record in records:
            self.rows_layout.insertWidget(
                self.rows_layout.count() - 1,
                MemoryRow(record, self.store, self.refresh, self._open_edit_panel, self._open_delete_panel),
            )
        if not records:
            empty = QLabel("No memories found.", objectName="settingsPlaceholderText")
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

    def clear_all(self) -> None:
        self.edit_panel.hide()
        self.delete_panel.hide()
        self.confirm_panel.show()

    def _hide_confirmation(self) -> None:
        self.confirm_panel.hide()

    def _confirmed_clear_all(self) -> None:
        self.confirm_panel.hide()
        self.store.clear_durable()
        self.refresh()

    def _open_edit_panel(self, record: MemoryRecord) -> None:
        self._editing_record = record
        self.confirm_panel.hide()
        self.delete_panel.hide()
        self.edit_input.setText(record.value)
        self.edit_error.hide()
        self.edit_panel.show()
        self.edit_input.setFocus()
        self.edit_input.selectAll()
        if self.content_scroll.verticalScrollBar() is not None:
            self.content_scroll.verticalScrollBar().setValue(0)

    def _save_edit(self) -> None:
        record = self._editing_record
        if record is None:
            return
        value = self.edit_input.text().strip()
        if not value:
            self.edit_error.show()
            return
        self.edit_panel.hide()
        self.store.update_memory(
            record.id,
            category=record.category,
            subject=record.subject,
            key=record.key,
            value=value,
            content=f"{record.key}: {value}",
            manual=True,
        )
        self.refresh()

    def _cancel_edit(self) -> None:
        self.edit_panel.hide()

    def _open_delete_panel(self, record: MemoryRecord) -> None:
        self._deleting_record = record
        self.confirm_panel.hide()
        self.edit_panel.hide()
        self.delete_panel.show()
        if self.content_scroll.verticalScrollBar() is not None:
            self.content_scroll.verticalScrollBar().setValue(0)

    def _confirmed_delete(self) -> None:
        record = self._deleting_record
        self.delete_panel.hide()
        if record is None:
            return
        self.store.delete_memory(record.id)
        self.refresh()

    def _cancel_delete(self) -> None:
        self.delete_panel.hide()


class MemoryRow(QFrame):
    def __init__(
        self,
        record: MemoryRecord,
        store: MemoryStore,
        refresh_callback,
        edit_requested,
        delete_requested,
    ) -> None:
        super().__init__()
        self.record = record
        self.store = store
        self.refresh_callback = refresh_callback
        self.setObjectName("settingsMemoryRow")
        layout = QGridLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        value = QLabel(record.value)
        value.setObjectName("memoryValue")
        value.setWordWrap(True)
        meta = QLabel(f"{record.category} · {record.key}")
        meta.setObjectName("memoryMeta")
        stamp = QLabel(_format_memory_stamp(record.created_at))
        stamp.setObjectName("memoryMeta")
        stamp.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        layout.addWidget(value, 0, 0, 1, 3)
        layout.addWidget(stamp, 0, 3)
        layout.addWidget(meta, 1, 0, 1, 4)

        edit = QPushButton("Edit", objectName="settingsActionButton")
        toggle = QPushButton("Disable" if record.active else "Enable", objectName="settingsActionButton")
        delete = QPushButton("Delete", objectName="settingsActionButton")
        edit.clicked.connect(lambda: edit_requested(record))
        toggle.clicked.connect(self.toggle_archive)
        delete.clicked.connect(lambda: delete_requested(record))
        layout.addWidget(edit, 2, 1)
        layout.addWidget(toggle, 2, 2)
        layout.addWidget(delete, 2, 3)

    def toggle_archive(self) -> None:
        if self.record.active:
            self.store.archive_memory(self.record.id)
        else:
            self.store.restore_memory(self.record.id)
        self.refresh_callback()


class ThemePresetCard(QFrame):
    clicked = Signal()
    delete_requested = Signal()

    def __init__(self, name: str, accent: str, custom: bool = False, active: bool = False) -> None:
        super().__init__()
        self.setObjectName("themePreset")
        self.setProperty("active", active)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(40)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(8)
        dot = QFrame(objectName="themeDot")
        dot.setFixedSize(14, 14)
        dot.setStyleSheet(f"background: {accent}; border-radius: 7px;")
        label = QLabel(name, objectName="themePresetName")
        layout.addWidget(dot)
        layout.addWidget(label, 1)
        if custom:
            remove = QToolButton(objectName="sidebarIconButton")
            remove.setIcon(icon("close"))
            remove.setToolTip(f"Delete {name}")
            remove.setCursor(Qt.CursorShape.PointingHandCursor)
            remove.clicked.connect(self.delete_requested.emit)
            layout.addWidget(remove)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)
