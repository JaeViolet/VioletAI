"""Native desktop chat interface for VioletAI."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import sys
import os
from time import perf_counter

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QPropertyAnimation,
    QPoint,
    QSize,
    Signal,
    Slot,
    QThread,
    Qt,
    QTimer,
)
from PySide6.QtGui import QCloseEvent, QCursor, QFont, QResizeEvent
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QPushButton,
    QLayout,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from config import (
    APP_FOOTER_TEXT,
    APP_NAME,
    DEFAULT_MODEL_NAME,
    MEMORY_DB_PATH,
    MEMORY_LOG_PATH,
    SYSTEM_PROMPT,
)
from conversation_store import Conversation, ConversationStore
from design import Motion, PNG_CONTROL_ICON_SIZE, app_stylesheet, icon
from memory_manager import SettingsOverlay
from memory_v2.pipeline import MemorySystem
from memory_v2.store import MemoryStore
from ollama_client import ModelDiscoveryWorker, OllamaWorker
from preferences import Preferences
from prompts import build_ollama_messages
from sidebar import ChatSidebar, SearchOverlay
from widgets import (
    AutoGrowingInput,
    MessageActions,
    MessageBubble,
    ModelSelector,
    ThinkingBubble,
    apply_interaction_cursors,
)


@dataclass
class PreparedRequest:
    outcome: object
    ollama_messages: list[dict[str, str]]


class RequestPreparationWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)
    stopped = Signal()

    def __init__(
        self,
        memory_system: MemorySystem,
        messages: list[dict[str, str]],
        message: str,
        conversation_id: str,
        source_message_id: str,
        previous_user_text: str | None,
        system_prompt: str,
    ) -> None:
        super().__init__()
        self._memory_system = memory_system
        self._messages = messages
        self._message = message
        self._conversation_id = conversation_id
        self._source_message_id = source_message_id
        self._previous_user_text = previous_user_text
        self._system_prompt = system_prompt

    @Slot()
    def run(self) -> None:
        try:
            outcome = self._memory_system.handle_user_message(
                self._message,
                conversation_id=self._conversation_id,
                message_id=self._source_message_id,
                previous_user_text=self._previous_user_text,
            )
            relevant_memories = (
                [item.record for item in outcome.retrieval.selected] if outcome.retrieval is not None else []
            )
            ollama_messages = build_ollama_messages(self._messages, relevant_memories, self._system_prompt)
            self.finished.emit(PreparedRequest(outcome, ollama_messages))
        except Exception as error:
            self.failed.emit(str(error))
        finally:
            self.stopped.emit()


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
    NEAR_BOTTOM_PX = 90

    def __init__(self) -> None:
        super().__init__()
        self.preferences = Preferences()
        self.active_model = self.preferences.selected_model or DEFAULT_MODEL_NAME
        self.available_models: list[str] = []
        self.store = ConversationStore()
        self.memory_store = MemoryStore(MEMORY_DB_PATH)
        self.memory_service = MemorySystem(self.memory_store)
        self.conversation = self._load_or_create_conversation()
        self.messages = self.conversation.messages

        self.thread: QThread | None = None
        self.worker: OllamaWorker | None = None
        self.prep_thread: QThread | None = None
        self.prep_worker: RequestPreparationWorker | None = None
        self.model_thread: QThread | None = None
        self.model_worker: ModelDiscoveryWorker | None = None
        self.pending_bubble: MessageBubble | None = None
        self.pending_row: QWidget | None = None
        self.pending_column: QWidget | None = None
        self.thinking_row: QWidget | None = None
        self.streamed_answer = ""
        self.current_diagnostics: object | None = None
        self._ollama_request_started_at: float | None = None
        self._first_token_at: float | None = None
        self._prompt_ready_at: float | None = None
        self._finalized_current_response = False
        self._generation_cancel_requested = False
        self._scroll_animation: QPropertyAnimation | None = None
        self._programmatic_scroll = False
        self._auto_scroll_enabled = True
        self._middle_scroll_origin: QPoint | None = None
        self._middle_scroll_timer = QTimer(self)
        self._middle_scroll_timer.setInterval(16)
        self._middle_scroll_timer.timeout.connect(self._perform_middle_scroll)

        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(Motion.STREAM_INTERVAL)
        self._render_timer.timeout.connect(self._render_stream)
        self._composer_multiline = False
        self._updating_composer_mode = False
        self._composer_edit_sequence = 0
        self._composer_state_changes_this_edit = 0
        self._composer_total_state_changes = 0
        self._composer_layout_diagnostics: list[dict[str, object]] = []
        self._bulk_rebuilding_messages = False
        self._message_rebuild_active = False
        self._rebuild_finish_active = False
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

        self.message_container = QWidget(objectName="messageContainer")
        self.message_layout = QVBoxLayout(self.message_container)
        self.message_layout.setContentsMargins(24, 28, 24, 24)
        self.message_layout.setSpacing(22)
        self.message_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinAndMaxSize)
        self.message_layout.addStretch(1)

        self.scroll_area = QScrollArea(objectName="chatScroll")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.verticalScrollBar().valueChanged.connect(self._handle_scroll_change)
        self.scroll_area.verticalScrollBar().rangeChanged.connect(self._rebuild_range_changed)
        self.scroll_area.setWidget(self.message_container)
        self.scroll_area.viewport().installEventFilter(self)
        main_layout.addWidget(self.scroll_area, 1)

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
        tools = [
            "Web Search",
            "Upload Files",
            "Upload Images",
            "Deep Research",
            "Image Generation",
        ]
        for tool_name in tools:
            action = menu.addAction(f"{tool_name} - Coming soon")
            action.setEnabled(False)
            action.setData(tool_name)
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
                self.toolbar_send_button.setVisible(self.thread is None)
                self.toolbar_stop_button.setVisible(self.thread is not None)
                self.toolbar_widget.setMaximumHeight(16_777_215)
                self.toolbar_widget.show()
            else:
                self.composer_layout.setContentsMargins(12, 4, 6, 4)
                self.tools_button.show()
                self.model_selector.show()
                self.send_button.setVisible(self.thread is None)
                self.stop_button.setVisible(self.thread is not None)
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
            if self.thread is None:
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
        visible_controls = (self.tools_button, self.model_selector, self.send_button if self.thread is None else self.stop_button)
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

    def _make_welcome(self) -> QWidget:
        row = QWidget()
        row.setProperty("messageRow", True)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(0)

        content = QWidget(objectName="welcomeContentColumn")
        content.setProperty("welcomeContentColumn", True)
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        welcome = QWidget(objectName="welcome")
        layout = QVBoxLayout(welcome)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)
        icon = QLabel(APP_NAME, objectName="welcomeIcon")
        title = QLabel("How can I help you today?", objectName="welcomeTitle")
        subtitle = QLabel("Private, local, and running on your machine.", objectName="welcomeSubtitle")
        for label in (icon, title, subtitle):
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(label)
        content_layout.addWidget(welcome)
        row.setProperty("contentColumn", content)
        row_layout.addStretch(1)
        row_layout.addWidget(content)
        row_layout.addStretch(1)
        return row

    def _clear_message_rows(self) -> None:
        while self.message_layout.count() > 1:
            item = self.message_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.pending_bubble = None
        self.pending_row = None
        self.pending_column = None
        self.thinking_row = None

    def _rebuild_messages(self) -> None:
        self._message_rebuild_active = True
        self.setUpdatesEnabled(False)
        try:
            self._clear_message_rows()
            visible_messages = [
                (index, message)
                for index, message in enumerate(self.messages)
                if message.get("role") != "system"
            ]
            if not visible_messages:
                self.message_layout.insertStretch(0, 2)
                self.message_layout.insertWidget(1, self._make_welcome())
                self.message_layout.insertStretch(2, 4)
            else:
                self._bulk_rebuilding_messages = True
                try:
                    for index, message in visible_messages:
                        self._add_message(message.get("content", ""), message.get("role", "assistant"), index)
                finally:
                    self._bulk_rebuilding_messages = False
            self._resize_rows()
        finally:
            self._message_rebuild_active = False
            self.setUpdatesEnabled(True)
        QTimer.singleShot(0, self._scroll_to_rebuild_bottom)
        QTimer.singleShot(30, self._scroll_to_rebuild_bottom)

    def _scroll_to_rebuild_bottom(self) -> None:
        bar = self.scroll_area.verticalScrollBar()
        if bar is None:
            return
        self._rebuild_finish_active = True
        self._programmatic_scroll = True
        bar.setValue(bar.maximum())
        self._programmatic_scroll = False
        QTimer.singleShot(60, self._release_rebuild_scroll_lock)

    def _release_rebuild_scroll_lock(self) -> None:
        self._rebuild_finish_active = False
        bar = self.scroll_area.verticalScrollBar()
        if bar is not None and bar.maximum() != bar.value():
            self._programmatic_scroll = True
            bar.setValue(bar.maximum())
            self._programmatic_scroll = False

    def _rebuild_range_changed(self, _minimum: int, _maximum: int) -> None:
        if not self._rebuild_finish_active:
            return
        bar = self.scroll_area.verticalScrollBar()
        if bar is not None:
            self._programmatic_scroll = True
            bar.setValue(bar.maximum())
            self._programmatic_scroll = False

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
        if not hasattr(self, "scroll_area"):
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
        if watched is self.scroll_area.viewport() and event.type() == QEvent.Type.Resize:
            if not self._message_rebuild_active:
                QTimer.singleShot(0, self._resize_rows)
        if watched is self.scroll_area.viewport() and event.type() == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.MiddleButton:
                self._toggle_middle_scroll(event.position().toPoint())
                return True
            if self._middle_scroll_timer.isActive():
                self._stop_middle_scroll()
        if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            self._stop_middle_scroll()
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
        viewport_width = self.scroll_area.viewport().width()
        available = max(280, min(self.CONTENT_MAX_WIDTH, viewport_width - 96))
        if self.composer.width() != available:
            self.composer.setFixedWidth(available)
        if hasattr(self, "input_panel") and self.input_panel.layout() is not None:
            self.input_panel.layout().activate()
        if hasattr(self, "input_box"):
            self.input_box._update_height()
        self._schedule_composer_mode_update()
        for index in range(self.message_layout.count() - 1):
            row = self.message_layout.itemAt(index).widget()
            if row and row.property("messageRow"):
                content = row.property("contentColumn")
                if isinstance(content, QWidget):
                    content.setMinimumWidth(available)
                    content.setMaximumWidth(available)
                bubble = row.property("bubble")
                if isinstance(bubble, MessageBubble):
                    max_width = int(available * 2 / 3) if bubble.role == "user" else available
                    if bubble.role == "user":
                        compact_width = bubble.preferred_width(max_width)
                        bubble.setFixedWidth(compact_width)
                        parent = bubble.parentWidget()
                        if parent is not None:
                            parent.setMinimumWidth(compact_width)
                            parent.setMaximumWidth(compact_width)
                    else:
                        parent = bubble.parentWidget()
                        if parent is not None:
                            parent.setMinimumWidth(min(260, max_width))
                            parent.setMaximumWidth(max_width)
                        bubble.setMinimumWidth(min(260, max_width))
                        bubble.setMaximumWidth(max_width)
                    bubble.updateGeometry()
                    self._settle_message_row(row, bubble)
                else:
                    self._settle_content_row(row, content if isinstance(content, QWidget) else None)
        self.message_layout.activate()
        self.message_container.setMinimumHeight(self.message_layout.sizeHint().height())
        self.message_container.updateGeometry()

    def _settle_message_row(self, row: QWidget, bubble: MessageBubble) -> None:
        column = bubble.parentWidget()
        content = row.property("contentColumn")
        if column is not None:
            if column.layout() is not None:
                column.layout().activate()
            column.setMinimumHeight(column.sizeHint().height())
            column.updateGeometry()
        if isinstance(content, QWidget):
            if content.layout() is not None:
                content.layout().activate()
            content.setMinimumHeight(content.sizeHint().height())
            content.updateGeometry()
        if row.layout() is not None:
            row.layout().activate()
        row.setMinimumHeight(row.sizeHint().height())
        row.updateGeometry()

    def _settle_content_row(self, row: QWidget, content: QWidget | None) -> None:
        if content is not None:
            if content.layout() is not None:
                content.layout().activate()
            content.setMinimumHeight(content.sizeHint().height())
            content.updateGeometry()
        if row.layout() is not None:
            row.layout().activate()
        row.setMinimumHeight(row.sizeHint().height())
        row.updateGeometry()

    def _add_message(self, text: str, role: str, message_index: int | None = None) -> MessageBubble:
        row = QWidget()
        row.setProperty("messageRow", True)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(0)

        content = QWidget()
        content.setObjectName("messageContentColumn")
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        column = QWidget()
        if role == "user":
            column.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Minimum)
        else:
            column.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        column_layout = QVBoxLayout(column)
        column_layout.setContentsMargins(0, 0, 0, 0)
        column_layout.setSpacing(6)
        if role == "user":
            column_layout.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)
        bubble = MessageBubble(text, role)
        row.setProperty("bubble", bubble)
        row.setProperty("contentColumn", content)
        column_layout.addWidget(bubble)
        if role == "assistant" and message_index is not None:
            self._attach_actions(column_layout, bubble, message_index)

        if role == "user":
            content_layout.addStretch()
            content_layout.addWidget(column)
        else:
            content_layout.addWidget(column, 1)
            content_layout.addStretch()
        row_layout.addStretch(1)
        row_layout.addWidget(content)
        row_layout.addStretch(1)
        self.message_layout.insertWidget(self.message_layout.count() - 1, row)
        apply_interaction_cursors(row)
        self._animate_appearance(row)
        if not self._bulk_rebuilding_messages:
            self._resize_rows()
        return bubble

    def _toggle_middle_scroll(self, position: QPoint) -> None:
        if self._middle_scroll_timer.isActive():
            self._stop_middle_scroll()
            return
        self._middle_scroll_origin = position
        self.scroll_area.viewport().setCursor(Qt.CursorShape.SizeVerCursor)
        self._middle_scroll_timer.start()

    def _perform_middle_scroll(self) -> None:
        if self._middle_scroll_origin is None:
            return
        local_pos = self.scroll_area.viewport().mapFromGlobal(QCursor.pos())
        delta = local_pos.y() - self._middle_scroll_origin.y()
        if abs(delta) < 8:
            return
        bar = self.scroll_area.verticalScrollBar()
        bar.setValue(bar.value() + int(delta / 8))

    def _stop_middle_scroll(self) -> None:
        self._middle_scroll_timer.stop()
        self._middle_scroll_origin = None
        self.scroll_area.viewport().unsetCursor()

    def _animate_appearance(self, row: QWidget) -> None:
        row.setGraphicsEffect(None)

    def _attach_actions(self, layout: QVBoxLayout, bubble: MessageBubble, message_index: int) -> None:
        actions = MessageActions()
        actions.copy_requested.connect(lambda: QApplication.clipboard().setText(bubble.text()))
        actions.regenerate_requested.connect(lambda: self.regenerate_response(message_index))
        layout.addWidget(actions)
        apply_interaction_cursors(actions)

    def _show_thinking(self) -> None:
        self._remove_thinking()
        row = QWidget()
        row.setProperty("messageRow", True)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        content = QWidget()
        content.setObjectName("messageContentColumn")
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.addWidget(ThinkingBubble())
        content_layout.addStretch()
        row.setProperty("contentColumn", content)
        layout.addStretch(1)
        layout.addWidget(content)
        layout.addStretch(1)
        self.message_layout.insertWidget(self.message_layout.count() - 1, row)
        self.thinking_row = row
        self._resize_rows()
        self._scroll_to_bottom()

    def _remove_thinking(self) -> None:
        if self.thinking_row is not None:
            self.message_layout.removeWidget(self.thinking_row)
            self.thinking_row.deleteLater()
            self.thinking_row = None

    def _handle_scroll_change(self, _value: int) -> None:
        if self._programmatic_scroll:
            return
        self._auto_scroll_enabled = self._is_near_bottom()

    def _is_near_bottom(self) -> bool:
        bar = self.scroll_area.verticalScrollBar()
        return bar.maximum() - bar.value() <= self.NEAR_BOTTOM_PX

    def _scroll_to_bottom(self, smooth: bool = False) -> None:
        QTimer.singleShot(0, lambda: self._perform_scroll(smooth))

    def _perform_scroll(self, smooth: bool) -> None:
        bar = self.scroll_area.verticalScrollBar()
        target = bar.maximum()
        self._programmatic_scroll = True
        try:
            if not smooth or abs(target - bar.value()) < 30:
                bar.setValue(target)
                return
            if self._scroll_animation is not None:
                self._scroll_animation.stop()
            self._scroll_animation = QPropertyAnimation(bar, b"value", self)
            self._scroll_animation.setDuration(120)
            self._scroll_animation.setStartValue(bar.value())
            self._scroll_animation.setEndValue(target)
            self._scroll_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._scroll_animation.finished.connect(lambda: setattr(self, "_programmatic_scroll", False))
            self._scroll_animation.start()
        finally:
            if self._scroll_animation is None or self._scroll_animation.state() != QPropertyAnimation.State.Running:
                self._programmatic_scroll = False

    def new_chat(self) -> None:
        if self.prep_thread is not None:
            return
        if self.thread is not None:
            self.stop_generation()
            return
        self.conversation = self.store.create(SYSTEM_PROMPT, self.active_model)
        self.messages = self.conversation.messages
        self.input_box.clear()
        self.streamed_answer = ""
        self._set_status("Ready")
        self._rebuild_sidebar()
        self._rebuild_messages()

    def select_conversation(self, conversation_id: str) -> None:
        if self.thread is not None or self.prep_thread is not None:
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
        if not message or self.thread is not None or self.prep_thread is not None:
            return
        previous_user_text = next(
            (item.get("content", "") for item in reversed(self.messages) if item.get("role") == "user"),
            None,
        )
        self.input_box.remember_prompt(message)
        source_message_id = str(len(self.messages))
        self._add_message(message, "user", len(self.messages))
        self.messages.append({"role": "user", "content": message})
        self.conversation.model = self.active_model
        self.store.save(self.conversation)
        self._rebuild_sidebar()
        self.input_box.clear()
        self._scroll_to_bottom()
        self._start_request_preparation(message, source_message_id, previous_user_text)

    def _start_request_preparation(
        self,
        message: str,
        source_message_id: str,
        previous_user_text: str | None,
    ) -> None:
        self.streamed_answer = ""
        self.pending_bubble = None
        self.pending_row = None
        self.pending_column = None
        self._ollama_request_started_at = None
        self._first_token_at = None
        self._prompt_ready_at = None
        self._finalized_current_response = False
        self._generation_cancel_requested = False
        self._auto_scroll_enabled = True
        self._show_thinking()
        self._set_controls_preparing(True)
        self._set_status("Thinking")

        self.prep_thread = QThread(self)
        self.prep_worker = RequestPreparationWorker(
            self.memory_service,
            [dict(item) for item in self.messages],
            message,
            self.conversation.id,
            source_message_id,
            previous_user_text,
            SYSTEM_PROMPT,
        )
        self.prep_worker.moveToThread(self.prep_thread)
        self.prep_thread.started.connect(self.prep_worker.run)
        self.prep_worker.finished.connect(self._receive_prepared_request)
        self.prep_worker.failed.connect(self._request_preparation_failed)
        self.prep_worker.stopped.connect(self.prep_worker.deleteLater)
        self.prep_worker.stopped.connect(self.prep_thread.quit)
        self.prep_thread.finished.connect(self._cleanup_prep_worker)
        self.prep_thread.start()

    def _receive_prepared_request(self, prepared: PreparedRequest) -> None:
        self._record_memory_mutation(prepared.outcome)
        self._start_generation(prepared.ollama_messages, prompt_already_recorded=True, show_thinking=False)

    def _record_memory_mutation(self, outcome: object) -> None:
        if os.environ.get("VIOLETAI_MEMORY_LOG") != "1":
            return
        action = getattr(outcome, "action", None)
        if action is None:
            return
        kind = getattr(action, "value", str(action))
        status = getattr(getattr(outcome, "action_status", None), "value", "?")
        retrieval = getattr(outcome, "retrieval", None)
        injected = bool(getattr(retrieval, "injected", False))
        try:
            MEMORY_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(MEMORY_LOG_PATH, "a", encoding="utf-8") as handle:
                handle.write(f"{datetime.now(UTC).isoformat()} action={kind} status={status} injected={injected}\n")
        except OSError:
            pass

    def _request_preparation_failed(self, error: str) -> None:
        render_started = perf_counter()
        self._remove_thinking()
        message = f"**Response failed**\n\nStage: request preparation\n\nError: {error}"
        self._add_message(message, "error")
        self._record_diagnostics_elapsed("render_ms", render_started)
        self._finalize_diagnostics(message, "Memory Pipeline", error)
        self._set_controls_preparing(False)
        self._set_status("Error")
        self._scroll_to_bottom()

    def _cleanup_prep_worker(self) -> None:
        if self.prep_thread is not None:
            self.prep_thread.deleteLater()
        self.prep_thread = None
        self.prep_worker = None

    def _refresh_settings_if_visible(self) -> None:
        if self.settings_overlay.isVisible():
            QTimer.singleShot(0, self.settings_overlay.refresh)

    def _append_assistant_direct(self, answer: str) -> MessageBubble:
        bubble = self._add_message(answer, "assistant", len(self.messages))
        self.messages.append({"role": "assistant", "content": answer})
        self.conversation.model = self.active_model
        self.store.save(self.conversation)
        self._rebuild_sidebar()
        return bubble

    def regenerate_response(self, message_index: int) -> None:
        if self.thread is not None or self.prep_thread is not None:
            return
        if not 0 <= message_index < len(self.messages):
            return
        if self.messages[message_index].get("role") != "assistant":
            return
        self.messages[:] = self.messages[:message_index]
        self.store.save(self.conversation)
        self._rebuild_messages()
        self._start_generation()

    def _start_generation(
        self,
        ollama_messages: list[dict[str, str]] | None = None,
        prompt_already_recorded: bool = False,
        show_thinking: bool = True,
    ) -> None:
        self.streamed_answer = ""
        self.pending_bubble = None
        self.pending_row = None
        self._ollama_request_started_at = None
        self._first_token_at = None
        self._prompt_ready_at = None
        self._finalized_current_response = False
        self._generation_cancel_requested = False
        self._auto_scroll_enabled = True
        if show_thinking:
            self._show_thinking()
        self._set_controls_generating(True)
        self._set_status("Connecting")

        self.thread = QThread(self)
        if ollama_messages is None:
            last_user_message = next(
                (message.get("content", "") for message in reversed(self.messages) if message.get("role") == "user"),
                "",
            )
            retrieval = self.memory_service.retrieve(
                last_user_message,
                token_counter=self.memory_service.temporary.token_counter,
                conversation_index=self.memory_service.temporary.conversation_index,
            )
            relevant_memories = [item.record for item in retrieval.selected]
            prompt_started = perf_counter()
            ollama_messages = build_ollama_messages(self.messages, relevant_memories, SYSTEM_PROMPT)
            if not prompt_already_recorded:
                self._record_diagnostics_elapsed("prompt_ms", prompt_started)
        self._prompt_ready_at = perf_counter()
        self.worker = OllamaWorker(
            ollama_messages,
            self.active_model,
            diagnostic_callback=self._record_ollama_diagnostic_event,
            think=False,
        )
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.request_started.connect(self._diagnostics_ollama_started)
        self.worker.connected.connect(lambda: self._set_status("Thinking"))
        self.worker.chunk_received.connect(self._receive_chunk)
        self.worker.finished.connect(self._receive_response)
        self.worker.cancelled.connect(self._receive_cancelled)
        self.worker.failed.connect(self._receive_error)
        self.worker.stopped.connect(self.worker.deleteLater)
        self.worker.stopped.connect(self.thread.quit)
        self.thread.finished.connect(self._cleanup_worker)
        self.thread.start()

    def _diagnostics_ollama_started(self) -> None:
        now = perf_counter()
        self._ollama_request_started_at = now
        if self._prompt_ready_at is not None:
            self._record_diagnostics_value("ollama_start_ms", (now - self._prompt_ready_at) * 1000)

    def _record_diagnostics_elapsed(self, key: str, started_at: float) -> None:
        if self.current_diagnostics is not None:
            self.current_diagnostics.record_elapsed(key, started_at)

    def _record_diagnostics_value(self, key: str, milliseconds: float) -> None:
        if self.current_diagnostics is not None:
            self.current_diagnostics.record(**{key: round(milliseconds, 3)})

    def _record_ollama_diagnostic_event(self, event: dict[str, object]) -> None:
        if self.current_diagnostics is None:
            return
        request_kind = str(event.get("request_kind") or "chat")
        key_name = "post_memory_events" if request_kind == "post_memory" else "ollama_events"
        existing = list(self.current_diagnostics.data.get(key_name, []))
        sanitized = {
            key: value
            for key, value in event.items()
            if key
            in {
                "event",
                "request_kind",
                "model",
                "message_count",
                "roles",
                "message_lengths",
                "status_code",
                "done",
                "event_count",
                "empty_event_count",
                "visible_content_length",
                "time_to_first_event_ms",
                "time_to_first_visible_token_ms",
                "cancellation_requested",
                "source",
                "stage",
                "message",
                "think",
                "options",
                "elapsed_ms",
                "error",
            }
        }
        existing.append(sanitized)
        self.current_diagnostics.record(**{key_name: existing})

    def _finalize_diagnostics(
        self,
        assistant_response: str,
        failed_stage: str | None = None,
        error: str | None = None,
    ) -> None:
        if self.current_diagnostics is not None:
            self.current_diagnostics.finalize(assistant_response, failed_stage, error)
            self.current_diagnostics = None

    def stop_generation(self) -> None:
        if self.worker is None:
            return
        self._generation_cancel_requested = True
        self._set_status("Stopped")
        self.worker.cancel()
        self._render_stream(force=True)
        self._finalize_partial_response(stopped=True)

    def _receive_chunk(self, chunk: str) -> None:
        if self._generation_cancel_requested:
            return
        now = perf_counter()
        if self._first_token_at is None:
            self._first_token_at = now
            if self._ollama_request_started_at is not None:
                self._record_diagnostics_value("first_token_ms", (now - self._ollama_request_started_at) * 1000)
        if self.pending_bubble is None:
            self._remove_thinking()
            self.pending_bubble = self._add_message("", "assistant")
            self.pending_row = self.pending_bubble.parentWidget().parentWidget()
            self.pending_column = self.pending_bubble.parentWidget()
        self.streamed_answer += chunk
        self._set_status("Generating")
        if not self._render_timer.isActive():
            self._render_timer.start()

    def _render_stream(self, force: bool = False) -> None:
        if self.pending_bubble is not None:
            self.pending_bubble.set_text(self.streamed_answer)
            self._finalize_message_geometry()
            if self._auto_scroll_enabled or force:
                self._scroll_to_bottom(smooth=True)

    def _receive_response(self, answer: str) -> None:
        if self._generation_cancel_requested:
            return
        generation_finished_at = perf_counter()
        if self._first_token_at is not None:
            self._record_diagnostics_value("generate_ms", (generation_finished_at - self._first_token_at) * 1000)
        render_started = perf_counter()
        self._render_timer.stop()
        self._remove_thinking()
        if self.pending_bubble is None:
            self.pending_bubble = self._add_message(answer, "assistant")
            self.pending_row = self.pending_bubble.parentWidget().parentWidget()
            self.pending_column = self.pending_bubble.parentWidget()
        else:
            self.pending_bubble.set_text(answer)
        self._finalize_message_geometry()
        self._append_assistant_message(answer)
        if self._auto_scroll_enabled:
            self._scroll_to_bottom(smooth=True)
        self._set_status("Ready")
        self._record_diagnostics_elapsed("render_ms", render_started)
        self._finalize_diagnostics(answer)

    def _receive_cancelled(self) -> None:
        generation_finished_at = perf_counter()
        if self._first_token_at is not None:
            self._record_diagnostics_value("generate_ms", (generation_finished_at - self._first_token_at) * 1000)
        render_started = perf_counter()
        self._set_status("Stopped")
        self._finalize_partial_response(stopped=True)
        self._record_diagnostics_elapsed("render_ms", render_started)
        self._finalize_diagnostics(self.streamed_answer.strip(), self._response_failure_stage("cancelled"), "cancelled")

    def _receive_error(self, error: str) -> None:
        if self._generation_cancel_requested:
            return
        failed_stage = self._response_failure_stage(error)
        generation_finished_at = perf_counter()
        if self._first_token_at is not None:
            self._record_diagnostics_value("generate_ms", (generation_finished_at - self._first_token_at) * 1000)
        render_started = perf_counter()
        self._render_timer.stop()
        self._remove_thinking()
        if self.pending_bubble is not None and self.streamed_answer.strip():
            self.pending_bubble.set_text(self.streamed_answer)
            self._finalize_partial_response(stopped=True)
            error_message = self._format_generation_error(error, failed_stage, partial=True)
            assistant_for_diagnostics = self.streamed_answer.strip()
        else:
            error_message = self._format_generation_error(error, failed_stage)
            assistant_for_diagnostics = error_message
        self._add_message(error_message, "error")
        self.store.save(self.conversation)
        self._set_status("Error")
        self._scroll_to_bottom()
        self._record_diagnostics_elapsed("render_ms", render_started)
        self._finalize_diagnostics(assistant_for_diagnostics, failed_stage, error)

    def _append_assistant_message(self, answer: str) -> None:
        if self._finalized_current_response:
            return
        self.messages.append({"role": "assistant", "content": answer})
        self.conversation.model = self.active_model
        self.store.save(self.conversation)
        self._finalized_current_response = True
        self._rebuild_sidebar()
        if self.pending_row is not None and self.pending_bubble is not None:
            message_index = len(self.messages) - 1
            column = self.pending_bubble.parentWidget()
            if isinstance(column, QWidget) and column.layout() is not None:
                self._attach_actions(column.layout(), self.pending_bubble, message_index)
        self._finalize_message_geometry()

    def _finalize_message_geometry(self) -> None:
        if self.pending_bubble is not None:
            self.pending_bubble.layout.invalidate()
            self.pending_bubble.adjustSize()
        self.message_container.layout().activate()
        self.message_container.adjustSize()
        self._resize_rows()

    def _finalize_partial_response(self, stopped: bool = False) -> None:
        answer = self.streamed_answer.strip()
        if stopped and answer:
            answer = f"{answer}\n\n_Response stopped._"
            if self.pending_bubble is not None:
                self.pending_bubble.set_text(answer)
        if answer:
            self._append_assistant_message(answer)
        else:
            self._remove_thinking()
            self.store.save(self.conversation)

    def _response_failure_stage(self, error: str) -> str:
        lowered = error.casefold()
        if "cancel" in lowered:
            return "Generation"
        if self._first_token_at is None:
            return "First Token"
        if self.streamed_answer.strip():
            return "Generate"
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
        if self.thread is not None:
            self.thread.deleteLater()
        self.worker = None
        self.thread = None
        self.pending_bubble = None
        self.pending_row = None
        self.pending_column = None
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

    def _set_controls_preparing(self, preparing: bool) -> None:
        self.send_button.setEnabled(not preparing)
        self.toolbar_send_button.setEnabled(not preparing)
        self.sidebar.set_generating(preparing)
        self.input_box.setEnabled(not preparing)
        self.model_selector.setEnabled(not preparing)
        self.toolbar_model_selector.setEnabled(not preparing)
        self.tools_button.setEnabled(not preparing)
        self.toolbar_tools_button.setEnabled(not preparing)
        self.stop_button.hide()
        self.toolbar_stop_button.hide()
        if self._composer_multiline:
            self.send_button.hide()
            self.toolbar_send_button.setVisible(not preparing)
        else:
            self.toolbar_send_button.hide()
            self.send_button.setVisible(not preparing)

    def _refresh_models(self) -> None:
        if self.model_thread is not None:
            return
        self.model_thread = QThread(self)
        self.model_worker = ModelDiscoveryWorker()
        self.model_worker.moveToThread(self.model_thread)
        self.model_thread.started.connect(self.model_worker.run)
        self.model_worker.finished.connect(self._models_discovered)
        self.model_worker.failed.connect(self._models_failed)
        self.model_worker.stopped.connect(self.model_worker.deleteLater)
        self.model_worker.stopped.connect(self.model_thread.quit)
        self.model_thread.finished.connect(self._cleanup_model_worker)
        self.model_thread.start()

    def _models_discovered(self, models: list[str]) -> None:
        self.available_models = models
        self._set_model_selector(models)

    def _models_failed(self, _error: str) -> None:
        self._set_model_selector(self.available_models)

    def _cleanup_model_worker(self) -> None:
        if self.model_thread is not None:
            self.model_thread.deleteLater()
        self.model_thread = None
        self.model_worker = None

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
        if not model_name or self.thread is not None or self.prep_thread is not None:
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
        if self.worker is not None:
            self.worker.cancel()
        if self.prep_thread is not None:
            self.prep_thread.quit()
            if not self.prep_thread.wait(2500):
                event.ignore()
                self.prep_thread.finished.connect(self.close)
                return
        if self.thread is not None:
            self.thread.quit()
            if not self.thread.wait(2500):
                event.ignore()
                self.thread.finished.connect(self.close)
                return
        if self.model_thread is not None:
            self.model_thread.quit()
            self.model_thread.wait(1000)
        self._stop_middle_scroll()
        event.accept()

    def _apply_style(self) -> None:
        self.setStyleSheet(app_stylesheet(accent=self.preferences.theme_accent))


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
    font = QFont("Segoe UI Variable", 10)
    app.setFont(font)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
