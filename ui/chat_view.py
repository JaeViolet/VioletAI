"""Chat message view: rendering, scrolling, and streaming display.

ChatView owns the message container, scroll area, message rows, thinking
indicator, and the streaming response bubble. It renders text passed in
by the window and emits signals (e.g. regenerate_requested) instead of
owning conversation, engine, or model state.
"""

from __future__ import annotations

from time import perf_counter

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QPoint,
    QPropertyAnimation,
    QTimer,
    Qt,
    Signal,
)
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.config import APP_NAME
from ui.design import Motion
from ui.widgets import (
    MessageActions,
    MessageBubble,
    ThinkingBubble,
    apply_interaction_cursors,
)


class ChatView(QWidget):
    NEAR_BOTTOM_PX = 90
    CONTENT_MAX_WIDTH = 760

    regenerate_requested = Signal(int)
    viewport_resized = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
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

        self.pending_bubble: MessageBubble | None = None
        self.pending_row: QWidget | None = None
        self.pending_column: QWidget | None = None
        self.thinking_row: QWidget | None = None
        self.streamed_answer = ""
        self.first_token_at: float | None = None
        self._finalized_current_response = False
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
        self._render_timer.timeout.connect(self.render_stream)

        self._bulk_rebuilding_messages = False
        self._message_rebuild_active = False
        self._rebuild_finish_active = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.scroll_area, 1)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.scroll_area.viewport() and event.type() == QEvent.Type.Resize:
            if not self._message_rebuild_active:
                self.viewport_resized.emit()
        if watched is self.scroll_area.viewport() and event.type() == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.MiddleButton:
                self._toggle_middle_scroll(event.position().toPoint())
                return True
            if self._middle_scroll_timer.isActive():
                self._stop_middle_scroll()
        if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            self._stop_middle_scroll()
        return super().eventFilter(watched, event)

    def _available_width(self) -> int:
        return max(280, min(self.CONTENT_MAX_WIDTH, self.scroll_area.viewport().width() - 96))

    def resize_rows(self, available: int | None = None) -> None:
        available = available if available is not None else self._available_width()
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

    def rebuild_messages(self, messages: list[dict[str, str]]) -> None:
        self._message_rebuild_active = True
        self.setUpdatesEnabled(False)
        try:
            self._clear_message_rows()
            visible_messages = [
                (index, message)
                for index, message in enumerate(messages)
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
                        self.add_message(message.get("content", ""), message.get("role", "assistant"), index)
                finally:
                    self._bulk_rebuilding_messages = False
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

    def add_message(self, text: str, role: str, message_index: int | None = None) -> MessageBubble:
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
            self.resize_rows()
        return bubble

    def _animate_appearance(self, row: QWidget) -> None:
        row.setGraphicsEffect(None)

    def _attach_actions(self, layout: QVBoxLayout, bubble: MessageBubble, message_index: int) -> None:
        actions = MessageActions()
        actions.copy_requested.connect(lambda: QApplication.clipboard().setText(bubble.text()))
        actions.regenerate_requested.connect(lambda: self.regenerate_requested.emit(message_index))
        layout.addWidget(actions)
        apply_interaction_cursors(actions)

    def show_thinking(self) -> None:
        self.remove_thinking()
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
        self.resize_rows()
        self.scroll_to_bottom()

    def remove_thinking(self) -> None:
        if self.thinking_row is not None:
            self.message_layout.removeWidget(self.thinking_row)
            self.thinking_row.deleteLater()
            self.thinking_row = None

    def _handle_scroll_change(self, _value: int) -> None:
        if self._programmatic_scroll:
            return
        self._auto_scroll_enabled = self.is_near_bottom()

    def is_near_bottom(self) -> bool:
        bar = self.scroll_area.verticalScrollBar()
        return bar.maximum() - bar.value() <= self.NEAR_BOTTOM_PX

    def auto_scroll_enabled(self) -> bool:
        return self._auto_scroll_enabled

    def scroll_to_bottom(self, smooth: bool = False) -> None:
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

    def stop_middle_scroll(self) -> None:
        self._stop_middle_scroll()

    def _stop_middle_scroll(self) -> None:
        self._middle_scroll_timer.stop()
        self._middle_scroll_origin = None
        self.scroll_area.viewport().unsetCursor()

    def reset_stream(self) -> None:
        self.streamed_answer = ""
        self.pending_bubble = None
        self.pending_row = None
        self.pending_column = None
        self.first_token_at = None
        self._finalized_current_response = False
        self._auto_scroll_enabled = True

    def on_chunk(self, chunk: str) -> None:
        if self.first_token_at is None:
            self.first_token_at = perf_counter()
        if self.pending_bubble is None:
            self.remove_thinking()
            self.pending_bubble = self.add_message("", "assistant")
            self.pending_row = self.pending_bubble.parentWidget().parentWidget()
            self.pending_column = self.pending_bubble.parentWidget()
        self.streamed_answer += chunk
        if not self._render_timer.isActive():
            self._render_timer.start()

    def render_stream(self, force: bool = False) -> None:
        if self.pending_bubble is not None:
            self.pending_bubble.set_text(self.streamed_answer)
            self.finalize_geometry()
            if self.auto_scroll_enabled() or force:
                self.scroll_to_bottom(smooth=True)

    def stop_render(self) -> None:
        self._render_timer.stop()

    def finalize_response(self, answer: str) -> None:
        self._render_timer.stop()
        self.remove_thinking()
        if self.pending_bubble is None:
            self.pending_bubble = self.add_message(answer, "assistant")
            self.pending_row = self.pending_bubble.parentWidget().parentWidget()
            self.pending_column = self.pending_bubble.parentWidget()
        else:
            self.pending_bubble.set_text(answer)
        self.finalize_geometry()

    def set_pending_text(self, text: str) -> None:
        if self.pending_bubble is not None:
            self.pending_bubble.set_text(text)

    def has_pending_stream(self) -> bool:
        return self.pending_bubble is not None and bool(self.streamed_answer.strip())

    def attach_pending_actions(self, message_index: int) -> None:
        if self.pending_row is not None and self.pending_bubble is not None:
            column = self.pending_bubble.parentWidget()
            if isinstance(column, QWidget) and column.layout() is not None:
                self._attach_actions(column.layout(), self.pending_bubble, message_index)

    def finalize_geometry(self) -> None:
        if self.pending_bubble is not None:
            self.pending_bubble.layout.invalidate()
            self.pending_bubble.adjustSize()
        self.message_container.layout().activate()
        self.message_container.adjustSize()
        self.resize_rows()

    def mark_finalized(self) -> None:
        self._finalized_current_response = True

    def is_finalized(self) -> bool:
        return self._finalized_current_response

    def clear_pending(self) -> None:
        self.pending_bubble = None
        self.pending_row = None
        self.pending_column = None
