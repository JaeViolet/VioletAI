"""Native desktop chat interface for VioletAI."""

from __future__ import annotations

import sys

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QPropertyAnimation,
    QThread,
    Qt,
    QTimer,
)
from PySide6.QtGui import QCloseEvent, QFont, QResizeEvent
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QScrollArea,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from config import APP_FOOTER_TEXT, APP_NAME, DEFAULT_MODEL_NAME, SYSTEM_PROMPT
from conversation_store import Conversation, ConversationStore
from ollama_client import ModelDiscoveryWorker, OllamaWorker
from preferences import Preferences
from sidebar import ChatSidebar
from widgets import AutoGrowingInput, MessageActions, MessageBubble, ThinkingBubble


class MainWindow(QMainWindow):
    CONTENT_MAX_WIDTH = 850
    NEAR_BOTTOM_PX = 90

    def __init__(self) -> None:
        super().__init__()
        self.preferences = Preferences()
        self.active_model = self.preferences.selected_model or DEFAULT_MODEL_NAME
        self.available_models: list[str] = []
        self.store = ConversationStore()
        self.conversation = self._load_or_create_conversation()
        self.messages = self.conversation.messages

        self.thread: QThread | None = None
        self.worker: OllamaWorker | None = None
        self.model_thread: QThread | None = None
        self.model_worker: ModelDiscoveryWorker | None = None
        self.pending_bubble: MessageBubble | None = None
        self.pending_row: QWidget | None = None
        self.thinking_row: QWidget | None = None
        self.streamed_answer = ""
        self._finalized_current_response = False
        self._generation_cancel_requested = False
        self._scroll_animation: QPropertyAnimation | None = None
        self._programmatic_scroll = False
        self._auto_scroll_enabled = True

        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(24)
        self._render_timer.timeout.connect(self._render_stream)

        self.setWindowTitle(APP_NAME)
        self.resize(1180, 780)
        self.setMinimumSize(760, 520)
        self._build_interface()
        self._apply_style()
        self._rebuild_sidebar()
        self._rebuild_messages()
        self._refresh_models()
        self.input_box.setFocus()

    def _load_or_create_conversation(self) -> Conversation:
        conversation = self.store.load_latest()
        if conversation is None:
            conversation = self.store.create(SYSTEM_PROMPT, self.active_model)
            self.store.save(conversation)
        if not conversation.messages or conversation.messages[0].get("role") != "system":
            conversation.messages.insert(0, {"role": "system", "content": SYSTEM_PROMPT})
        if conversation.model:
            self.active_model = conversation.model
            self.preferences.selected_model = self.active_model
            self.preferences.save()
        return conversation

    def _build_interface(self) -> None:
        central = QWidget(objectName="centralWidget")
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.sidebar = ChatSidebar()
        self.sidebar.new_chat_requested.connect(self.new_chat)
        self.sidebar.conversation_selected.connect(self.select_conversation)
        self.sidebar.rename_requested.connect(self.rename_conversation)
        self.sidebar.delete_requested.connect(self.delete_conversation)
        self.sidebar.search_changed.connect(lambda _text: self._rebuild_sidebar())
        root.addWidget(self.sidebar)

        main_panel = QFrame(objectName="mainPanel")
        main_layout = QVBoxLayout(main_panel)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        root.addWidget(main_panel, 1)

        header = QFrame(objectName="header")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(14, 13, 22, 13)
        header_layout.setSpacing(10)
        self.sidebar_toggle = QToolButton(objectName="headerIconButton")
        self.sidebar_toggle.setToolTip("Toggle sidebar")
        self.sidebar_toggle.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_TitleBarMenuButton))
        self.sidebar_toggle.clicked.connect(self.sidebar.toggle)
        title = QLabel(APP_NAME, objectName="title")
        self.status = QLabel("Ready", objectName="statusBadge")
        header_layout.addWidget(self.sidebar_toggle)
        header_layout.addWidget(title)
        header_layout.addStretch()
        header_layout.addWidget(self.status)
        main_layout.addWidget(header)

        self.message_container = QWidget(objectName="messageContainer")
        self.message_layout = QVBoxLayout(self.message_container)
        self.message_layout.setContentsMargins(24, 28, 24, 24)
        self.message_layout.setSpacing(22)
        self.message_layout.addStretch(1)

        self.scroll_area = QScrollArea(objectName="chatScroll")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.verticalScrollBar().valueChanged.connect(self._handle_scroll_change)
        self.scroll_area.setWidget(self.message_container)
        self.scroll_area.viewport().installEventFilter(self)
        main_layout.addWidget(self.scroll_area, 1)

        input_panel = QFrame(objectName="inputPanel")
        input_outer = QHBoxLayout(input_panel)
        input_outer.setContentsMargins(24, 8, 24, 14)
        input_outer.addStretch()

        self.composer = QFrame(objectName="composer")
        self.composer.setMaximumWidth(self.CONTENT_MAX_WIDTH)
        composer_layout = QHBoxLayout(self.composer)
        composer_layout.setContentsMargins(14, 5, 6, 5)
        composer_layout.setSpacing(8)
        composer_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self.input_box = AutoGrowingInput()
        self.input_box.send_requested.connect(self.send_message)
        self.model_selector = QComboBox(objectName="modelSelector")
        self.model_selector.setToolTip("Select local Ollama model")
        self.model_selector.currentTextChanged.connect(self._model_changed)
        self.send_button = QToolButton(objectName="sendButton")
        self.send_button.setToolTip("Send message")
        self.send_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowUp))
        self.send_button.clicked.connect(self.send_message)
        self.stop_button = QToolButton(objectName="sendButton")
        self.stop_button.setToolTip("Stop generating")
        self.stop_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaStop))
        self.stop_button.clicked.connect(self.stop_generation)
        self.stop_button.hide()

        composer_layout.addWidget(self.input_box, 1)
        composer_layout.addWidget(self.model_selector)
        composer_layout.addWidget(self.stop_button)
        composer_layout.addWidget(self.send_button)
        input_outer.addWidget(self.composer, 1)
        input_outer.addStretch()

        footer = QLabel(APP_FOOTER_TEXT, objectName="helperText")
        footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        panel_layout = QVBoxLayout()
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(5)
        panel_layout.addWidget(input_panel)
        panel_layout.addWidget(footer)
        main_layout.addLayout(panel_layout)
        main_layout.addSpacing(10)
        self.setCentralWidget(central)

    def _make_welcome(self) -> QWidget:
        welcome = QWidget(objectName="welcome")
        layout = QVBoxLayout(welcome)
        layout.setContentsMargins(0, 50, 0, 25)
        layout.setSpacing(7)
        icon = QLabel(APP_NAME, objectName="welcomeIcon")
        title = QLabel("How can I help you today?", objectName="welcomeTitle")
        subtitle = QLabel("Private, local, and running on your machine.", objectName="welcomeSubtitle")
        for label in (icon, title, subtitle):
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(label)
        return welcome

    def _clear_message_rows(self) -> None:
        while self.message_layout.count() > 1:
            item = self.message_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.pending_bubble = None
        self.pending_row = None
        self.thinking_row = None

    def _rebuild_messages(self) -> None:
        self._clear_message_rows()
        visible_messages = [
            (index, message)
            for index, message in enumerate(self.messages)
            if message.get("role") != "system"
        ]
        if not visible_messages:
            self.message_layout.insertWidget(0, self._make_welcome())
        else:
            for index, message in visible_messages:
                self._add_message(message.get("content", ""), message.get("role", "assistant"), index)
        QTimer.singleShot(0, self._resize_rows)
        self._scroll_to_bottom()

    def _rebuild_sidebar(self) -> None:
        self.sidebar.rebuild(
            self.store.grouped(self.sidebar.search.text()),
            self.conversation.id,
        )

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.scroll_area.viewport() and event.type() == QEvent.Type.Resize:
            QTimer.singleShot(0, self._resize_rows)
        return super().eventFilter(watched, event)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._resize_rows()

    def _resize_rows(self) -> None:
        viewport_width = self.scroll_area.viewport().width()
        available = max(280, min(self.CONTENT_MAX_WIDTH, viewport_width - 48))
        self.composer.setMaximumWidth(available)
        for index in range(self.message_layout.count() - 1):
            row = self.message_layout.itemAt(index).widget()
            if row and row.property("messageRow"):
                bubble = row.property("bubble")
                if isinstance(bubble, MessageBubble):
                    max_width = int(available * 0.82) if bubble.role == "user" else available
                    bubble.setMaximumWidth(max_width)
                    bubble.setMinimumWidth(min(260, max_width))
                    bubble.updateGeometry()

    def _add_message(self, text: str, role: str, message_index: int | None = None) -> MessageBubble:
        row = QWidget()
        row.setProperty("messageRow", True)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(0)

        column = QWidget()
        column_layout = QVBoxLayout(column)
        column_layout.setContentsMargins(0, 0, 0, 0)
        column_layout.setSpacing(6)
        bubble = MessageBubble(text, role)
        row.setProperty("bubble", bubble)
        column_layout.addWidget(bubble)
        if role == "assistant" and message_index is not None:
            self._attach_actions(column_layout, bubble, message_index)

        if role == "user":
            row_layout.addStretch()
            row_layout.addWidget(column)
        else:
            row_layout.addWidget(column, 1)
        self.message_layout.insertWidget(self.message_layout.count() - 1, row)
        self._animate_appearance(row)
        self._resize_rows()
        return bubble

    def _animate_appearance(self, row: QWidget) -> None:
        effect = QGraphicsOpacityEffect(row)
        row.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b"opacity", row)
        animation.setDuration(120)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    def _attach_actions(self, layout: QVBoxLayout, bubble: MessageBubble, message_index: int) -> None:
        actions = MessageActions()
        actions.copy_requested.connect(lambda: QApplication.clipboard().setText(bubble.text()))
        actions.regenerate_requested.connect(lambda: self.regenerate_response(message_index))
        layout.addWidget(actions)

    def _show_thinking(self) -> None:
        self._remove_thinking()
        row = QWidget()
        row.setProperty("messageRow", True)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(ThinkingBubble())
        layout.addStretch()
        self.message_layout.insertWidget(self.message_layout.count() - 1, row)
        self.thinking_row = row
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
        self._auto_scroll_enabled = True
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
        if self.thread is not None:
            self.stop_generation()
            return
        self.conversation = self.store.create(SYSTEM_PROMPT, self.active_model)
        self.store.save(self.conversation)
        self.messages = self.conversation.messages
        self.input_box.clear()
        self.streamed_answer = ""
        self._set_status("Ready")
        self._rebuild_sidebar()
        self._rebuild_messages()

    def select_conversation(self, conversation_id: str) -> None:
        if self.thread is not None:
            return
        conversation = self.store.load_by_id(conversation_id)
        if conversation is None:
            self._rebuild_sidebar()
            return
        self.conversation = conversation
        self.messages = self.conversation.messages
        self.active_model = self.conversation.model or self.active_model
        self._set_model_selector(self.available_models)
        self._set_status("Ready")
        self._rebuild_sidebar()
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

    def delete_conversation(self, conversation_id: str) -> None:
        self.store.delete(conversation_id)
        if conversation_id == self.conversation.id:
            latest = self.store.load_latest()
            self.conversation = latest or self.store.create(SYSTEM_PROMPT, self.active_model)
            if latest is None:
                self.store.save(self.conversation)
            self.messages = self.conversation.messages
            self._rebuild_messages()
        self._rebuild_sidebar()

    def send_message(self) -> None:
        message = self.input_box.toPlainText().strip()
        if not message or self.thread is not None:
            return
        self.input_box.remember_prompt(message)
        self._add_message(message, "user", len(self.messages))
        self.messages.append({"role": "user", "content": message})
        self.conversation.model = self.active_model
        self.store.save(self.conversation)
        self._rebuild_sidebar()
        self.input_box.clear()
        self._scroll_to_bottom()
        self._start_generation()

    def regenerate_response(self, message_index: int) -> None:
        if self.thread is not None:
            return
        if not 0 <= message_index < len(self.messages):
            return
        if self.messages[message_index].get("role") != "assistant":
            return
        self.messages[:] = self.messages[:message_index]
        self.store.save(self.conversation)
        self._rebuild_messages()
        self._start_generation()

    def _start_generation(self) -> None:
        self.streamed_answer = ""
        self.pending_bubble = None
        self.pending_row = None
        self._finalized_current_response = False
        self._generation_cancel_requested = False
        self._auto_scroll_enabled = True
        self._show_thinking()
        self._set_controls_generating(True)
        self._set_status("Connecting")

        self.thread = QThread(self)
        self.worker = OllamaWorker(self.messages.copy(), self.active_model)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.connected.connect(lambda: self._set_status("Thinking"))
        self.worker.chunk_received.connect(self._receive_chunk)
        self.worker.finished.connect(self._receive_response)
        self.worker.cancelled.connect(self._receive_cancelled)
        self.worker.failed.connect(self._receive_error)
        self.worker.stopped.connect(self.worker.deleteLater)
        self.worker.stopped.connect(self.thread.quit)
        self.thread.finished.connect(self._cleanup_worker)
        self.thread.start()

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
        if self.pending_bubble is None:
            self._remove_thinking()
            self.pending_bubble = self._add_message("", "assistant")
            self.pending_row = self.pending_bubble.parentWidget().parentWidget()
        self.streamed_answer += chunk
        self._set_status("Generating")
        if not self._render_timer.isActive():
            self._render_timer.start()
        if self._auto_scroll_enabled:
            self._scroll_to_bottom(smooth=True)

    def _render_stream(self, force: bool = False) -> None:
        if self.pending_bubble is not None:
            self.pending_bubble.set_text(self.streamed_answer)
            if self._auto_scroll_enabled or force:
                self._scroll_to_bottom(smooth=True)

    def _receive_response(self, answer: str) -> None:
        if self._generation_cancel_requested:
            return
        self._render_timer.stop()
        self._remove_thinking()
        if self.pending_bubble is None:
            self.pending_bubble = self._add_message(answer, "assistant")
            self.pending_row = self.pending_bubble.parentWidget().parentWidget()
        else:
            self.pending_bubble.set_text(answer)
        self._append_assistant_message(answer)
        self._set_status("Ready")

    def _receive_cancelled(self) -> None:
        self._set_status("Stopped")
        self._finalize_partial_response(stopped=True)

    def _receive_error(self, error: str) -> None:
        if self._generation_cancel_requested:
            return
        self._render_timer.stop()
        self._remove_thinking()
        if self.pending_bubble is not None and self.streamed_answer.strip():
            self.pending_bubble.set_text(self.streamed_answer)
            self._finalize_partial_response()
        self._add_message(f"**Unable to respond**\n\n{error}", "error")
        self.store.save(self.conversation)
        self._set_status("Error")
        self._scroll_to_bottom()

    def _append_assistant_message(self, answer: str) -> None:
        if self._finalized_current_response:
            return
        self.messages.append({"role": "assistant", "content": answer})
        self.conversation.model = self.active_model
        self.store.save(self.conversation)
        self._finalized_current_response = True
        self._rebuild_sidebar()
        if self.pending_row is not None and self.pending_bubble is not None:
            row_index = self.message_layout.indexOf(self.pending_row)
            if row_index >= 0:
                self._rebuild_messages()

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

    def _cleanup_worker(self) -> None:
        if self.thread is not None:
            self.thread.deleteLater()
        self.worker = None
        self.thread = None
        self.pending_bubble = None
        self.pending_row = None
        self._set_controls_generating(False)
        if self.status.text() in {"Connecting", "Thinking", "Generating"}:
            self._set_status("Ready")
        self.input_box.setFocus()

    def _set_controls_generating(self, generating: bool) -> None:
        self.send_button.setEnabled(not generating)
        self.sidebar.set_generating(generating)
        self.input_box.setEnabled(not generating)
        self.model_selector.setEnabled(not generating)
        self.stop_button.setVisible(generating)
        self.send_button.setVisible(not generating)

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
        self.model_selector.clear()
        self.model_selector.addItems(values or [current])
        self.model_selector.setCurrentText(current)
        self.model_selector.blockSignals(False)

    def _model_changed(self, model_name: str) -> None:
        if not model_name or self.thread is not None:
            return
        self.active_model = model_name
        self.preferences.selected_model = model_name
        self.preferences.save()
        self.conversation.model = model_name
        self.store.save(self.conversation)

    def _set_status(self, text: str) -> None:
        self.status.setText(text)
        self.status.setProperty("state", text.lower())
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.worker is not None:
            self.worker.cancel()
        if self.thread is not None:
            self.thread.quit()
            if not self.thread.wait(2500):
                event.ignore()
                self.thread.finished.connect(self.close)
                return
        if self.model_thread is not None:
            self.model_thread.quit()
            self.model_thread.wait(1000)
        event.accept()

    def _apply_style(self) -> None:
        self.setStyleSheet("""
            * { font-family: "Segoe UI Variable", "Segoe UI"; }
            QMainWindow, #centralWidget, #mainPanel, #messageContainer {
                background: #212121; color: #ececec;
            }
            #sidebar {
                background: #171717; border-right: 1px solid #2f2f2f;
            }
            #sidebarNewChat {
                background: #242424; color: #f1f1f1; border: 1px solid #3b3b3b;
                border-radius: 8px; padding: 8px 10px; text-align: left;
            }
            #sidebarNewChat:hover { background: #2d2d2d; }
            #chatSearch {
                background: #222; color: #ececec; border: 1px solid #3b3b3b;
                border-radius: 8px; padding: 7px 9px;
            }
            #sidebarScroll { background: transparent; border: none; }
            #conversationGroup {
                color: #8c8c8c; font-size: 11px; padding: 12px 6px 4px 6px;
            }
            #conversationRow {
                background: transparent; border-radius: 7px;
            }
            #conversationRow:hover { background: #252525; }
            #conversationRow[active="true"] { background: #303030; }
            #conversationTitle { color: #e4e4e4; font-size: 13px; }
            #sidebarIconButton, #headerIconButton {
                background: transparent; border: none; color: #c7c7c7;
                border-radius: 6px; padding: 4px;
            }
            #sidebarIconButton:hover, #headerIconButton:hover { background: #333; }
            #header { background: #212121; border-bottom: 1px solid #2f2f2f; }
            #title { color: #f5f5f5; font-size: 16px; font-weight: 600; }
            #statusBadge {
                color: #b4b4b4; background: #2f2f2f; border-radius: 10px;
                padding: 3px 9px; font-size: 11px;
            }
            #statusBadge[state="ready"] { color: #cfcfcf; background: #2f2f2f; }
            #statusBadge[state="connecting"], #statusBadge[state="thinking"] {
                color: #f5d38b; background: #47391f;
            }
            #statusBadge[state="generating"] { color: #b9f5d3; background: #1f4735; }
            #statusBadge[state="stopped"] { color: #d2d2d2; background: #3a3a3a; }
            #statusBadge[state="error"] { color: #ffb4ab; background: #4a2424; }
            #chatScroll { background: #212121; border: none; }
            QScrollBar:vertical { background: transparent; width: 9px; margin: 3px; }
            QScrollBar::handle:vertical { background: #4c4c4c; border-radius: 4px; min-height: 36px; }
            QScrollBar::handle:vertical:hover { background: #666; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
            #welcomeIcon { color: #f2f2f2; font-size: 28px; font-weight: 650; }
            #welcomeTitle { color: #f2f2f2; font-size: 25px; font-weight: 600; }
            #welcomeSubtitle { color: #8d8d8d; font-size: 13px; }
            #userBubble {
                background: #303030; border: 1px solid #3a3a3a; border-radius: 18px;
            }
            #assistantBubble, #errorBubble { background: transparent; border: none; }
            #errorBubble { color: #ffb4ab; }
            #markdownView {
                background: transparent; color: #ececec; border: none;
                font-size: 15px; selection-background-color: #5b78a6;
            }
            #thinkingDots { color: #aaa; font-size: 24px; letter-spacing: 2px; }
            #codeBlock { background: #171717; border: 1px solid #353535; border-radius: 8px; }
            #codeHeader { background: #2a2a2a; border-top-left-radius: 8px; border-top-right-radius: 8px; }
            #codeLanguage { color: #aaa; font-size: 11px; }
            #copiedLabel { color: #aaa; font-size: 11px; padding-right: 4px; }
            #copyButton, #actionButton {
                background: transparent; border: none; color: #c7c7c7;
                padding: 3px; min-width: 24px; min-height: 24px;
            }
            #copyButton:hover, #actionButton:hover {
                color: white; background: #3c3c3c; border-radius: 4px;
            }
            #codeEditor {
                background: #171717; color: #e6e6e6; border: none;
                padding: 0; selection-background-color: #425775;
            }
            #inputPanel { background: #212121; }
            #composer {
                background: #111214; border: 1px solid #3f4145; border-radius: 22px;
            }
            #messageInput {
                background: transparent; color: #f1f1f1; border: none;
                padding: 4px 3px; font-size: 15px; selection-background-color: #58719a;
            }
            #messageInput:disabled { color: #8b8b8b; }
            #modelSelector {
                background: #2b2c30; color: #e3e3e3; border: 1px solid #44464c;
                border-radius: 16px; padding: 4px 9px; min-height: 28px;
            }
            #modelSelector:disabled { color: #838383; background: #252525; border-color: #343434; }
            #sendButton {
                background: #8b5cf6; color: white; border: none;
                border-radius: 17px; min-width: 34px; min-height: 34px;
            }
            #sendButton:hover { background: #9b6dff; }
            #sendButton:pressed { background: #7947e8; }
            #sendButton:disabled { background: #555; color: #8c8c8c; }
            #helperText { color: #777; font-size: 10px; }
        """)


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
