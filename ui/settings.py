"""Settings overlay UI for VioletAI."""

from __future__ import annotations

from PySide6.QtCore import QPropertyAnimation, QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
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

from ui.design import Motion, icon
from ui.themes import BUILTIN_THEMES, DEFAULT_ACCENT, DEFAULT_THEME_NAME, is_builtin
from ui.widgets import apply_interaction_cursors

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


class SettingsOverlay(QFrame):
    closed = Signal()
    theme_changed = Signal()

    def __init__(self, preferences: Preferences | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
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

        self._build_theme_page()

        self.tab_pages: dict[str, QWidget] = {
            "Settings": self._build_placeholder_page(),
            "Theme": self.theme_page,
            "Memory": self._build_placeholder_page(),
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
        if name == "Theme":
            self._sync_accent_controls()
            self._rebuild_presets()

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
