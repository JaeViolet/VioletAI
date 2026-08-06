"""Main window UI for VioletAI."""

from __future__ import annotations

import os

from PySide6.QtCore import (
    QEvent,
    QObject,
    QSize,
    Signal,
    Qt,
    QTimer,
)
from PySide6.QtGui import QCloseEvent, QResizeEvent
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QPushButton,
    QLayout,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.config import (
    APP_FOOTER_TEXT,
    APP_NAME,
    DEFAULT_MODEL_NAME,
    MEMORY_DB_PATH,
    SYSTEM_PROMPT,
)
from conversations.manager import Conversation, ConversationStore
from core.prompts import build_ollama_messages
from core.engine import Engine, ModelManager
from memory.manager import LocalMemoryBackend, MemoryManager
from tools.manager import available_tools
from ui.chat_view import ChatView
from ui.design import PNG_CONTROL_ICON_SIZE, icon
from ui.styles import app_stylesheet
from ui.preferences import Preferences
from ui.settings import SettingsOverlay
from ui.sidebar import ChatSidebar, SearchOverlay
from ui.widgets import (
    AutoGrowingInput,
    MessageBubble,
    ModelSelector,
    apply_interaction_cursors,
)


class ConfirmBackdrop(QFrame):
    confirmed = Signal()
    cancelled = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("confirmBackdrop")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.hide()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._card = QFrame(objectName="confirmCard")
        layout.addWidget(self._card, 0, Qt.AlignmentFlag.AlignCenter)

        card_layout = QVBoxLayout(self._card)
        card_layout.setContentsMargins(24, 20, 24, 20)
        card_layout.setSpacing(10)
        self.title_label = QLabel(objectName="confirmCardTitle")
        self.text_label = QLabel(objectName="confirmCardText")
        self.text_label.setWordWrap(True)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch()
        self.cancel_button = QPushButton("Cancel", objectName="settingsActionButton")
        self.confirm_button = QPushButton(objectName="settingsDangerButton")
        self.cancel_button.clicked.connect(self._cancel)
        self.confirm_button.clicked.connect(self.confirmed.emit)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.confirm_button)
        card_layout.addWidget(self.title_label)
        card_layout.addWidget(self.text_label)
        card_layout.addLayout(buttons)

    def set_message(self, title: str, text: str, confirm_text: str) -> None:
        self.title_label.setText(title)
        self.text_label.setText(text)
        self.confirm_button.setText(confirm_text)

    def show_overlay(self) -> None:
        parent = self.parentWidget()
        if parent is not None:
            self.setGeometry(parent.rect())
        self.raise_()
        self.show()

    def hide_overlay(self) -> None:
        self.hide()

    def mousePressEvent(self, event) -> None:
        if not self._card.geometry().contains(event.position().toPoint()):
            self._cancel()
            event.accept()
            return
        super().mousePressEvent(event)

    def _cancel(self) -> None:
        self.cancelled.emit()
        self.hide_overlay()


