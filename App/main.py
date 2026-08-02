"""Native desktop chat interface for the local AI Agent."""

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
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from config import APP_NAME, MODEL_NAME, SYSTEM_PROMPT
from conversation_store import Conversation, ConversationStore
from ollama_client import OllamaWorker
from widgets import AutoGrowingInput, MessageActions, MessageBubble, ThinkingBubble


class MainWindow(QMainWindow):
    CONTENT_MAX_WIDTH = 850
    NEAR_BOTTOM_PX = 90

    def __init__(self) -> None:
        super().__init__()
        self.store = ConversationStore()
        self.conversation = self._load_or_create_conversation()
        self.messages = self.conversation.messages

        self.thread: QThread | None = None
        self.worker: OllamaWorker | None = None
        self.pending_bubble: MessageBubble | None = None
        self.pending_row: QWidget | None = None
        self.thinking_row: QWidget | None = None
        self.streamed_answer = ""
        self._finalized_current_response = False
        self._generation_cancel_requested = False
        self._scroll_animation: QPropertyAnimation | None = None

        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(35)
        self._render_timer.timeout.connect(self._render_stream)

        self.setWindowTitle(APP_NAME)
        self.resize(1080, 780)
        self.setMinimumSize(680, 520)
        self._build_interface()
        self._apply_style()
        self._rebuild_messages()
        self.input_box.setFocus()

    def _load_or_create_conversation(self) -> Conversation:
        conversation = self.store.load_latest()
        if conversation is None:
            conversation = self.store.create(SYSTEM_PROMPT)
            self.store.save(conversation)
        if not conversation.messages or conversation.messages[0].get("role") != "system":
            conversation.messages.insert(0, {"role": "system", "content": SYSTEM_PROMPT})
        return conversation

    def _build_interface(self) -> None:
        central = QWidget(objectName="centralWidget")
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QFrame(objectName="header")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(22, 13, 22, 13)
        header_layout.setSpacing(10)
        title = QLabel(APP_NAME, objectName="title")
        model = QLabel(f"{MODEL_NAME} - Local", objectName="modelLabel")
        self.new_chat_button = QPushButton("New chat", objectName="headerButton")
        self.new_chat_button.setToolTip("Start a new conversation")
        self.new_chat_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.new_chat_button.clicked.connect(self.new_chat)
        self.status = QLabel("Ready", objectName="statusBadge")
        header_layout.addWidget(title)
        header_layout.addWidget(model)
        header_layout.addStretch()
        header_layout.addWidget(self.new_chat_button)
        header_layout.addWidget(self.status)
        root.addWidget(header)

        self.message_container = QWidget(objectName="messageContainer")
        self.message_layout = QVBoxLayout(self.message_container)
        self.message_layout.setContentsMargins(24, 28, 24, 24)
        self.message_layout.setSpacing(22)
        self.message_layout.addStretch(1)

        self.scroll_area = QScrollArea(objectName="chatScroll")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setWidget(self.message_container)
        self.scroll_area.viewport().installEventFilter(self)
        root.addWidget(self.scroll_area, 1)

        input_panel = QFrame(objectName="inputPanel")
        input_outer = QHBoxLayout(input_panel)
        input_outer.setContentsMargins(24, 10, 24, 16)
        input_outer.addStretch()

        self.composer = QFrame(objectName="composer")
        self.composer.setMaximumWidth(self.CONTENT_MAX_WIDTH)
        composer_layout = QVBoxLayout(self.composer)
        composer_layout.setContentsMargins(14, 8, 8, 7)
        composer_layout.setSpacing(2)

        input_row = QHBoxLayout()
        input_row.setSpacing(8)
        input_row.setAlignment(Qt.AlignmentFlag.AlignBottom)
        self.input_box = AutoGrowingInput()
        self.input_box.send_requested.connect(self.send_message)
        self.stop_button = QPushButton("Stop", objectName="stopButton")
        self.stop_button.setToolTip("Stop generating")
        self.stop_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.stop_button.clicked.connect(self.stop_generation)
        self.stop_button.hide()
        self.send_button = QPushButton("Send", objectName="sendButton")
        self.send_button.setToolTip("Send message")
        self.send_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_button.clicked.connect(self.send_message)
        input_row.addWidget(self.input_box, 1)
        input_row.addWidget(self.stop_button, 0, Qt.AlignmentFlag.AlignBottom)
        input_row.addWidget(self.send_button, 0, Qt.AlignmentFlag.AlignBottom)
        composer_layout.addLayout(input_row)
        input_outer.addWidget(self.composer, 1)
        input_outer.addStretch()

        panel_layout = QVBoxLayout()
        panel_layout.setSpacing(6)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.addWidget(input_panel)
        hint = QLabel(
            "AI Agent can make mistakes. Check important information.",
            objectName="helperText",
        )
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        panel_layout.addWidget(hint)
        root.addLayout(panel_layout)
        root.addSpacing(10)
        self.setCentralWidget(central)

    def _make_welcome(self) -> QWidget:
        welcome = QWidget(objectName="welcome")
        layout = QVBoxLayout(welcome)
        layout.setContentsMargins(0, 50, 0, 25)
        layout.setSpacing(7)
        icon = QLabel("*", objectName="welcomeIcon")
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
        row.setProperty("messageIndex", message_index if message_index is not None else -1)
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
        self._resize_rows()
        return bubble

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

    def _is_near_bottom(self) -> bool:
        bar = self.scroll_area.verticalScrollBar()
        return bar.maximum() - bar.value() <= self.NEAR_BOTTOM_PX

    def _scroll_to_bottom(self, smooth: bool = False) -> None:
        QTimer.singleShot(0, lambda: self._perform_scroll(smooth))

    def _perform_scroll(self, smooth: bool) -> None:
        bar = self.scroll_area.verticalScrollBar()
        target = bar.maximum()
        if not smooth or abs(target - bar.value()) < 30:
            bar.setValue(target)
            return
        if self._scroll_animation is not None:
            self._scroll_animation.stop()
        self._scroll_animation = QPropertyAnimation(bar, b"value", self)
        self._scroll_animation.setDuration(170)
        self._scroll_animation.setStartValue(bar.value())
        self._scroll_animation.setEndValue(target)
        self._scroll_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._scroll_animation.start()

    def new_chat(self) -> None:
        if self.thread is not None:
            self.stop_generation()
            return
        self.conversation = self.store.create(SYSTEM_PROMPT)
        self.store.save(self.conversation)
        self.messages = self.conversation.messages
        self.input_box.clear()
        self.streamed_answer = ""
        self._set_status("Ready")
        self._rebuild_messages()

    def send_message(self) -> None:
        message = self.input_box.toPlainText().strip()
        if not message or self.thread is not None:
            return
        self._add_message(message, "user", len(self.messages))
        self.messages.append({"role": "user", "content": message})
        self.store.save(self.conversation)
        self.input_box.clear()
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
        self._show_thinking()
        self._set_controls_generating(True)
        self._set_status("Connecting")

        self.thread = QThread(self)
        self.worker = OllamaWorker(self.messages.copy())
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
        self._finalize_partial_response()

    def _receive_chunk(self, chunk: str) -> None:
        if self._generation_cancel_requested:
            return
        should_scroll = self._is_near_bottom()
        if self.pending_bubble is None:
            self._remove_thinking()
            self.pending_bubble = self._add_message("", "assistant")
            self.pending_row = self.pending_bubble.parentWidget().parentWidget()
        self.streamed_answer += chunk
        self._set_status("Generating")
        if not self._render_timer.isActive():
            self._render_timer.start()
        if should_scroll:
            self._scroll_to_bottom(smooth=True)

    def _render_stream(self, force: bool = False) -> None:
        if self.pending_bubble is not None:
            should_scroll = self._is_near_bottom()
            self.pending_bubble.set_text(self.streamed_answer)
            if should_scroll or force:
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
        self._finalize_partial_response()

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
        message_index = len(self.messages)
        self.messages.append({"role": "assistant", "content": answer})
        self.store.save(self.conversation)
        self._finalized_current_response = True
        if self.pending_row is not None and self.pending_bubble is not None:
            row_index = self.message_layout.indexOf(self.pending_row)
            if row_index >= 0:
                self._rebuild_messages()

    def _finalize_partial_response(self) -> None:
        answer = self.streamed_answer.strip()
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
        self.new_chat_button.setEnabled(not generating)
        self.input_box.setEnabled(not generating)
        self.stop_button.setVisible(generating)
        self.send_button.setVisible(not generating)

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
        event.accept()

    def _apply_style(self) -> None:
        self.setStyleSheet("""
            * { font-family: "Segoe UI Variable", "Segoe UI"; }
            QMainWindow, #centralWidget, #messageContainer {
                background: #212121; color: #ececec;
            }
            #header { background: #212121; border-bottom: 1px solid #2f2f2f; }
            #title { color: #f5f5f5; font-size: 16px; font-weight: 600; }
            #modelLabel { color: #8f8f8f; font-size: 12px; padding-left: 5px; }
            #headerButton {
                background: transparent; color: #d6d6d6; border: 1px solid #444;
                border-radius: 8px; padding: 5px 10px; font-size: 12px;
            }
            #headerButton:hover { background: #2f2f2f; color: white; }
            #headerButton:disabled { color: #777; border-color: #333; }
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
            #welcomeIcon { color: #d8d8d8; font-size: 30px; }
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
            #thinkingLabel, #thinkingDots { color: #aaa; font-size: 14px; font-style: italic; }
            #codeBlock { background: #171717; border: 1px solid #353535; border-radius: 8px; }
            #codeHeader { background: #2a2a2a; border-top-left-radius: 8px; border-top-right-radius: 8px; }
            #codeLanguage { color: #aaa; font-size: 11px; }
            #copyButton, #actionButton {
                background: transparent; border: none; color: #c7c7c7;
                padding: 3px 7px; font-size: 11px;
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
                background: #303030; border: 1px solid #474747; border-radius: 22px;
            }
            #messageInput {
                background: transparent; color: #f1f1f1; border: none;
                padding: 7px 3px; font-size: 15px; selection-background-color: #58719a;
            }
            #messageInput:disabled { color: #8b8b8b; }
            #sendButton, #stopButton {
                border: none; border-radius: 18px; padding: 0 14px;
                min-height: 36px; font-size: 13px; font-weight: 600;
            }
            #sendButton { background: #f1f1f1; color: #181818; }
            #sendButton:hover { background: white; }
            #sendButton:pressed { background: #d5d5d5; }
            #sendButton:disabled { background: #555; color: #8c8c8c; }
            #stopButton { background: #3c3c3c; color: #f1f1f1; }
            #stopButton:hover { background: #4a4a4a; }
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
