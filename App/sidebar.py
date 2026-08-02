"""Collapsible conversation sidebar and floating search overlay."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QPropertyAnimation, Qt, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QInputDialog,
    QLineEdit,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from config import APP_NAME
from conversation_store import Conversation
from design import Motion, icon
from widgets import apply_interaction_cursors


class ConversationRow(QFrame):
    selected = Signal(str)
    pin_requested = Signal(str, bool)
    rename_requested = Signal(str, str)
    delete_requested = Signal(str)

    def __init__(self, conversation: Conversation, active: bool) -> None:
        super().__init__()
        self.conversation = conversation
        self.setObjectName("conversationRow")
        self.setProperty("active", active)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(34)
        self._full_title = conversation.title

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 10, 0)
        layout.setSpacing(0)

        self.title = QLabel(conversation.title)
        self.title.setObjectName("conversationTitle")
        self.title.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)

        layout.addWidget(self.title, 1)
        self._update_elided_title()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.selected.emit(self.conversation.id)
        super().mousePressEvent(event)

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        pin_action = menu.addAction("Unpin" if self.conversation.pinned else "Pin")
        rename_action = menu.addAction("Rename")
        delete_action = menu.addAction("Delete")
        selected = menu.exec(QPoint(event.globalX(), event.globalY()))
        if selected == pin_action:
            self.pin_requested.emit(self.conversation.id, not self.conversation.pinned)
        elif selected == rename_action:
            self._rename()
        elif selected == delete_action:
            self.delete_requested.emit(self.conversation.id)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_elided_title()

    def _update_elided_title(self) -> None:
        metrics = QFontMetrics(self.title.font())
        self.title.setText(metrics.elidedText(self._full_title, Qt.TextElideMode.ElideRight, max(24, self.width() - 20)))

    def _rename(self) -> None:
        title, accepted = QInputDialog.getText(
            self,
            "Rename conversation",
            "Title",
            text=self.conversation.title,
        )
        if accepted:
            self.rename_requested.emit(self.conversation.id, title)


class SearchOverlay(QFrame):
    selected = Signal(str)
    closed = Signal()
    search_changed = Signal(str)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("searchOverlay")
        self.setWindowFlags(Qt.WindowType.Widget)
        self.hide()

        self.opacity = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.opacity)
        self.fade = QPropertyAnimation(self.opacity, b"opacity", self)
        self.fade.setDuration(Motion.NORMAL)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 16, 22, 18)
        layout.setSpacing(10)

        top = QHBoxLayout()
        self.input = QLineEdit(objectName="overlaySearchInput")
        self.input.setPlaceholderText("Search...")
        self.input.textChanged.connect(self.search_changed.emit)
        close_button = QToolButton(objectName="sidebarIconButton")
        close_button.setIcon(icon("close"))
        close_button.setToolTip("Close search")
        close_button.clicked.connect(self.close_overlay)
        top.addWidget(self.input, 1)
        top.addWidget(close_button)
        layout.addLayout(top)

        self.hint = QLabel("Recent chats", objectName="overlayHint")
        layout.addWidget(self.hint)

        self.scroll_area = QScrollArea(objectName="sidebarScroll")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results = QWidget()
        self.results.setObjectName("searchResults")
        self.results_layout = QVBoxLayout(self.results)
        self.results_layout.setContentsMargins(0, 0, 0, 0)
        self.results_layout.setSpacing(4)
        self.results_layout.addStretch(1)
        self.scroll_area.setWidget(self.results)
        layout.addWidget(self.scroll_area, 1)

    def show_overlay(self) -> None:
        parent_rect = self.parentWidget().rect()
        width = min(720, max(420, int(parent_rect.width() * 0.48)))
        height = min(520, max(360, int(parent_rect.height() * 0.55)))
        self.setGeometry(
            (parent_rect.width() - width) // 2,
            max(42, (parent_rect.height() - height) // 3),
            width,
            height,
        )
        self.input.clear()
        self.opacity.setOpacity(0)
        self.show()
        self.raise_()
        self.input.setFocus()
        self.fade.stop()
        self.fade.setStartValue(0)
        self.fade.setEndValue(1)
        self.fade.start()

    def close_overlay(self) -> None:
        self.hide()
        self.closed.emit()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close_overlay()
            return
        super().keyPressEvent(event)

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.WindowDeactivate:
            self.close_overlay()
        return super().event(event)

    def rebuild(self, conversations: list[Conversation]) -> None:
        while self.results_layout.count() > 1:
            item = self.results_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for conversation in conversations:
            button = QPushButton(conversation.title)
            button.setObjectName("sidebarNewChat")
            button.setIcon(icon("expand"))
            button.clicked.connect(lambda _checked=False, cid=conversation.id: self.selected.emit(cid))
            self.results_layout.insertWidget(self.results_layout.count() - 1, button)
        apply_interaction_cursors(self)


class ChatSidebar(QFrame):
    new_chat_requested = Signal()
    search_requested = Signal()
    conversation_selected = Signal(str)
    pin_requested = Signal(str, bool)
    rename_requested = Signal(str, str)
    delete_requested = Signal(str)

    EXPANDED_WIDTH = 260
    COLLAPSED_WIDTH = 48

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("sidebar")
        self.setMinimumWidth(self.EXPANDED_WIDTH)
        self.setMaximumWidth(self.EXPANDED_WIDTH)
        self._expanded = True

        self.root_layout = QVBoxLayout(self)
        self.root_layout.setContentsMargins(8, 12, 8, 12)
        self.root_layout.setSpacing(8)

        self.expanded_container = QWidget()
        expanded_layout = QVBoxLayout(self.expanded_container)
        expanded_layout.setContentsMargins(0, 0, 0, 0)
        expanded_layout.setSpacing(8)
        self.root_layout.addWidget(self.expanded_container, 1)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(4)
        self.brand_label = QLabel(APP_NAME, objectName="sidebarTopTitle")
        self.search_button = QToolButton(objectName="sidebarIconButton")
        self.search_button.setIcon(icon("search"))
        self.search_button.setToolTip("Search chats")
        self.search_button.clicked.connect(self.search_requested.emit)
        self.collapse_button = QToolButton(objectName="sidebarIconButton")
        self.collapse_button.setIcon(icon("collapse"))
        self.collapse_button.setToolTip("Collapse sidebar")
        self.collapse_button.clicked.connect(self.toggle)
        top.addWidget(self.brand_label, 1)
        top.addWidget(self.search_button)
        top.addWidget(self.collapse_button)
        expanded_layout.addLayout(top)

        self.new_chat_button = QPushButton("New chat")
        self.new_chat_button.setObjectName("sidebarNewChat")
        self.new_chat_button.setIcon(icon("new"))
        self.new_chat_button.clicked.connect(self.new_chat_requested.emit)
        expanded_layout.addWidget(self.new_chat_button)

        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("sidebarScroll")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_widget = QWidget()
        self.list_widget.setObjectName("sidebarList")
        self.list_layout = QVBoxLayout(self.list_widget)
        self.list_layout.setContentsMargins(0, 4, 0, 0)
        self.list_layout.setSpacing(3)
        self.list_layout.addStretch(1)
        self.scroll_area.setWidget(self.list_widget)
        expanded_layout.addWidget(self.scroll_area, 1)

        self.collapsed_container = QWidget()
        collapsed_layout = QVBoxLayout(self.collapsed_container)
        collapsed_layout.setContentsMargins(0, 0, 0, 0)
        collapsed_layout.setSpacing(10)
        self.collapsed_expand_button = QToolButton(objectName="collapsedSidebarButton")
        self.collapsed_expand_button.setIcon(icon("expand"))
        self.collapsed_expand_button.setToolTip("Open sidebar")
        self.collapsed_expand_button.clicked.connect(self.toggle)
        self.collapsed_search_button = QToolButton(objectName="collapsedSidebarButton")
        self.collapsed_search_button.setIcon(icon("search"))
        self.collapsed_search_button.setToolTip("Search chats")
        self.collapsed_search_button.clicked.connect(self.search_requested.emit)
        self.collapsed_new_chat_button = QToolButton(objectName="collapsedSidebarButton")
        self.collapsed_new_chat_button.setIcon(icon("new"))
        self.collapsed_new_chat_button.setToolTip("New chat")
        self.collapsed_new_chat_button.clicked.connect(self.new_chat_requested.emit)
        collapsed_layout.addWidget(self.collapsed_expand_button, 0, Qt.AlignmentFlag.AlignHCenter)
        collapsed_layout.addWidget(self.collapsed_search_button, 0, Qt.AlignmentFlag.AlignHCenter)
        collapsed_layout.addWidget(self.collapsed_new_chat_button, 0, Qt.AlignmentFlag.AlignHCenter)
        collapsed_layout.addStretch(1)
        self.root_layout.addWidget(self.collapsed_container, 1)
        self.collapsed_container.hide()
        apply_interaction_cursors(self)

    def toggle(self) -> None:
        self.set_expanded(not self._expanded)

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = expanded
        self.expanded_container.setVisible(expanded)
        self.collapsed_container.setVisible(not expanded)
        if expanded:
            self.root_layout.setContentsMargins(8, 12, 8, 12)
        else:
            self.root_layout.setContentsMargins(0, 14, 0, 12)
        self.collapse_button.setIcon(icon("collapse" if expanded else "expand"))
        self.collapse_button.setToolTip("Collapse sidebar" if expanded else "Expand sidebar")
        width = self.EXPANDED_WIDTH if expanded else self.COLLAPSED_WIDTH
        self.setMinimumWidth(width)
        self.setMaximumWidth(width)
        self._sync_list_width()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._sync_list_width()

    def _sync_list_width(self) -> None:
        if not hasattr(self, "scroll_area"):
            return
        margins = self.root_layout.contentsMargins()
        content_width = max(1, self.width() - margins.left() - margins.right())
        self.list_widget.setMinimumWidth(content_width)
        self.list_widget.setMaximumWidth(content_width)

    def set_generating(self, generating: bool) -> None:
        self.new_chat_button.setEnabled(not generating)
        self.search_button.setEnabled(not generating)
        self.collapsed_new_chat_button.setEnabled(not generating)
        self.collapsed_search_button.setEnabled(not generating)

    def rebuild(
        self,
        groups: dict[str, list[Conversation]],
        active_conversation_id: str,
    ) -> None:
        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for group_name, conversations in groups.items():
            if not conversations:
                continue
            label = QLabel(group_name)
            label.setObjectName("conversationGroup")
            self.list_layout.insertWidget(self.list_layout.count() - 1, label)
            for conversation in conversations:
                row = ConversationRow(
                    conversation,
                    active=conversation.id == active_conversation_id,
                )
                row.selected.connect(self.conversation_selected.emit)
                row.pin_requested.connect(self.pin_requested.emit)
                row.rename_requested.connect(self.rename_requested.emit)
                row.delete_requested.connect(self.delete_requested.emit)
                self.list_layout.insertWidget(self.list_layout.count() - 1, row)
        self._sync_list_width()
        apply_interaction_cursors(self)
