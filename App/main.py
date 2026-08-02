import sys
from typing import Any

import requests
from PySide6.QtCore import QObject, QThread, Qt, Signal, QTimer
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

MODEL_NAME = "qwen3.5:9b"
OLLAMA_URL = "http://127.0.0.1:11434/api/chat"


class AutoGrowingInput(QTextEdit):
    send_requested = Signal()

    MIN_HEIGHT = 52
    MAX_HEIGHT = 180

    def __init__(self) -> None:
        super().__init__()

        self.setPlaceholderText("Message AI Agent…")
        self.setAcceptRichText(False)

        self.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        self.document().contentsChanged.connect(self.update_height)

        self.setMinimumHeight(self.MIN_HEIGHT)
        self.setMaximumHeight(self.MAX_HEIGHT)
        self.update_height()

    def update_height(self) -> None:
        document_height = self.document().size().height()
        margins = self.contentsMargins()

        desired_height = int(
            document_height
            + margins.top()
            + margins.bottom()
            + 18
        )

        new_height = max(
            self.MIN_HEIGHT,
            min(desired_height, self.MAX_HEIGHT),
        )

        self.setFixedHeight(new_height)

        if desired_height > self.MAX_HEIGHT:
            self.setVerticalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAsNeeded
            )
        else:
            self.setVerticalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            )

    def keyPressEvent(self, event: QKeyEvent) -> None:
        is_enter = event.key() in (
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
        )

        shift_pressed = bool(
            event.modifiers()
            & Qt.KeyboardModifier.ShiftModifier
        )

        if is_enter and not shift_pressed:
            event.accept()
            self.send_requested.emit()
            return

        super().keyPressEvent(event)


class MessageBubble(QFrame):
    def __init__(
        self,
        text: str,
        user_message: bool = False,
        thinking: bool = False,
    ) -> None:
        super().__init__()

        self.user_message = user_message
        self.thinking = thinking

        if thinking:
            self.setObjectName("thinkingBubble")
        elif user_message:
            self.setObjectName("userBubble")
        else:
            self.setObjectName("assistantBubble")

        self.setMaximumWidth(760)

        self.setSizePolicy(
            QSizePolicy.Policy.Maximum,
            QSizePolicy.Policy.Minimum,
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(0)

        self.label = QLabel(text)
        self.label.setWordWrap(True)
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )

        self.label.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Minimum,
        )

        layout.addWidget(self.label)

    def set_text(self, text: str) -> None:
        self.label.setText(text)
        self.adjustSize()
        self.updateGeometry()


