"""Collapsible conversation sidebar."""

from __future__ import annotations

from PySide6.QtCore import QPropertyAnimation, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLineEdit,
    QLabel,
    QPushButton,
    QScrollArea,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from conversation_store import Conversation


class ConversationRow(QFrame):
    selected = Signal(str)
    rename_requested = Signal(str, str)
    delete_requested = Signal(str)

    def __init__(self, conversation: Conversation, active: bool) -> None:
        super().__init__()
        self.conversation = conversation
        self.setObjectName("conversationRow")
        self.setProperty("active", active)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 6, 6)
        layout.setSpacing(4)

        self.title = QLabel(conversation.title)
        self.title.setObjectName("conversationTitle")
        self.title.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)

        rename_button = QToolButton()
        rename_button.setObjectName("sidebarIconButton")
        rename_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView))
        rename_button.setToolTip("Rename conversation")
        rename_button.clicked.connect(self._rename)

        delete_button = QToolButton()
        delete_button.setObjectName("sidebarIconButton")
        delete_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogDiscardButton))
        delete_button.setToolTip("Delete conversation")
        delete_button.clicked.connect(lambda: self.delete_requested.emit(conversation.id))

        layout.addWidget(self.title, 1)
        layout.addWidget(rename_button)
        layout.addWidget(delete_button)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.selected.emit(self.conversation.id)
        super().mousePressEvent(event)

    def _rename(self) -> None:
        title, accepted = QInputDialog.getText(
            self,
            "Rename conversation",
            "Title",
            text=self.conversation.title,
        )
        if accepted:
            self.rename_requested.emit(self.conversation.id, title)


class ChatSidebar(QFrame):
    new_chat_requested = Signal()
    conversation_selected = Signal(str)
    rename_requested = Signal(str, str)
    delete_requested = Signal(str)
    search_changed = Signal(str)

    EXPANDED_WIDTH = 280
    COLLAPSED_WIDTH = 0

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("sidebar")
        self.setMinimumWidth(self.EXPANDED_WIDTH)
        self.setMaximumWidth(self.EXPANDED_WIDTH)
        self._expanded = True

        self._animation = QPropertyAnimation(self, b"maximumWidth", self)
        self._animation.setDuration(160)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 12, 10, 12)
        layout.setSpacing(8)

        self.new_chat_button = QPushButton("New chat")
        self.new_chat_button.setObjectName("sidebarNewChat")
        self.new_chat_button.clicked.connect(self.new_chat_requested.emit)
        layout.addWidget(self.new_chat_button)

        self.search = QLineEdit()
        self.search.setObjectName("chatSearch")
        self.search.setPlaceholderText("Search chats")
        self.search.textChanged.connect(self.search_changed.emit)
        layout.addWidget(self.search)

        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("sidebarScroll")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_widget = QWidget()
        self.list_layout = QVBoxLayout(self.list_widget)
        self.list_layout.setContentsMargins(0, 4, 0, 0)
        self.list_layout.setSpacing(4)
        self.list_layout.addStretch(1)
        self.scroll_area.setWidget(self.list_widget)
        layout.addWidget(self.scroll_area, 1)

    def toggle(self) -> None:
        self.set_expanded(not self._expanded)

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = expanded
        self._animation.stop()
        self._animation.setStartValue(self.maximumWidth())
        self._animation.setEndValue(self.EXPANDED_WIDTH if expanded else self.COLLAPSED_WIDTH)
        self._animation.start()
        self.setMinimumWidth(0 if not expanded else self.EXPANDED_WIDTH)

    def set_generating(self, generating: bool) -> None:
        self.new_chat_button.setEnabled(not generating)
        self.search.setEnabled(not generating)

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
                row.rename_requested.connect(self.rename_requested.emit)
                row.delete_requested.connect(self.delete_requested.emit)
                self.list_layout.insertWidget(self.list_layout.count() - 1, row)
