"""Background Ollama streaming worker."""

from __future__ import annotations

import json
from typing import Any

import requests
from PySide6.QtCore import QObject, Signal, Slot

from config import KEEP_ALIVE, MODEL_NAME, OLLAMA_URL


class OllamaWorker(QObject):
    """Run one streaming chat request outside the UI thread."""

    chunk_received = Signal(str)
    finished = Signal(str)
    failed = Signal(str)
    stopped = Signal()

    def __init__(self, messages: list[dict[str, str]]) -> None:
        super().__init__()
        self._messages = messages
        self._cancelled = False
        self._response: requests.Response | None = None

    @Slot()
    def run(self) -> None:
        complete_answer: list[str] = []
        try:
            self._response = requests.post(
                OLLAMA_URL,
                json={
                    "model": MODEL_NAME,
                    "messages": self._messages,
                    "stream": True,
                    "keep_alive": KEEP_ALIVE,
                },
                stream=True,
                timeout=(10, 600),
            )
            self._response.raise_for_status()

            for line in self._response.iter_lines(decode_unicode=True):
                if self._cancelled:
                    return
                if not line:
                    continue

                data: dict[str, Any] = json.loads(line)
                if error := data.get("error"):
                    raise RuntimeError(str(error))

                chunk = str(data.get("message", {}).get("content", ""))
                if chunk:
                    complete_answer.append(chunk)
                    self.chunk_received.emit(chunk)

                if data.get("done"):
                    break

            if self._cancelled:
                return

            answer = "".join(complete_answer).strip()
            if not answer:
                raise RuntimeError("Ollama returned an empty response.")
            self.finished.emit(answer)

        except requests.ConnectionError:
            if not self._cancelled:
                self.failed.emit(
                    "Could not connect to Ollama. Make sure Ollama is running."
                )
        except requests.Timeout:
            if not self._cancelled:
                self.failed.emit("The model took too long to respond.")
        except (requests.RequestException, json.JSONDecodeError) as error:
            if not self._cancelled:
                self.failed.emit(f"Ollama request failed: {error}")
        except Exception as error:  # Keep worker failures out of the GUI event loop.
            if not self._cancelled:
                self.failed.emit(str(error))
        finally:
            if self._response is not None:
                self._response.close()
                self._response = None
            self.stopped.emit()

    def cancel(self) -> None:
        self._cancelled = True
        if self._response is not None:
            self._response.close()
