"""Optional diagnostics for the unified VioletAI memory pipeline."""

from __future__ import annotations

import logging
from datetime import datetime
from logging.handlers import RotatingFileHandler
from time import perf_counter
from typing import Any

from config import MEMORY_LOG_PATH


class MemoryDiagnostics:
    def __init__(self, enabled: bool = False, auto_emit: bool = True) -> None:
        self.enabled = enabled
        self.auto_emit = auto_emit
        self.started_at = perf_counter()
        self.data: dict[str, Any] = {}
        self._finalized = False

    def record(self, **values: Any) -> None:
        if self.enabled:
            self.data.update(values)

    def record_elapsed(self, key: str, started_at: float) -> None:
        self.record(**{key: round((perf_counter() - started_at) * 1000, 3)})

    def emit(self) -> None:
        if not self.auto_emit:
            return
        self.finalize()

    def finalize(
        self,
        assistant_response: str | None = None,
        failed_stage: str | None = None,
        error: str | None = None,
    ) -> None:
        if not self.enabled:
            return
        if self._finalized:
            return
        self._finalized = True
        if assistant_response is not None:
            self.data["assistant_response"] = assistant_response
        if failed_stage is not None:
            self.data["failed_stage"] = failed_stage
        if error is not None:
            self.data["error"] = error
        self.data["total_execution_ms"] = round((perf_counter() - self.started_at) * 1000, 2)
        _logger().info(_format_record(self.data))
        _close_logger()


_LOGGER: logging.Logger | None = None


