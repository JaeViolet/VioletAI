"""Background Ollama streaming worker."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from typing import Any

import requests
from PySide6.QtCore import QObject, Signal, Slot

from config import (
    CONNECT_TIMEOUT_SECONDS,
    DEFAULT_MODEL_NAME,
    KEEP_ALIVE,
    OLLAMA_BASE_URL,
    OLLAMA_URL,
    READ_TIMEOUT_SECONDS,
)


class OllamaError(RuntimeError):
    """Base class for user-facing Ollama errors."""


class InvalidStreamError(OllamaError):
    """Raised when Ollama returns malformed newline-delimited JSON."""


class EmptyResponseError(OllamaError):
    """Raised when a completed stream contains no assistant text."""


class ModelMissingError(OllamaError):
    """Raised when the configured model is not available in Ollama."""


def parse_stream_line(line: str | bytes) -> dict[str, Any]:
    text = line.decode("utf-8") if isinstance(line, bytes) else line
    text = text.strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise InvalidStreamError(f"Ollama sent invalid streamed JSON: {text}") from error
    if not isinstance(data, dict):
        raise InvalidStreamError("Ollama streamed JSON was not an object.")
    return data


def iter_message_chunks(
    lines: Iterable[str | bytes],
    model_name: str = DEFAULT_MODEL_NAME,
) -> Iterator[tuple[str, bool]]:
    for line in lines:
        data = parse_stream_line(line)
        if not data:
            continue
        if error := data.get("error"):
            message = str(error)
            if "not found" in message.lower() or "pull model" in message.lower():
                raise ModelMissingError(
                    f"Configured model '{model_name}' is missing. Run: ollama pull {model_name}"
                )
            raise OllamaError(message)
        chunk = data.get("message", {}).get("content", "")
        yield str(chunk), bool(data.get("done"))


def _http_error_message(response: requests.Response, model_name: str) -> str:
    try:
        data = response.json()
    except ValueError:
        data = {}
    error_text = str(data.get("error") or response.text or response.reason)
    if response.status_code == 404 or "not found" in error_text.lower():
        return f"Configured model '{model_name}' is missing. Run: ollama pull {model_name}"
    return f"Ollama request failed with HTTP {response.status_code}: {error_text}"


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


class OllamaWorker(QObject):
    """Run one streaming chat request outside the UI thread."""

    connected = Signal()
    request_started = Signal()
    chunk_received = Signal(str)
    finished = Signal(str)
    cancelled = Signal()
    failed = Signal(str)
    stopped = Signal()

    def __init__(
        self,
        messages: list[dict[str, str]],
        model_name: str = DEFAULT_MODEL_NAME,
    ) -> None:
        super().__init__()
        self._messages = messages
        self._model_name = model_name
        self._cancelled = False
        self._response: requests.Response | None = None

    @Slot()
    def run(self) -> None:
        complete_answer: list[str] = []
        try:
            self.request_started.emit()
            self._response = requests.post(
                OLLAMA_URL,
                json={
                    "model": self._model_name,
                    "messages": self._messages,
                    "stream": True,
                    "keep_alive": KEEP_ALIVE,
                },
                stream=True,
                timeout=(CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS),
            )
            if self._response.status_code >= 400:
                raise OllamaError(_http_error_message(self._response, self._model_name))

            self.connected.emit()
            for chunk, done in iter_message_chunks(
                self._response.iter_lines(decode_unicode=True),
                self._model_name,
            ):
                if self._cancelled:
                    self.cancelled.emit()
                    return
                if chunk:
                    complete_answer.append(chunk)
                    self.chunk_received.emit(chunk)
                if done:
                    break

            if self._cancelled:
                self.cancelled.emit()
                return

            answer = "".join(complete_answer).strip()
            if not answer:
                raise EmptyResponseError("Ollama returned an empty response.")
            self.finished.emit(answer)

        except requests.ConnectionError:
            if not self._cancelled:
                self.failed.emit("Could not connect to Ollama. Make sure Ollama is running.")
        except requests.Timeout:
            if not self._cancelled:
                self.failed.emit(
                    f"Ollama request timed out after {READ_TIMEOUT_SECONDS} seconds."
                )
        except InvalidStreamError as error:
            if not self._cancelled:
                self.failed.emit(str(error))
        except EmptyResponseError as error:
            if not self._cancelled:
                self.failed.emit(str(error))
        except ModelMissingError as error:
            if not self._cancelled:
                self.failed.emit(str(error))
        except (requests.RequestException, OllamaError) as error:
            if not self._cancelled:
                self.failed.emit(str(error))
        except Exception as error:
            if not self._cancelled:
                self.failed.emit(f"Unexpected Ollama error: {error}")
        finally:
            if self._response is not None:
                self._response.close()
                self._response = None
            self.stopped.emit()

    def cancel(self) -> None:
        self._cancelled = True
        if self._response is not None:
            self._response.close()


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
