"""Model discovery and selection management."""

from __future__ import annotations

import requests
from PySide6.QtCore import QObject, QThread, Signal, Slot

from core.config import CONNECT_TIMEOUT_SECONDS, OLLAMA_BASE_URL
from models.ollama import OllamaError


def discover_models() -> list[str]:
    response = requests.get(
        f"{OLLAMA_BASE_URL}/api/tags",
        timeout=(CONNECT_TIMEOUT_SECONDS, 20),
    )
    response.raise_for_status()
    data = response.json()
    models = data.get("models", [])
    if not isinstance(models, list):
        raise OllamaError("Ollama returned an invalid model list.")
    names = sorted(
        str(model.get("name", ""))
        for model in models
        if isinstance(model, dict) and model.get("name")
    )
    return names


class ModelDiscoveryWorker(QObject):
    finished = Signal(list)
    failed = Signal(str)
    stopped = Signal()

    @Slot()
    def run(self) -> None:
        try:
            self.finished.emit(discover_models())
        except requests.ConnectionError:
            self.failed.emit("Could not connect to Ollama for model discovery.")
        except requests.Timeout:
            self.failed.emit("Ollama model discovery timed out.")
        except (requests.RequestException, OllamaError) as error:
            self.failed.emit(str(error))
        finally:
            self.stopped.emit()


class ModelManager(QObject):
    """Runs model discovery outside the UI thread."""

    finished = Signal(list)
    failed = Signal(str)
    stopped = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.thread: QThread | None = None
        self.worker: ModelDiscoveryWorker | None = None

    @property
    def running(self) -> bool:
        return self.thread is not None

    def start(self) -> None:
        if self.running:
            return
        thread = QThread(self)
        worker = ModelDiscoveryWorker()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self.finished)
        worker.failed.connect(self.failed)
        worker.stopped.connect(worker.deleteLater)
        worker.stopped.connect(thread.quit)
        thread.finished.connect(self._cleanup)
        self.thread = thread
        self.worker = worker
        thread.start()

    def shutdown(self, wait_ms: int = 1000) -> None:
        if self.thread is not None:
            thread = self.thread
            thread.quit()
            thread.wait(wait_ms)
            self.thread = None
            self.worker = None

    def _cleanup(self) -> None:
        if self.thread is not None:
            self.thread.deleteLater()
        self.thread = None
        self.worker = None
        self.stopped.emit()
