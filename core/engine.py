"""Core AI orchestration for VioletAI."""

from __future__ import annotations

from PySide6.QtCore import QObject, QThread, Signal

from models.manager import ModelManager
from models.ollama import OllamaWorker


class Engine(QObject):
    """Runs one streaming chat request outside the UI thread."""

    connected = Signal()
    chunk_received = Signal(str)
    finished = Signal(str)
    cancelled = Signal()
    failed = Signal(str)
    stopped = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.thread: QThread | None = None
        self.worker: OllamaWorker | None = None

    @property
    def running(self) -> bool:
        return self.thread is not None

    def start(
        self,
        ollama_messages: list[dict[str, str]],
        model_name: str,
        think: bool | None = None,
    ) -> None:
        if self.running:
            return
        thread = QThread(self)
        worker = OllamaWorker(ollama_messages, model_name, think=think)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.connected.connect(self.connected)
        worker.chunk_received.connect(self.chunk_received)
        worker.finished.connect(self.finished)
        worker.cancelled.connect(self.cancelled)
        worker.failed.connect(self.failed)
        worker.stopped.connect(worker.deleteLater)
        worker.stopped.connect(thread.quit)
        thread.finished.connect(self._cleanup)
        self.thread = thread
        self.worker = worker
        thread.start()

    def cancel(self) -> None:
        if self.worker is not None:
            self.worker.cancel()

    def shutdown(self, wait_ms: int = 2500) -> bool:
        if self.worker is not None:
            self.worker.cancel()
        if self.thread is not None:
            thread = self.thread
            thread.quit()
            if not thread.wait(wait_ms):
                return False
            self.thread = None
            self.worker = None
        return True

    def _cleanup(self) -> None:
        if self.thread is not None:
            self.thread.deleteLater()
        self.thread = None
        self.worker = None
        self.stopped.emit()


__all__ = ["Engine", "ModelManager"]