class MainWindow(QMainWindow):
    CONTENT_MAX_WIDTH = 760

    def __init__(self) -> None:
        super().__init__()
        self.preferences = Preferences()
        self.active_model = self.preferences.selected_model or DEFAULT_MODEL_NAME
        self.available_models: list[str] = []
        self.store = ConversationStore()
        self.memory_store = MemoryManager(LocalMemoryBackend(MEMORY_DB_PATH))
        self.conversation = self._load_or_create_conversation()
        self.messages = self.conversation.messages

        self.engine = Engine(self)
        self.engine.connected.connect(lambda: self._set_status("Thinking"))
        self.engine.chunk_received.connect(self._receive_chunk)
        self.engine.finished.connect(self._receive_response)
        self.engine.cancelled.connect(self._receive_cancelled)
        self.engine.failed.connect(self._receive_error)
        self.engine.stopped.connect(self._cleanup_worker)
        self.model_manager = ModelManager(self)
        self.model_manager.finished.connect(self._models_discovered)
        self.model_manager.failed.connect(self._models_failed)

        self._generation_cancel_requested = False

        self._composer_multiline = False
        self._updating_composer_mode = False
        self._composer_edit_sequence = 0
        self._composer_state_changes_this_edit = 0
        self._composer_total_state_changes = 0
        self._composer_layout_diagnostics: list[dict[str, object]] = []
        self._composer_mode_timer = QTimer(self)
        self._composer_mode_timer.setSingleShot(True)
        self._composer_mode_timer.timeout.connect(self._update_composer_mode)

        self.setWindowTitle(APP_NAME)
        self.resize(1180, 780)
        self.setMinimumSize(760, 520)
        self._build_interface()
        self._apply_style()
        apply_interaction_cursors(self)
        self._rebuild_sidebar()
        self._rebuild_messages()
        self._refresh_models()
        self.input_box.setFocus()

    def _load_or_create_conversation(self) -> Conversation:
        conversation = self.store.create(SYSTEM_PROMPT, self.active_model)
        if not conversation.messages or conversation.messages[0].get("role") != "system":
            conversation.messages.insert(0, {"role": "system", "content": SYSTEM_PROMPT})
        return conversation

    def _build_interface(self) -> None:
        central = QWidget(objectName="centralWidget")
        central.installEventFilter(self)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.sidebar = ChatSidebar()
        self.sidebar.new_chat_requested.connect(self.new_chat)
        self.sidebar.search_requested.connect(self.open_search_overlay)
        self.sidebar.settings_requested.connect(self.toggle_settings_overlay)
        self.sidebar.conversation_selected.connect(self.select_conversation)
        self.sidebar.pin_requested.connect(self.pin_conversation)
        self.sidebar.rename_requested.connect(self.rename_conversation)
        self.sidebar.delete_requested.connect(self.delete_conversation)
        root.addWidget(self.sidebar)

        self.chat_panel = QFrame(objectName="mainPanel")
        self.chat_panel.installEventFilter(self)
        main_layout = QVBoxLayout(self.chat_panel)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        root.addWidget(self.chat_panel, 1)

        self.chat_view = ChatView()
        self.chat_view.regenerate_requested.connect(self.regenerate_response)
        self.chat_view.viewport_resized.connect(self._on_chat_viewport_resized)
        main_layout.addWidget(self.chat_view, 1)

        self.input_panel = QFrame(objectName="inputPanel")
        input_outer = QHBoxLayout(self.input_panel)
        input_outer.setContentsMargins(24, 8, 24, 14)
        input_outer.addStretch()

        self.composer = QFrame(objectName="composer")
        self.composer.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.composer.setFrameShape(QFrame.Shape.NoFrame)
        self.composer.setProperty("compact", True)
        self.composer.setFixedWidth(self.CONTENT_MAX_WIDTH)
        self.composer_layout = QVBoxLayout(self.composer)
        self.composer_layout.setContentsMargins(12, 4, 6, 4)
        self.composer_layout.setSpacing(4)
        self.composer_layout.setSizeConstraint(QLayout.SizeConstraint.SetDefaultConstraint)

        self.input_box = AutoGrowingInput()
        self.input_box.send_requested.connect(self.send_message)
        self.input_box.height_changed.connect(lambda _height: self._schedule_composer_mode_update())
        self.input_box.textChanged.connect(self._handle_composer_text_changed)
        self.tools_button = QToolButton(objectName="toolsButton")
        self.tools_button.setToolTip("VioletAI tools")
        self.tools_button.setIcon(icon("new"))
        self.tools_button.setIconSize(QSize(18, 18))
        self.tools_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.tools_button.setMenu(self._build_tools_menu())
        self.toolbar_tools_button = QToolButton(objectName="toolsButton")
        self.toolbar_tools_button.setToolTip("VioletAI tools")
        self.toolbar_tools_button.setIcon(icon("new"))
        self.toolbar_tools_button.setIconSize(QSize(18, 18))
        self.toolbar_tools_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.toolbar_tools_button.setMenu(self._build_tools_menu())
        self.model_selector = ModelSelector(objectName="modelSelector")
        self._configure_model_selector(self.model_selector)
        self.model_selector.currentTextChanged.connect(self._model_changed)
        self.toolbar_model_selector = ModelSelector(objectName="modelSelector")
        self._configure_model_selector(self.toolbar_model_selector)
        self.toolbar_model_selector.currentTextChanged.connect(self._model_changed)
        self.send_button = QToolButton(objectName="sendButton")
        self.send_button.setToolTip("Send message")
        self.send_button.setIcon(icon("send", "white", PNG_CONTROL_ICON_SIZE))
        self.send_button.setIconSize(QSize(PNG_CONTROL_ICON_SIZE, PNG_CONTROL_ICON_SIZE))
        self.send_button.clicked.connect(self.send_message)
        self.toolbar_send_button = QToolButton(objectName="sendButton")
        self.toolbar_send_button.setToolTip("Send message")
        self.toolbar_send_button.setIcon(icon("send", "white", PNG_CONTROL_ICON_SIZE))
        self.toolbar_send_button.setIconSize(QSize(PNG_CONTROL_ICON_SIZE, PNG_CONTROL_ICON_SIZE))
        self.toolbar_send_button.clicked.connect(self.send_message)
        self.stop_button = QToolButton(objectName="sendButton")
        self.stop_button.setToolTip("Stop generating")
        self.stop_button.setIcon(icon("stop", "white", PNG_CONTROL_ICON_SIZE))
        self.stop_button.setIconSize(QSize(PNG_CONTROL_ICON_SIZE, PNG_CONTROL_ICON_SIZE))
        self.stop_button.clicked.connect(self.stop_generation)
        self.stop_button.hide()
        self.toolbar_stop_button = QToolButton(objectName="sendButton")
        self.toolbar_stop_button.setToolTip("Stop generating")
        self.toolbar_stop_button.setIcon(icon("stop", "white", PNG_CONTROL_ICON_SIZE))
        self.toolbar_stop_button.setIconSize(QSize(PNG_CONTROL_ICON_SIZE, PNG_CONTROL_ICON_SIZE))
        self.toolbar_stop_button.clicked.connect(self.stop_generation)
        self.toolbar_stop_button.hide()

        self.input_row = QHBoxLayout()
        self.input_row.setContentsMargins(0, 0, 0, 0)
        self.input_row.setSpacing(8)
        self.input_row.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self.toolbar_widget = QWidget(objectName="composerToolbar")
        self.toolbar_layout = QHBoxLayout(self.toolbar_widget)
        self.toolbar_layout.setContentsMargins(0, 0, 0, 0)
        self.toolbar_layout.setSpacing(8)
        self.toolbar_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self.composer_layout.addLayout(self.input_row)
        self.composer_layout.addWidget(self.toolbar_widget)
        self.input_row.addWidget(self.tools_button)
        self.input_row.addWidget(self.input_box, 1)
        self.input_row.addWidget(self.model_selector)
        self.input_row.addWidget(self.stop_button)
        self.input_row.addWidget(self.send_button)
        self.toolbar_layout.addWidget(self.toolbar_tools_button)
        self.toolbar_layout.addStretch(1)
        self.toolbar_layout.addWidget(self.toolbar_model_selector)
        self.toolbar_layout.addWidget(self.toolbar_stop_button)
        self.toolbar_layout.addWidget(self.toolbar_send_button)
        self.toolbar_widget.hide()
        self._set_composer_layout_mode(False)
        input_outer.addWidget(self.composer, 1)
        input_outer.addStretch()

        self.footer_status = QLabel(objectName="footerStatus")
        self.footer_status.setText(APP_FOOTER_TEXT)
        self.footer_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        panel_layout = QVBoxLayout()
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(5)
        panel_layout.addWidget(self.input_panel)
        panel_layout.addWidget(self.footer_status)
        main_layout.addLayout(panel_layout)
        main_layout.addSpacing(10)
        self.setCentralWidget(central)
        self.search_overlay = SearchOverlay(self.chat_panel)
        self.search_overlay.selected.connect(self._select_from_search)
        self.search_overlay.search_changed.connect(self._rebuild_search_results)
        self.settings_overlay = SettingsOverlay(self.memory_store, self.preferences, self.chat_panel)
        self.settings_overlay.theme_changed.connect(self._apply_style)
        self.confirm_overlay = ConfirmBackdrop(self.chat_panel)
        self.confirm_overlay.confirmed.connect(self._confirmed_delete_conversation)
        self._pending_delete_conversation_id: str | None = None

    def _configure_model_selector(self, selector: QComboBox) -> None:
        selector.setToolTip("Select local Ollama model")
        selector.setMinimumContentsLength(9)
        selector.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        selector.setFixedHeight(30)
        selector.setMinimumWidth(112)
        selector.setMaximumWidth(140)
        selector.view().setMinimumWidth(190)

    def _build_tools_menu(self) -> QMenu:
        menu = QMenu(self)
        for tool in available_tools():
            if tool.handler is None:
                action = menu.addAction(f"{tool.name} - Coming soon")
                action.setEnabled(False)
            else:
                action = menu.addAction(tool.name)
                action.triggered.connect(lambda checked=False, handler=tool.handler: handler(self))
            action.setData(tool.name)
        return menu

    def _set_composer_layout_mode(self, multiline: bool) -> None:
        if self._updating_composer_mode:
            return
        if multiline == self._composer_multiline:
            return
        self._updating_composer_mode = True
        cursor = self.input_box.textCursor()
        self._composer_multiline = multiline
        self._composer_state_changes_this_edit += 1
        self._composer_total_state_changes += 1
        try:
            is_compact = not multiline
            if self.composer.property("compact") != is_compact:
                self.composer.setProperty("compact", is_compact)
                self.composer.style().unpolish(self.composer)
                self.composer.style().polish(self.composer)
            if multiline:
                self.composer_layout.setContentsMargins(14, 10, 8, 8)
                self.tools_button.hide()
                self.model_selector.hide()
                self.send_button.hide()
                self.stop_button.hide()
                self.toolbar_tools_button.show()
                self.toolbar_model_selector.show()
                self.toolbar_send_button.setVisible(not self.engine.running)
                self.toolbar_stop_button.setVisible(self.engine.running)
                self.toolbar_widget.setMaximumHeight(16_777_215)
                self.toolbar_widget.show()
            else:
                self.composer_layout.setContentsMargins(12, 4, 6, 4)
                self.tools_button.show()
                self.model_selector.show()
                self.send_button.setVisible(not self.engine.running)
                self.stop_button.setVisible(self.engine.running)
                self.toolbar_widget.hide()
                self.toolbar_widget.setMaximumHeight(0)
            self.composer_layout.activate()
            self.composer.updateGeometry()
            self.input_box.setTextCursor(cursor)
            self.input_box.setFocus()
        finally:
            self._updating_composer_mode = False

    def _set_visible_if_needed(self, widget: QWidget, visible: bool) -> None:
        if widget.isVisible() != visible:
            widget.setVisible(visible)

    def _schedule_composer_mode_update(self) -> None:
        if self._updating_composer_mode:
            return
        if hasattr(self, "_composer_mode_timer") and not self._composer_mode_timer.isActive():
            self._composer_mode_timer.start(0)

    def _handle_composer_text_changed(self) -> None:
        self._composer_edit_sequence += 1
        self._composer_state_changes_this_edit = 0
        self._schedule_composer_mode_update()

    def _update_composer_mode(self) -> None:
        if not hasattr(self, "input_box") or self._updating_composer_mode:
            return
        available_width = self._stable_composer_text_width()
        metrics = self.input_box.measured_document_metrics(available_width)
        multiline = self._next_composer_multiline(metrics)
        self._record_composer_layout_diagnostic(metrics, available_width, multiline)
        if multiline != self._composer_multiline:
            self._set_composer_layout_mode(multiline)
        else:
            target_toolbar_height = 16_777_215 if multiline else 0
            if self.toolbar_widget.maximumHeight() != target_toolbar_height:
                self.toolbar_widget.setMaximumHeight(target_toolbar_height)
            self._set_visible_if_needed(self.toolbar_widget, multiline)
            self._set_visible_if_needed(self.tools_button, not multiline)
            self._set_visible_if_needed(self.model_selector, not multiline)
            self._set_visible_if_needed(self.toolbar_tools_button, multiline)
            self._set_visible_if_needed(self.toolbar_model_selector, multiline)
            if not self.engine.running:
                self._set_visible_if_needed(self.send_button, not multiline)
                self._set_visible_if_needed(self.toolbar_send_button, multiline)
                self._set_visible_if_needed(self.stop_button, False)
                self._set_visible_if_needed(self.toolbar_stop_button, False)

    def _next_composer_multiline(self, metrics: dict[str, float | int]) -> bool:
        visual_lines = int(metrics.get("visual_lines") or 1)
        block_count = int(metrics.get("block_count") or 1)
        document_height = float(metrics.get("document_height") or 0)
        line_height = max(1.0, float(metrics.get("line_height") or 1))
        if not self._composer_multiline:
            return block_count > 1 or visual_lines > 1 or document_height > line_height * 1.75
        return not (block_count <= 1 and visual_lines <= 1 and document_height < line_height * 1.45)

    def _stable_composer_text_width(self) -> int:
        row_spacing = self.input_row.spacing()
        control_width = 0
        visible_controls = (self.tools_button, self.model_selector, self.send_button if not self.engine.running else self.stop_button)
        for control in visible_controls:
            hint = control.sizeHint()
            control_width += max(control.width(), hint.width(), control.minimumWidth())
        control_width += row_spacing * max(0, len(visible_controls))
        compact_left_margin = 12
        compact_right_margin = 6
        return max(80, self.composer.width() - compact_left_margin - compact_right_margin - control_width)

    def _record_composer_layout_diagnostic(
        self,
        metrics: dict[str, float | int],
        available_width: int,
        requested_state: bool,
    ) -> None:
        record = {
            "edit": self._composer_edit_sequence,
            "document_height": round(float(metrics.get("document_height") or 0), 3),
            "available_text_width": available_width,
            "target_height": self.input_box.height(),
            "current_state": "multiline" if self._composer_multiline else "compact",
            "requested_next_state": "multiline" if requested_state else "compact",
            "state_changes_this_edit": self._composer_state_changes_this_edit,
        }
        self._composer_layout_diagnostics.append(record)
        self._composer_layout_diagnostics = self._composer_layout_diagnostics[-80:]
        if os.environ.get("VIOLETAI_COMPOSER_DIAGNOSTICS") == "1":
            print(f"Composer {record}", flush=True)

    def _rebuild_messages(self) -> None:
        self.chat_view.rebuild_messages(self.messages)
        self._resize_rows()

    def _rebuild_sidebar(self) -> None:
        self.setUpdatesEnabled(False)
        try:
            self.sidebar.rebuild(
                self.store.grouped(),
                self.conversation.id,
            )
        finally:
            self.setUpdatesEnabled(True)

    def open_search_overlay(self) -> None:
        self._rebuild_search_results("")
        self.search_overlay.show_overlay()

    def _rebuild_search_results(self, query: str) -> None:
        self.search_overlay.rebuild(self.store.search(query))

    def _select_from_search(self, conversation_id: str) -> None:
        self.search_overlay.close_overlay()
        self.select_conversation(conversation_id)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if not hasattr(self, "chat_panel"):
            return super().eventFilter(watched, event)
        if (
            watched is self.chat_panel
            and hasattr(self, "search_overlay")
            and self.search_overlay.isVisible()
        ):
            if event.type() == QEvent.Type.MouseButtonPress:
                if not self.search_overlay.geometry().contains(event.position().toPoint()):
                    self.search_overlay.close_overlay()
            if event.type() == QEvent.Type.Resize:
                QTimer.singleShot(0, self._position_search_overlay)
        if (
            watched is self.chat_panel
            and hasattr(self, "settings_overlay")
            and self.settings_overlay.isVisible()
        ):
            if event.type() == QEvent.Type.MouseButtonPress:
                if not self.settings_overlay.geometry().contains(event.position().toPoint()):
                    self.settings_overlay.close_overlay()
            if event.type() == QEvent.Type.Resize:
                QTimer.singleShot(0, self._position_settings_overlay)
        if (
            watched is self.chat_panel
            and hasattr(self, "confirm_overlay")
            and self.confirm_overlay.isVisible()
        ):
            if event.type() == QEvent.Type.Resize:
                QTimer.singleShot(0, self._position_confirm_overlay)
        return super().eventFilter(watched, event)

    def _position_search_overlay(self) -> None:
        if self.search_overlay.isVisible():
            self.search_overlay.show_overlay()

    def open_settings_overlay(self) -> None:
        self.settings_overlay.show_overlay()

    def toggle_settings_overlay(self) -> None:
        if self.settings_overlay.isVisible():
            self.settings_overlay.close_overlay()
        else:
            self.settings_overlay.show_overlay()

    def _position_settings_overlay(self) -> None:
        if self.settings_overlay.isVisible():
            self.settings_overlay.show_overlay()

    def _position_confirm_overlay(self) -> None:
        if self.confirm_overlay.isVisible():
            self.confirm_overlay.setGeometry(self.chat_panel.rect())

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._resize_rows()

    def _resize_rows(self) -> None:
        available = self.chat_view._available_width()
        if self.composer.width() != available:
            self.composer.setFixedWidth(available)
        if hasattr(self, "input_panel") and self.input_panel.layout() is not None:
            self.input_panel.layout().activate()
        if hasattr(self, "input_box"):
            self.input_box._update_height()
        self._schedule_composer_mode_update()
        self.chat_view.resize_rows(available)

    def _on_chat_viewport_resized(self) -> None:
        QTimer.singleShot(0, self._resize_rows)

    def _add_message(self, text: str, role: str, message_index: int | None = None) -> MessageBubble:
        return self.chat_view.add_message(text, role, message_index)

    def new_chat(self) -> None:
        if self.engine.running:
            self.stop_generation()
            return
        self.conversation = self.store.create(SYSTEM_PROMPT, self.active_model)
        self.messages = self.conversation.messages
        self.input_box.clear()
        self.chat_view.reset_stream()
        self._set_status("Ready")
        self._rebuild_sidebar()
        self._rebuild_messages()

    def select_conversation(self, conversation_id: str) -> None:
        if self.engine.running:
            return
        conversation = self.store.load_by_id(conversation_id)
        if conversation is None:
            self._rebuild_sidebar()
            return
        if conversation_id == self.conversation.id:
            self.input_box.setFocus()
            return
        self.conversation = conversation
        self.messages = self.conversation.messages
        self.active_model = self.conversation.model or self.active_model
        self._set_model_selector(self.available_models)
        self._set_status("Ready")
        self.sidebar.set_active(conversation.id)
        self._rebuild_messages()
        self.input_box.setFocus()

    def rename_conversation(self, conversation_id: str, title: str) -> None:
        self.store.rename(conversation_id, title)
        if conversation_id == self.conversation.id:
            current = self.store.load_by_id(conversation_id)
            if current is not None:
                self.conversation = current
                self.messages = current.messages
        self._rebuild_sidebar()

    def pin_conversation(self, conversation_id: str, pinned: bool) -> None:
        self.store.set_pinned(conversation_id, pinned)
        if conversation_id == self.conversation.id:
            current = self.store.load_by_id(conversation_id)
            if current is not None:
                self.conversation = current
                self.messages = current.messages
        self._rebuild_sidebar()

    def delete_conversation(self, conversation_id: str) -> None:
        self._pending_delete_conversation_id = conversation_id
        self.confirm_overlay.set_message(
            "Delete conversation",
            "Delete this conversation? This removes the saved JSON file.",
            "Delete",
        )
        self.confirm_overlay.show_overlay()

    def _confirmed_delete_conversation(self) -> None:
        self.confirm_overlay.hide_overlay()
        conversation_id = self._pending_delete_conversation_id
        self._pending_delete_conversation_id = None
        if not conversation_id:
            return
        self.store.delete(conversation_id)
        if conversation_id == self.conversation.id:
            self.conversation = self.store.create(SYSTEM_PROMPT, self.active_model)
            self.messages = self.conversation.messages
            self._rebuild_messages()
        self._rebuild_sidebar()

    def send_message(self) -> None:
        message = self.input_box.toPlainText().strip()
        if not message or self.engine.running:
            return
        self.input_box.remember_prompt(message)
        self._add_message(message, "user", len(self.messages))
        self.messages.append({"role": "user", "content": message})
        self.conversation.model = self.active_model
        self.store.save(self.conversation)
        self._rebuild_sidebar()
        self.input_box.clear()
        self.chat_view.scroll_to_bottom()
        self._start_generation()

    def _start_generation(
        self,
        ollama_messages: list[dict[str, str]] | None = None,
        show_thinking: bool = True,
    ) -> None:
        self.chat_view.reset_stream()
        self._generation_cancel_requested = False
        if show_thinking:
            self.chat_view.show_thinking()
        self._set_controls_generating(True)
        self._set_status("Connecting")

        if ollama_messages is None:
            ollama_messages = build_ollama_messages(self.messages, SYSTEM_PROMPT)
        self.engine.start(ollama_messages, self.active_model)

    def regenerate_response(self, message_index: int) -> None:
        if self.engine.running:
            return
        if not 0 <= message_index < len(self.messages):
            return
        if self.messages[message_index].get("role") != "assistant":
            return
        self.messages[:] = self.messages[:message_index]
        self.store.save(self.conversation)
        self._rebuild_messages()
        self._start_generation()

    def stop_generation(self) -> None:
        if not self.engine.running:
            return
        self._generation_cancel_requested = True
        self._set_status("Stopped")
        self.engine.cancel()
        self.chat_view.render_stream(force=True)
        self._finalize_partial_response(stopped=True)

    def _receive_chunk(self, chunk: str) -> None:
        if self._generation_cancel_requested:
            return
        self.chat_view.on_chunk(chunk)
        self._set_status("Generating")

    def _receive_response(self, answer: str) -> None:
        if self._generation_cancel_requested:
            return
        self.chat_view.finalize_response(answer)
        self._append_assistant_message(answer)
        if self.chat_view.auto_scroll_enabled():
            self.chat_view.scroll_to_bottom(smooth=True)
        self._set_status("Ready")

    def _receive_cancelled(self) -> None:
        self._set_status("Stopped")
        self._finalize_partial_response(stopped=True)

    def _receive_error(self, error: str) -> None:
        if self._generation_cancel_requested:
            return
        failed_stage = self._response_failure_stage(error)
        self.chat_view.stop_render()
        self.chat_view.remove_thinking()
        if self.chat_view.has_pending_stream():
            self.chat_view.set_pending_text(self.chat_view.streamed_answer)
            self._finalize_partial_response(stopped=True)
            error_message = self._format_generation_error(error, failed_stage, partial=True)
        else:
            error_message = self._format_generation_error(error, failed_stage)
        self.chat_view.add_message(error_message, "error")
        self.store.save(self.conversation)
        self._set_status("Error")
        self.chat_view.scroll_to_bottom()

    def _append_assistant_message(self, answer: str) -> None:
        if self.chat_view.is_finalized():
            return
        self.messages.append({"role": "assistant", "content": answer})
        self.conversation.model = self.active_model
        self.store.save(self.conversation)
        self.chat_view.mark_finalized()
        self._rebuild_sidebar()
        self.chat_view.attach_pending_actions(len(self.messages) - 1)
        self.chat_view.finalize_geometry()

    def _finalize_partial_response(self, stopped: bool = False) -> None:
        answer = self.chat_view.streamed_answer.strip()
        if stopped and answer:
            answer = f"{answer}\n\n_Response stopped._"
            self.chat_view.set_pending_text(answer)
        if answer:
            self._append_assistant_message(answer)
        else:
            self.chat_view.remove_thinking()
            self.store.save(self.conversation)

    def _response_failure_stage(self, error: str) -> str:
        lowered = error.casefold()
        if "cancel" in lowered:
            return "Generation"
        if self.chat_view.first_token_at is None:
            return "First Token"
        return "Generate"

    def _format_generation_error(self, error: str, failed_stage: str, partial: bool = False) -> str:
        stage = self._stage_display_name(failed_stage)
        if partial:
            return f"**Generation stopped**\n\nStage: {stage}\n\nError: {error}"
        return f"**Response failed**\n\nStage: {stage}\n\nError: {error}"

    def _stage_display_name(self, failed_stage: str) -> str:
        return {
            "First Token": "before first token",
            "Generate": "during streaming",
            "Generation": "generation",
        }.get(failed_stage, failed_stage or "generation")

    def _cleanup_worker(self) -> None:
        self.chat_view.clear_pending()
        self._set_controls_generating(False)
        if self.footer_status.text() in {"Connecting...", "Thinking...", "Generating..."}:
            self._set_status("Ready")
        self.input_box.setFocus()

    def _set_controls_generating(self, generating: bool) -> None:
        self.send_button.setEnabled(not generating)
        self.toolbar_send_button.setEnabled(not generating)
        self.sidebar.set_generating(generating)
        self.input_box.setEnabled(not generating)
        self.model_selector.setEnabled(not generating)
        self.toolbar_model_selector.setEnabled(not generating)
        self.tools_button.setEnabled(not generating)
        self.toolbar_tools_button.setEnabled(not generating)
        if self._composer_multiline:
            self.stop_button.hide()
            self.send_button.hide()
            self.toolbar_stop_button.setVisible(generating)
            self.toolbar_send_button.setVisible(not generating)
        else:
            self.toolbar_stop_button.hide()
            self.toolbar_send_button.hide()
            self.stop_button.setVisible(generating)
            self.send_button.setVisible(not generating)

    def _refresh_models(self) -> None:
        self.model_manager.start()

    def _models_discovered(self, models: list[str]) -> None:
        self.available_models = models
        self._set_model_selector(models)

    def _models_failed(self, _error: str) -> None:
        self._set_model_selector(self.available_models)

    def _set_model_selector(self, models: list[str]) -> None:
        current = self.active_model or DEFAULT_MODEL_NAME
        values = list(models)
        if current not in values:
            values.insert(0, current)
        self.model_selector.blockSignals(True)
        self.toolbar_model_selector.blockSignals(True)
        self.model_selector.clear()
        self.toolbar_model_selector.clear()
        self.model_selector.addItems(values or [current])
        self.toolbar_model_selector.addItems(values or [current])
        self.model_selector.setCurrentText(current)
        self.toolbar_model_selector.setCurrentText(current)
        self.model_selector.blockSignals(False)
        self.toolbar_model_selector.blockSignals(False)

    def _model_changed(self, model_name: str) -> None:
        if not model_name or self.engine.running:
            return
        self.active_model = model_name
        self.preferences.selected_model = model_name
        self.preferences.save()
        self.conversation.model = model_name
        self.store.save(self.conversation)
        for selector in (self.model_selector, self.toolbar_model_selector):
            if selector.currentText() != model_name:
                selector.blockSignals(True)
                selector.setCurrentText(model_name)
                selector.blockSignals(False)

    def _set_status(self, text: str) -> None:
        if text == "Ready":
            self.footer_status.setText(APP_FOOTER_TEXT)
        elif text == "Error":
            self.footer_status.setText("Error - check the latest message.")
        else:
            self.footer_status.setText(f"{text}...")

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self.engine.shutdown(2500):
            event.ignore()
            if self.engine.thread is not None:
                self.engine.thread.finished.connect(self.close)
            return
        self.model_manager.shutdown(1000)
        self.chat_view.stop_middle_scroll()
        event.accept()

    def _apply_style(self) -> None:
        self.setStyleSheet(app_stylesheet(accent=self.preferences.theme_accent))
