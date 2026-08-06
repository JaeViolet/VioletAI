"""Background Ollama streaming worker."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from time import perf_counter
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


def _visible_content_from_event(data: dict[str, Any]) -> str:
    message = data.get("message")
    if isinstance(message, dict):
        return str(message.get("content") or "")
    return str(data.get("response") or "")


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
        read_timeout_seconds: int = READ_TIMEOUT_SECONDS,
        options: dict[str, Any] | None = None,
        think: bool | None = None,
    ) -> None:
        super().__init__()
        self._messages = messages
        self._model_name = model_name
        self._read_timeout_seconds = read_timeout_seconds
        self._options = options
        self._think = think
        self._cancelled = False
        self._response: requests.Response | None = None

    @Slot()
    def run(self) -> None:
        complete_answer: list[str] = []
        event_count = 0
        empty_event_count = 0
        done_seen = False
        first_event_at: float | None = None
        first_visible_token_at: float | None = None
        try:
            self.request_started.emit()
            payload: dict[str, Any] = {
                "model": self._model_name,
                "messages": self._messages,
                "stream": True,
                "keep_alive": KEEP_ALIVE,
            }
            if self._options is not None:
                payload["options"] = self._options
            if self._think is not None:
                payload["think"] = self._think
            self._response = requests.post(
                OLLAMA_URL,
                json=payload,
                stream=True,
                timeout=(CONNECT_TIMEOUT_SECONDS, self._read_timeout_seconds),
            )
            if self._response.status_code >= 400:
                raise OllamaError(_http_error_message(self._response, self._model_name))

            self.connected.emit()
            for line in self._response.iter_lines(decode_unicode=True):
                if first_event_at is None:
                    first_event_at = perf_counter()
                data = parse_stream_line(line)
                event_count += 1
                if not data:
                    empty_event_count += 1
                    continue
                if error := data.get("error"):
                    message = str(error)
                    if "not found" in message.lower() or "pull model" in message.lower():
                        raise ModelMissingError(
                            f"Configured model '{self._model_name}' is missing. Run: ollama pull {self._model_name}"
                        )
                    raise OllamaError(message)
                chunk = _visible_content_from_event(data)
                done = bool(data.get("done"))
                done_seen = done_seen or done
                if self._cancelled:
                    self.cancelled.emit()
                    return
                if chunk:
                    if first_visible_token_at is None:
                        first_visible_token_at = perf_counter()
                    complete_answer.append(chunk)
                    self.chunk_received.emit(chunk)
                else:
                    empty_event_count += 1
                if done:
                    break

            if self._cancelled:
                self.cancelled.emit()
                return

            answer = "".join(complete_answer).strip()
            if not answer:
                raise EmptyResponseError(
                    self._empty_response_message(event_count, empty_event_count, done_seen, first_event_at)
                )
            self.finished.emit(answer)

        except requests.ConnectionError:
            if not self._cancelled:
                self.failed.emit("Could not connect to Ollama. Make sure Ollama is running.")
        except requests.Timeout:
            if not self._cancelled:
                stage = "before first event" if first_event_at is None else (
                    "before first visible token" if first_visible_token_at is None else "during streaming"
                )
                self.failed.emit(
                    f"Ollama request timed out {stage} after {self._read_timeout_seconds} seconds."
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

    def _empty_response_message(
        self,
        event_count: int,
        empty_event_count: int,
        done_seen: bool,
        first_event_at: float | None,
    ) -> str:
        if first_event_at is None:
            return "Ollama returned no stream events before the response ended."
        return (
            "Ollama completed the stream before sending visible assistant text "
            f"(events={event_count}, empty_events={empty_event_count}, done={done_seen})."
        )


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
