"""Reusable native Qt widgets for the chat interface."""

from __future__ import annotations

import re

from PySide6.QtCore import QEvent, QRegularExpression, QSize, Qt, Signal, QTimer
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QKeyEvent,
    QSyntaxHighlighter,
    QTextCharFormat,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class AutoGrowingInput(QTextEdit):
    send_requested = Signal()

    MIN_HEIGHT = 50
    MAX_HEIGHT = 180

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("messageInput")
        self.setPlaceholderText("Message AI Agent")
        self.setAcceptRichText(False)
        self.setTabChangesFocus(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.document().documentLayout().documentSizeChanged.connect(
            self._update_height
        )
        self.setMinimumHeight(self.MIN_HEIGHT)
        self._update_height()

    def sizeHint(self) -> QSize:
        return QSize(super().sizeHint().width(), self.height())

    def _update_height(self) -> None:
        document_height = self.document().documentLayout().documentSize().height()
        frame = self.frameWidth() * 2
        desired = int(document_height + frame + 18)
        height = max(self.MIN_HEIGHT, min(desired, self.MAX_HEIGHT))
        self.setFixedHeight(height)
        policy = (
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
            if desired > self.MAX_HEIGHT
            else Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.setVerticalScrollBarPolicy(policy)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        is_enter = event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        has_shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if is_enter and not has_shift:
            event.accept()
            self.send_requested.emit()
            return
        super().keyPressEvent(event)


class MarkdownView(QTextBrowser):
    """Selectable Markdown text that grows to its complete document height."""

    height_changed = Signal()

    def __init__(self, markdown: str = "") -> None:
        super().__init__()
        self.setObjectName("markdownView")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setOpenExternalLinks(True)
        self.setReadOnly(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.document().setDocumentMargin(0)
        self.document().documentLayout().documentSizeChanged.connect(
            lambda _size: self._fit_height()
        )
        self.set_markdown(markdown)

    def set_markdown(self, markdown: str) -> None:
        self._markdown = markdown
        self.document().setMarkdown(markdown)
        QTimer.singleShot(0, self._fit_height)

    def resizeEvent(self, event: QEvent) -> None:
        super().resizeEvent(event)
        self.document().setTextWidth(self.viewport().width())
        self._fit_height()

    def _fit_height(self) -> None:
        width = max(1, self.viewport().width())
        if self.document().textWidth() != width:
            self.document().setTextWidth(width)
        height = max(1, int(self.document().size().height()) + 2)
        if self.height() != height:
            self.setFixedHeight(height)
            self.setMinimumHeight(height)
            self.setMaximumHeight(height)
            self.height_changed.emit()


class CodeHighlighter(QSyntaxHighlighter):
    """Small dependency-free highlighter for common code constructs."""

    KEYWORDS = {
        "and", "as", "assert", "async", "await", "break", "case", "catch",
        "class", "const", "continue", "def", "default", "delete", "do",
        "elif", "else", "except", "export", "extends", "false", "finally",
        "for", "from", "function", "if", "import", "in", "interface", "is",
        "lambda", "let", "new", "none", "not", "null", "or", "pass",
        "False", "None", "True", "raise", "return", "static", "super",
        "switch", "throw", "true",
        "try", "type", "typeof", "var", "while", "with", "yield",
    }

    def __init__(self, document) -> None:
        super().__init__(document)
        self._rules: list[tuple[QRegularExpression, QTextCharFormat]] = []
        self._add_rule(r"\b(" + "|".join(sorted(self.KEYWORDS)) + r")\b", "#c792ea")
        self._add_rule(r"\b\d+(?:\.\d+)?\b", "#f78c6c")
        self._add_rule(r"(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*')", "#c3e88d")
        self._add_rule(r"(?:#|//).*$", "#697098")

    def _add_rule(self, pattern: str, color: str) -> None:
        text_format = QTextCharFormat()
        text_format.setForeground(QColor(color))
        self._rules.append((QRegularExpression(pattern), text_format))

    def highlightBlock(self, text: str) -> None:
        for pattern, text_format in self._rules:
            iterator = pattern.globalMatch(text)
            while iterator.hasNext():
                match = iterator.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), text_format)


class CodeBlock(QFrame):
    def __init__(self, language: str, code: str) -> None:
        super().__init__()
        self.setObjectName("codeBlock")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("codeHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 5, 7, 5)
        language_label = QLabel(language or "code")
        language_label.setObjectName("codeLanguage")
        copy_button = QPushButton("Copy")
        copy_button.setObjectName("copyButton")
        copy_button.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_button.clicked.connect(lambda: self._copy(copy_button))
        header_layout.addWidget(language_label)
        header_layout.addStretch()
        header_layout.addWidget(copy_button)
        layout.addWidget(header)

        self.editor = QPlainTextEdit(code.rstrip())
        self.editor.setObjectName("codeEditor")
        self.editor.setReadOnly(True)
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.editor.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.editor.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        fixed_font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        fixed_font.setPointSize(10)
        self.editor.setFont(fixed_font)
        self.editor.document().setDocumentMargin(12)
        self.editor.document().documentLayout().documentSizeChanged.connect(
            lambda _size: self._fit_height()
        )
        self.highlighter = CodeHighlighter(self.editor.document())
        self._fit_height()
        layout.addWidget(self.editor)

    def _fit_height(self) -> None:
        metrics = self.editor.fontMetrics()
        line_count = max(1, self.editor.document().blockCount())
        height = line_count * metrics.lineSpacing() + 28
        self.editor.setFixedHeight(height)
        self.editor.setMinimumHeight(height)
        self.editor.setMaximumHeight(height)
        self.updateGeometry()

    def _copy(self, button: QPushButton) -> None:
        QApplication.clipboard().setText(self.editor.toPlainText())
        button.setText("Copied")
        QTimer.singleShot(1400, lambda: button.setText("Copy"))


class MessageBubble(QFrame):
    """A responsive message with Markdown and native code-block controls."""

    _FENCE = re.compile(r"```([^\n`]*)\n(.*?)(?:```|\Z)", re.DOTALL)

    def __init__(self, text: str, role: str) -> None:
        super().__init__()
        self.role = role
        self._text = text
        self.setObjectName(f"{role}Bubble")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self.layout = QVBoxLayout(self)
        margins = (16, 11, 16, 11) if role == "user" else (4, 7, 4, 7)
        self.layout.setContentsMargins(*margins)
        self.layout.setSpacing(10)
        self.set_text(text)

    def set_text(self, text: str) -> None:
        self._text = text
        while self.layout.count():
            item = self.layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        cursor = 0
        found_code = False
        for match in self._FENCE.finditer(text):
            found_code = True
            if markdown := text[cursor:match.start()].strip("\n"):
                self.layout.addWidget(MarkdownView(markdown))
            self.layout.addWidget(CodeBlock(match.group(1).strip(), match.group(2)))
            cursor = match.end()
        remainder = text[cursor:].strip("\n")
        if remainder or not found_code:
            self.layout.addWidget(MarkdownView(remainder or " "))
        self.adjustSize()
        self.updateGeometry()

    def text(self) -> str:
        return self._text


class MessageActions(QFrame):
    copy_requested = Signal()
    regenerate_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("messageActions")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(6)

        self.copy_button = QPushButton("Copy")
        self.copy_button.setObjectName("actionButton")
        self.copy_button.setToolTip("Copy response")
        self.copy_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_button.clicked.connect(self._copy_clicked)

        self.regenerate_button = QPushButton("Regenerate")
        self.regenerate_button.setObjectName("actionButton")
        self.regenerate_button.setToolTip("Regenerate response")
        self.regenerate_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.regenerate_button.clicked.connect(self.regenerate_requested.emit)

        layout.addWidget(self.copy_button)
        layout.addWidget(self.regenerate_button)
        layout.addStretch()

    def _copy_clicked(self) -> None:
        self.copy_requested.emit()
        self.copy_button.setText("Copied")
        QTimer.singleShot(1400, lambda: self.copy_button.setText("Copy"))


class ThinkingBubble(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("thinkingBubble")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 8, 4, 8)
        layout.setSpacing(4)
        label = QLabel("Thinking")
        label.setObjectName("thinkingLabel")
        self.dots = QLabel(".")
        self.dots.setObjectName("thinkingDots")
        layout.addWidget(label)
        layout.addWidget(self.dots)
        layout.addStretch()
        self._step = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._animate)
        self.timer.start(350)

    def _animate(self) -> None:
        self._step = (self._step + 1) % 4
        self.dots.setText("." * self._step)