class OllamaWorker(QObject):
    finished = Signal(str)
    failed = Signal(str)

    def __init__(self, messages: list[dict[str, str]]) -> None:
        super().__init__()
        self.messages = messages

    def run(self) -> None:
        try:
            response = requests.post(
                OLLAMA_URL,
                json={
                    "model": MODEL_NAME,
                    "messages": self.messages,
                    "stream": False,
                    "keep_alive": "30m",
                },
                timeout=600,
            )

            response.raise_for_status()
            data: dict[str, Any] = response.json()

            answer = (
                data.get("message", {})
                .get("content", "")
                .strip()
            )

            if not answer:
                raise RuntimeError(
                    "Ollama returned an empty response."
                )

            self.finished.emit(answer)

        except requests.ConnectionError:
            self.failed.emit(
                "Could not connect to Ollama. "
                "Make sure Ollama is running."
            )

        except requests.Timeout:
            self.failed.emit(
                "The model took too long to respond."
            )

        except requests.RequestException as error:
            self.failed.emit(
                f"Ollama request failed: {error}"
            )

        except Exception as error:
            self.failed.emit(str(error))


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()

        self.thread: QThread | None = None
        self.worker: OllamaWorker | None = None
        self.thinking_bubble: MessageBubble | None = None

        self.messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": (
                    "You are a helpful local desktop AI assistant. "
                    "Be clear, accurate, concise, and practical."
                ),
            }
        ]

        self.setWindowTitle("AI Agent")
        self.resize(1050, 760)
        self.setMinimumSize(700, 520)

        self.build_interface()
        self.apply_style()

        self.add_message(
            "Hello. I’m ready when you are.",
            user_message=False,
        )

    def build_interface(self) -> None:
        central_widget = QWidget()
        central_widget.setObjectName("centralWidget")

        root_layout = QVBoxLayout(central_widget)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("header")

        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(26, 16, 26, 16)

        title_column = QVBoxLayout()
        title_column.setSpacing(2)

        title = QLabel("AI Agent")
        title.setObjectName("title")

        subtitle = QLabel(f"{MODEL_NAME}  •  Local")
        subtitle.setObjectName("subtitle")

        title_column.addWidget(title)
        title_column.addWidget(subtitle)

        self.connection_status = QLabel("Ready")
        self.connection_status.setObjectName("statusBadge")

        header_layout.addLayout(title_column)
        header_layout.addStretch()
        header_layout.addWidget(self.connection_status)

        root_layout.addWidget(header)

        self.message_container = QWidget()
        self.message_container.setObjectName(
            "messageContainer"
        )

        self.message_layout = QVBoxLayout(
            self.message_container
        )
        self.message_layout.setContentsMargins(
            48,
            30,
            48,
            30,
        )
        self.message_layout.setSpacing(20)
        self.message_layout.addStretch()

        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("chatScroll")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(
            QFrame.Shape.NoFrame
        )
        self.scroll_area.setWidget(
            self.message_container
        )

        root_layout.addWidget(self.scroll_area, 1)

        input_panel = QFrame()
        input_panel.setObjectName("inputPanel")

        input_panel_layout = QVBoxLayout(input_panel)
        input_panel_layout.setContentsMargins(
            48,
            14,
            48,
            18,
        )
        input_panel_layout.setSpacing(8)

        input_shell = QFrame()
        input_shell.setObjectName("inputShell")

        input_shell_layout = QHBoxLayout(input_shell)
        input_shell_layout.setContentsMargins(
            10,
            8,
            10,
            8,
        )
        input_shell_layout.setSpacing(10)
        input_shell_layout.setAlignment(
            Qt.AlignmentFlag.AlignBottom
        )

        self.input_box = AutoGrowingInput()
        self.input_box.send_requested.connect(
            self.send_message
        )

        self.send_button = QPushButton("Send")
        self.send_button.setObjectName("sendButton")
        self.send_button.setFixedSize(76, 42)
        self.send_button.clicked.connect(
            self.send_message
        )

        input_shell_layout.addWidget(
            self.input_box,
            1,
        )
        input_shell_layout.addWidget(
            self.send_button,
            0,
            Qt.AlignmentFlag.AlignBottom,
        )

        helper = QLabel(
            "Enter to send  •  Shift+Enter for a new line"
        )
        helper.setObjectName("helperText")
        helper.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )

        input_panel_layout.addWidget(input_shell)
        input_panel_layout.addWidget(helper)

        root_layout.addWidget(input_panel)
        self.setCentralWidget(central_widget)

    def add_message(
        self,
        text: str,
        user_message: bool,
        thinking: bool = False,
    ) -> MessageBubble:
        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(0)

        bubble = MessageBubble(
            text=text,
            user_message=user_message,
            thinking=thinking,
        )

        if user_message:
            row_layout.addStretch()
            row_layout.addWidget(bubble)
        else:
            row_layout.addWidget(bubble)
            row_layout.addStretch()

        self.message_layout.insertWidget(
            self.message_layout.count() - 1,
            row_widget,
        )

        bubble.setProperty("rowWidget", row_widget)
        self.scroll_to_bottom()

        return bubble

    def show_thinking(self) -> None:
        self.thinking_bubble = self.add_message(
            "Thinking…",
            user_message=False,
            thinking=True,
        )

    def remove_thinking(self) -> None:
        if self.thinking_bubble is None:
            return

        row_widget = self.thinking_bubble.property(
            "rowWidget"
        )

        if row_widget is not None:
            self.message_layout.removeWidget(row_widget)
            row_widget.deleteLater()

        self.thinking_bubble = None

    def scroll_to_bottom(self) -> None:
        QTimer.singleShot(
            0,
            lambda: self.scroll_area
            .verticalScrollBar()
            .setValue(
                self.scroll_area
                .verticalScrollBar()
                .maximum()
            ),
        )

    def send_message(self) -> None:
        message = self.input_box.toPlainText().strip()

        if not message:
            return

        if self.thread is not None:
            return

        self.add_message(
            message,
            user_message=True,
        )

        self.messages.append(
            {
                "role": "user",
                "content": message,
            }
        )

        self.input_box.clear()
        self.input_box.update_height()
        self.show_thinking()

        self.send_button.setEnabled(False)
        self.input_box.setEnabled(False)
        self.connection_status.setText("Thinking")

        self.thread = QThread()
        self.worker = OllamaWorker(
            self.messages.copy()
        )

        self.worker.moveToThread(self.thread)

        self.thread.started.connect(
            self.worker.run
        )

        self.worker.finished.connect(
            self.receive_response
        )
        self.worker.failed.connect(
            self.receive_error
        )

        self.worker.finished.connect(
            self.thread.quit
        )
        self.worker.failed.connect(
            self.thread.quit
        )

        self.thread.finished.connect(
            self.cleanup_worker
        )

        self.thread.start()

    def receive_response(self, answer: str) -> None:
        self.remove_thinking()

        self.messages.append(
            {
                "role": "assistant",
                "content": answer,
            }
        )

        self.add_message(
            answer,
            user_message=False,
        )

    def receive_error(self, error: str) -> None:
        self.remove_thinking()

        self.add_message(
            error,
            user_message=False,
        )

    def cleanup_worker(self) -> None:
        if self.worker is not None:
            self.worker.deleteLater()

        if self.thread is not None:
            self.thread.deleteLater()

        self.worker = None
        self.thread = None

        self.send_button.setEnabled(True)
        self.input_box.setEnabled(True)
        self.input_box.setFocus()
        self.connection_status.setText("Ready")

    def apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow,
            #centralWidget,
            #messageContainer {
                background-color: #181818;
                color: #f2f2f2;
                font-family: "Segoe UI";
                font-size: 15px;
            }

            #header {
                background-color: #202020;
                border-bottom: 1px solid #303030;
            }

            #title {
                color: #ffffff;
                font-size: 18px;
                font-weight: 600;
            }

            #subtitle {
                color: #929292;
                font-size: 12px;
            }

            #statusBadge {
                color: #d8d8d8;
                background-color: #303030;
                border: 1px solid #414141;
                border-radius: 11px;
                padding: 4px 10px;
                font-size: 12px;
            }

            #chatScroll {
                background-color: #181818;
                border: none;
            }

            QScrollBar:vertical {
                background: transparent;
                width: 10px;
                margin: 4px;
            }

            QScrollBar::handle:vertical {
                background: #444444;
                border-radius: 5px;
                min-height: 30px;
            }

            QScrollBar::handle:vertical:hover {
                background: #555555;
            }

            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {
                height: 0px;
            }

            #userBubble {
                background-color: #303030;
                border: 1px solid #3c3c3c;
                border-radius: 17px;
            }

            #assistantBubble {
                background-color: #222222;
                border: 1px solid #333333;
                border-radius: 17px;
            }

            #thinkingBubble {
                background-color: #222222;
                border: 1px solid #333333;
                border-radius: 17px;
                color: #999999;
                font-style: italic;
            }

            #userBubble QLabel,
            #assistantBubble QLabel {
                color: #f1f1f1;
                background: transparent;
            }

            #thinkingBubble QLabel {
                color: #999999;
                background: transparent;
                font-style: italic;
            }

            #inputPanel {
                background-color: #181818;
                border-top: 1px solid #252525;
            }

            #inputShell {
                background-color: #292929;
                border: 1px solid #424242;
                border-radius: 20px;
            }

            #inputShell:focus-within {
                border: 1px solid #666666;
            }

            AutoGrowingInput {
                background-color: transparent;
                color: #ffffff;
                border: none;
                padding: 7px 8px;
                selection-background-color: #696969;
            }

            AutoGrowingInput:disabled {
                color: #8f8f8f;
            }

            #sendButton {
                background-color: #f1f1f1;
                color: #151515;
                border: none;
                border-radius: 14px;
                font-weight: 600;
            }

            #sendButton:hover {
                background-color: #ffffff;
            }

            #sendButton:pressed {
                background-color: #d3d3d3;
            }

            #sendButton:disabled {
                background-color: #555555;
                color: #999999;
            }

            #helperText {
                color: #777777;
                font-size: 11px;
            }
            """
        )


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("AI Agent")

    window = MainWindow()
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())