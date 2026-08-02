"""Reusable native Qt widgets for the chat interface."""

from __future__ import annotations

import re

from PySide6.QtCore import QEvent, QRegularExpression, QSize, Qt, Signal, QTimer
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QFontMetrics,
    QPainter,
    QPen,
    QKeyEvent,
    QSyntaxHighlighter,
    QTextCharFormat,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QSizePolicy,
    QTextBrowser,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from design import Colors, PNG_CONTROL_ICON_SIZE, icon


class ModelSelector(QComboBox):
    """QComboBox with a reliable chevron that survives native stylesheet quirks."""

    def arrow_color(self) -> str:
        return Colors.TEXT_FAINT if not self.isEnabled() else Colors.TEXT_MUTED

    def paintEvent(self, event: QEvent) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(self.arrow_color()), 1.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        x = self.width() - 17
        y = self.height() / 2 - 1
        painter.drawLine(x - 3, y, x, y + 3)
        painter.drawLine(x, y + 3, x + 3, y)
        painter.end()


class AutoGrowingInput(QTextEdit):
    send_requested = Signal()
    height_changed = Signal(int)

    MIN_HEIGHT = 30
    MAX_HEIGHT = 180

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("messageInput")
        self.setPlaceholderText("Ask VioletAI")
        self.setAcceptRichText(False)
        self.setTabChangesFocus(True)
        self._prompt_history: list[str] = []
        self._history_index: int | None = None
        self._draft_before_history = ""
        self._updating_height = False
        self._last_desired_height = 0
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.document().documentLayout().documentSizeChanged.connect(
            self._update_height
        )
        self.setMinimumHeight(self.MIN_HEIGHT)
        self._update_height()

    def sizeHint(self) -> QSize:
        return QSize(super().sizeHint().width(), self.height())

    def setPlainText(self, text: str) -> None:
        super().setPlainText(text)
        self._update_height()

    def _update_height(self) -> None:
        if self._updating_height:
            return
        self._updating_height = True
        document_height = self.document().documentLayout().documentSize().height()
        frame = self.frameWidth() * 2
        line_height = self.fontMetrics().lineSpacing()
        has_multiple_blocks = self.document().blockCount() > 1
        is_empty = not self.toPlainText()
        if is_empty or (not has_multiple_blocks and document_height <= line_height + 12):
            desired = self.MIN_HEIGHT
        else:
            block_height = self.document().blockCount() * line_height + frame + 10
            desired = int(max(document_height + frame + 8, block_height))
        height = max(self.MIN_HEIGHT, min(desired, self.MAX_HEIGHT))
        try:
            policy = (
                Qt.ScrollBarPolicy.ScrollBarAsNeeded
                if desired > self.MAX_HEIGHT
                else Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            )
            if self.verticalScrollBarPolicy() != policy:
                self.setVerticalScrollBarPolicy(policy)
            single_line_height = line_height
            is_single_visual_line = (
                self.document().blockCount() <= 1
                and document_height <= single_line_height + 12
            )
            document_margin = int(max(0, (height - single_line_height - frame) / 2)) if is_single_visual_line else 4
            if int(self.document().documentMargin()) != document_margin:
                self.document().setDocumentMargin(document_margin)
            if self._last_desired_height != height:
                self._last_desired_height = height
                self.setFixedHeight(height)
                self.height_changed.emit(height)
        finally:
            self._updating_height = False

    def is_visually_multiline(self) -> bool:
        line_height = max(1, self.fontMetrics().lineSpacing())
        document_height = self.document().documentLayout().documentSize().height()
        return self.document().blockCount() > 1 or document_height > line_height + 14

    def keyPressEvent(self, event: QKeyEvent) -> None:
        is_enter = event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        has_shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if is_enter and not has_shift:
            event.accept()
            self.send_requested.emit()
            return
        if event.key() == Qt.Key.Key_Up and not event.modifiers():
            if self._should_use_previous_prompt():
                event.accept()
                self._previous_prompt()
                return
        if event.key() == Qt.Key.Key_Down and not event.modifiers():
            if self._should_use_next_prompt():
                event.accept()
                self._next_prompt()
                return
        super().keyPressEvent(event)

    def remember_prompt(self, prompt: str) -> None:
        prompt = prompt.strip()
        if prompt and (not self._prompt_history or self._prompt_history[-1] != prompt):
            self._prompt_history.append(prompt)
        self._history_index = None
        self._draft_before_history = ""

    def _should_use_previous_prompt(self) -> bool:
        cursor = self.textCursor()
        return not self.toPlainText() or cursor.blockNumber() == 0

    def _should_use_next_prompt(self) -> bool:
        if self._history_index is None:
            return False
        cursor = self.textCursor()
        return cursor.blockNumber() == self.document().blockCount() - 1

    def _previous_prompt(self) -> None:
        if not self._prompt_history:
            return
        if self._history_index is None:
            self._draft_before_history = self.toPlainText()
            self._history_index = len(self._prompt_history) - 1
        else:
            self._history_index = max(0, self._history_index - 1)
        self.setPlainText(self._prompt_history[self._history_index])
        self.moveCursor(self.textCursor().MoveOperation.End)

    def _next_prompt(self) -> None:
        if self._history_index is None:
            return
        if self._history_index >= len(self._prompt_history) - 1:
            self._history_index = None
            self.setPlainText(self._draft_before_history)
        else:
            self._history_index += 1
            self.setPlainText(self._prompt_history[self._history_index])
        self.moveCursor(self.textCursor().MoveOperation.End)


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
        self._fit_height()

    def sizeHint(self) -> QSize:
        return QSize(super().sizeHint().width(), self._document_height())

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        self.document().setTextWidth(max(1, width))
        return self._document_height()

    def resizeEvent(self, event: QEvent) -> None:
        super().resizeEvent(event)
        self.document().setTextWidth(self.viewport().width())
        self._fit_height()

    def _fit_height(self) -> None:
        width = max(1, self.viewport().width())
        if self.document().textWidth() != width:
            self.document().setTextWidth(width)
        height = self._document_height()
        if self.height() != height:
            self.setFixedHeight(height)
            self.setMinimumHeight(height)
            self.setMaximumHeight(height)
            self.height_changed.emit()
        self.updateGeometry()

    def _document_height(self) -> int:
        return max(1, int(self.document().size().height()) + 4)


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
    COLLAPSED_LINES = 12

    def __init__(self, language: str, code: str) -> None:
        super().__init__()
        self.setObjectName("codeBlock")
        self._code = code.rstrip()
        self._collapsed = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("codeHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 5, 7, 5)
        language_label = QLabel(self._label_for(language))
        language_label.setObjectName("codeLanguage")
        self.copied_label = QLabel("")
        self.copied_label.setObjectName("copiedLabel")

        self.collapse_button = QToolButton()
        self.collapse_button.setObjectName("copyButton")
        self.collapse_button.setToolTip("Expand code" if self._collapsed else "Collapse code")
        self.collapse_button.setIcon(icon("expand" if self._collapsed else "collapse"))
        self.collapse_button.clicked.connect(self.toggle_collapsed)
        self.collapse_button.setVisible(False)

        copy_button = QToolButton()
        copy_button.setObjectName("copyButton")
        copy_button.setToolTip("Copy code")
        copy_button.setIcon(icon("copy", size=PNG_CONTROL_ICON_SIZE))
        copy_button.setIconSize(QSize(PNG_CONTROL_ICON_SIZE, PNG_CONTROL_ICON_SIZE))
        copy_button.clicked.connect(self._copy)
        header_layout.addWidget(language_label)
        header_layout.addStretch()
        header_layout.addWidget(self.copied_label)
        header_layout.addWidget(self.collapse_button)
        header_layout.addWidget(copy_button)
        layout.addWidget(header)

        self.editor = QPlainTextEdit(self._code)
        self.editor.setObjectName("codeEditor")
        self.editor.setReadOnly(True)
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.editor.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.editor.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
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

    def sizeHint(self) -> QSize:
        margins = self.layout().contentsMargins()
        header_height = self.layout().itemAt(0).widget().sizeHint().height()
        return QSize(
            super().sizeHint().width(),
            header_height + self._editor_height() + margins.top() + margins.bottom(),
        )

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def resizeEvent(self, event: QEvent) -> None:
        super().resizeEvent(event)
        self._fit_height()

    def _fit_height(self) -> None:
        height = self._editor_height()
        self.editor.setFixedHeight(height)
        self.editor.setMinimumHeight(height)
        self.editor.setMaximumHeight(height)
        self.updateGeometry()

    def _editor_height(self) -> int:
        if self._collapsed:
            metrics = self.editor.fontMetrics()
            return self._visible_line_count() * metrics.lineSpacing() + 28
        self.editor.document().setTextWidth(max(1, self.editor.viewport().width()))
        metrics = self.editor.fontMetrics()
        line_height = self.editor.document().blockCount() * metrics.lineSpacing() + 42
        document_height = int(self.editor.document().size().height()) + 80
        return max(1, line_height, document_height)

    def _line_count(self) -> int:
        return max(1, self._code.count("\n") + 1)

    def _visible_line_count(self) -> int:
        if self._collapsed:
            return min(self.COLLAPSED_LINES, self._line_count())
        return self._line_count()

    def _label_for(self, language: str) -> str:
        value = language.strip()
        if not value:
            return "code"
        parts = value.split(maxsplit=1)
        if len(parts) == 2 and any(parts[1].endswith(ext) for ext in (".py", ".js", ".ts", ".json", ".md", ".txt")):
            return f"{parts[0]} - {parts[1]}"
        return value

    def toggle_collapsed(self) -> None:
        self._collapsed = not self._collapsed
        self.collapse_button.setToolTip("Expand code" if self._collapsed else "Collapse code")
        self.collapse_button.setIcon(icon("expand" if self._collapsed else "collapse"))
        self._fit_height()

    def _copy(self) -> None:
        QApplication.clipboard().setText(self.editor.toPlainText())
        self.copied_label.setText("Copied")
        QTimer.singleShot(1000, lambda: self.copied_label.setText(""))


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

    def preferred_width(self, max_width: int) -> int:
        """Return a compact width for user bubbles without exceeding max_width."""
        if self.role != "user":
            return max_width
        margins = self.layout.contentsMargins()
        horizontal_padding = margins.left() + margins.right()
        metrics = QFontMetrics(self.font())
        natural = 0
        for paragraph in (self._text or " ").splitlines() or [" "]:
            if not paragraph:
                paragraph = " "
            natural = max(natural, metrics.horizontalAdvance(paragraph.expandtabs(4)))
        return min(max_width, max(46, natural + horizontal_padding + 8))

    def set_text(self, text: str) -> None:
        self._text = text
        if not self._FENCE.search(text) and self.layout.count() == 1:
            widget = self.layout.itemAt(0).widget()
            if isinstance(widget, MarkdownView):
                widget.set_markdown(text or " ")
                self.layout.invalidate()
                self.adjustSize()
                self.updateGeometry()
                return

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
        self.layout.invalidate()
        self.adjustSize()
        self.updateGeometry()

    def resizeEvent(self, event: QEvent) -> None:
        super().resizeEvent(event)
        for index in range(self.layout.count()):
            widget = self.layout.itemAt(index).widget()
            if isinstance(widget, MarkdownView):
                widget._fit_height()
        self.layout.invalidate()
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

        self.copy_button = QToolButton()
        self.copy_button.setObjectName("actionButton")
        self.copy_button.setToolTip("Copy response")
        self.copy_button.setIcon(icon("copy", size=PNG_CONTROL_ICON_SIZE))
        self.copy_button.setIconSize(QSize(PNG_CONTROL_ICON_SIZE, PNG_CONTROL_ICON_SIZE))
        self.copy_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_button.clicked.connect(self._copy_clicked)

        self.regenerate_button = QToolButton()
        self.regenerate_button.setObjectName("actionButton")
        self.regenerate_button.setToolTip("Regenerate response")
        self.regenerate_button.setIcon(icon("regen", size=PNG_CONTROL_ICON_SIZE))
        self.regenerate_button.setIconSize(QSize(PNG_CONTROL_ICON_SIZE, PNG_CONTROL_ICON_SIZE))
        self.regenerate_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.regenerate_button.clicked.connect(self.regenerate_requested.emit)
        self.copied_label = QLabel("")
        self.copied_label.setObjectName("copiedLabel")

        layout.addWidget(self.copy_button)
        layout.addWidget(self.regenerate_button)
        layout.addWidget(self.copied_label)
        layout.addStretch()

    def _copy_clicked(self) -> None:
        self.copy_requested.emit()
        self.copied_label.setText("Copied")
        QTimer.singleShot(1000, lambda: self.copied_label.setText(""))


class ThinkingBubble(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("thinkingBubble")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 8, 4, 8)
        layout.setSpacing(4)
        self.dots = QLabel(".")
        self.dots.setObjectName("thinkingDots")
        layout.addWidget(self.dots)
        layout.addStretch()
        self._step = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._animate)
        self.timer.start(350)

    def _animate(self) -> None:
        self._step = (self._step + 1) % 4
        self.dots.setText("." * self._step)