def _logger() -> logging.Logger:
    global _LOGGER
    if _LOGGER is not None:
        return _LOGGER
    MEMORY_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("violet.memory")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.handlers.clear()
    formatter = logging.Formatter("%(message)s")
    file_handler = RotatingFileHandler(
        MEMORY_LOG_PATH,
        maxBytes=512_000,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    _LOGGER = logger
    return logger


def _close_logger() -> None:
    global _LOGGER
    if _LOGGER is None:
        return
    for handler in list(_LOGGER.handlers):
        handler.close()
        _LOGGER.removeHandler(handler)
    _LOGGER = None


def _format_record(data: dict[str, Any]) -> str:
    divider = "-" * 44
    kind = _record_kind(data)
    lines = [
        divider,
        f"[{_timestamp()}] {kind}",
        "",
        "User",
        _quote(data.get("user_message", "")),
        "",
    ]

    if kind == "Memory":
        lines.extend(["Memory", _memory_summary(data), ""])

    timing_lines = _timing_lines(data)
    if timing_lines:
        lines.extend(timing_lines)
        lines.append("")

    if data.get("error"):
        lines.extend(["Error", _quote(data.get("error")), ""])

    if data.get("post_memory_events"):
        lines.extend(["Post-memory Ollama", *_post_memory_debug_lines(data), ""])

    lines.extend(["Assistant", _quote(data.get("assistant_response", "")), ""])
    lines.append(f"{'Total':<13} {_format_duration(data.get('total_execution_ms'))}")
    lines.append(divider)
    return "\n".join(lines)


def _record_kind(data: dict[str, Any]) -> str:
    action = str(data.get("action") or data.get("operation_executed") or "")
    operation = str(data.get("operation_executed") or "")
    if action and action != "NONE" and operation != "Normal Chat":
        return "Memory"
    return "Chat"


def _timestamp() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def _quote(value: object) -> str:
    text = str(value or "")
    return f'"{text}"'


def _memory_summary(data: dict[str, Any]) -> str:
    action = str(data.get("operation_executed") or data.get("action") or "MEMORY")
    status = str(data.get("structured_result") or data.get("database_result") or "")
    if status and status not in {"SUCCESS", "NORMAL_CHAT"}:
        return f"{action} • FAILED ({status})"
    detail = _memory_detail(data)
    return f"{action} • {detail}" if detail else action


def _memory_detail(data: dict[str, Any]) -> str:
    key = _human_key(data.get("canonical_key") or _candidate_key(data))
    value = data.get("new_value") or data.get("selected_memory_value") or data.get("value") or _candidate_value(data)
    if key and value:
        return f"{key} = {value}"
    if key:
        return key
    if value:
        return str(value)
    return ""


def _candidate_key(data: dict[str, Any]) -> str:
    candidates = data.get("candidate_memories")
    if isinstance(candidates, list) and candidates:
        first = str(candidates[0])
        return first.split("=", 1)[0].strip()
    return ""


def _candidate_value(data: dict[str, Any]) -> str:
    candidates = data.get("candidate_memories")
    if isinstance(candidates, list) and candidates:
        first = str(candidates[0])
        if "=" in first:
            return first.split("=", 1)[1].strip()
    return ""


def _human_key(value: object) -> str:
    return str(value or "").replace("_", " ").strip()


def _timing_lines(data: dict[str, Any]) -> list[str]:
    failed_stage = _stage_label(str(data.get("failed_stage") or ""))
    failure_code = str(data.get("error_code") or data.get("structured_result") or data.get("database_result") or "")
    rows = [
        ("Analysis", ("analysis_ms", "analysis_execution_ms", "analysis_time_ms")),
        ("Retrieve", ("retrieve_ms", "retrieval_ms", "candidate_retrieval_ms")),
        ("Execute", ("execute_ms", "execution_ms", "database_execution_ms")),
        ("Prompt", ("prompt_ms", "prompt_time_ms")),
        ("Ollama Start", ("ollama_start_ms",)),
        ("First Token", ("first_token_ms", "first_token_time_ms")),
        ("Generate", ("generate_ms", "generation_ms")),
        ("Render", ("render_ms", "render_time_ms")),
    ]
    lines: list[str] = []
    for label, keys in rows:
        if failed_stage == label and failure_code != "SUCCESS":
            failed = f"FAILED ({failure_code})" if failure_code else "FAILED"
            lines.append(f"{label:<13} {failed}")
            continue
        duration = _first_present(data, keys)
        if duration is not None:
            lines.append(f"{label:<13} {_format_duration(duration)}")
    return lines


def _stage_label(value: str) -> str:
    mapping = {
        "Memory Analysis": "Analysis",
        "Candidate Retrieval": "Retrieve",
        "Context Resolution": "Retrieve",
        "Validation": "Execute",
        "Memory Pipeline": "Execute",
        "Database Execution": "Execute",
        "Prompt": "Prompt",
        "Ollama Start": "Ollama Start",
        "First Token": "First Token",
        "Generation": "Generate",
        "Generate": "Generate",
        "Render": "Render",
    }
    return mapping.get(value, value)


def _first_present(data: dict[str, Any], keys: tuple[str, ...]) -> object | None:
    for key in keys:
        if key in data:
            return data[key]
    return None


def _format_duration(value: object) -> str:
    try:
        milliseconds = float(value)
    except (TypeError, ValueError):
        return str(value)
    if milliseconds >= 1000:
        return f"{milliseconds / 1000:.2f} s"
    if milliseconds >= 10:
        return f"{milliseconds:.0f} ms"
    return f"{milliseconds:.1f} ms"


def _post_memory_debug_lines(data: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    count = data.get("post_memory_message_count")
    roles = data.get("post_memory_roles")
    if count is not None:
        lines.append(f"Messages      {count} roles={roles}")
    for index, message in enumerate(data.get("post_memory_messages") or [], start=1):
        if not isinstance(message, dict):
            continue
        lines.append(
            f"Prompt {index:<6} {message.get('role', '')} len={message.get('length', 0)} "
            f"preview={_quote(message.get('preview', ''))}"
        )
    for event in data.get("post_memory_events") or []:
        if not isinstance(event, dict):
            continue
        kind = event.get("event")
        if kind == "request_start":
            lines.append(
                f"Request       start messages={event.get('message_count')} "
                f"roles={event.get('roles')} cancelled={event.get('cancellation_requested')}"
            )
            if "think" in event or "options" in event:
                lines.append(f"Options       think={event.get('think')} options={event.get('options')}")
        elif kind == "http_status":
            lines.append(f"HTTP          {event.get('status_code')}")
        elif kind == "ndjson_event":
            continue
        elif kind == "error":
            lines.append(f"Error Source  {event.get('source')} {_quote(event.get('message', ''))}")
        elif kind == "cancel_requested":
            lines.append(f"Cancel        requested={event.get('cancellation_requested')}")
    stream = _post_memory_stream_summary(data.get("post_memory_events") or [])
    if stream:
        lines.append(stream)
    return lines


def _post_memory_stream_summary(events: object) -> str:
    if not isinstance(events, list):
        return ""
    saw_stream = False
    done = False
    content_length = 0
    cancelled = False
    event_count = 0
    for event in events:
        if not isinstance(event, dict) or event.get("event") != "ndjson_event":
            continue
        saw_stream = True
        event_count += 1
        done = bool(event.get("done"))
        cancelled = bool(event.get("cancellation_requested"))
        try:
            content_length = max(content_length, int(event.get("accumulated_content_length") or 0))
        except (TypeError, ValueError):
            pass
    if not saw_stream:
        return ""
    return f"Stream        events={event_count} content_length={content_length} done={done} cancelled={cancelled}"
