"""Optional diagnostics for the unified VioletAI memory pipeline."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from time import perf_counter
from typing import Any

from config import MEMORY_LOG_PATH


class MemoryDiagnostics:
    def __init__(self, enabled: bool = False) -> None:
        self.enabled = enabled
        self.started_at = perf_counter()
        self.data: dict[str, Any] = {}

    def record(self, **values: Any) -> None:
        if self.enabled:
            self.data.update(values)

    def emit(self) -> None:
        if not self.enabled:
            return
        self.data["total_execution_ms"] = round((perf_counter() - self.started_at) * 1000, 2)
        _logger().info(_format_record(self.data))


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


def _format_record(data: dict[str, Any]) -> str:
    lines = [
        "=" * 50,
        "",
        "[Memory Diagnostics]",
        "",
        "Stage 1 - Memory Analysis",
        "",
    ]
    labels = [
        ("timestamp", "Timestamp"),
        ("user_message", "User Message"),
        ("memory_related", "Memory Related"),
        ("action", "Action"),
        ("confidence", "Confidence"),
        ("diagnostic_reasoning", "Reasoning"),
        ("subject", "Subject"),
        ("canonical_key", "Canonical Key"),
        ("value", "Value"),
        ("referenced_previous_user_message", "Referenced Previous User Message"),
        ("stage_2", "Stage 2 - Candidate Retrieval"),
        ("candidate_memories", "Candidates"),
        ("ranking_scores", "Ranking Scores"),
        ("selected_memory", "Selected"),
        ("stage_3", "Stage 3 - Validation"),
        ("memory_mode", "Memory Mode"),
        ("validation_result", "Validation Result"),
        ("stage_4", "Stage 4 - Database Execution"),
        ("operation_executed", "Operation Executed"),
        ("database_result", "Database"),
        ("stage_5", "Stage 5 - Structured Result"),
        ("structured_result", "Structured Result"),
        ("stage_6", "Stage 6 - UI"),
        ("ui_confirmation", "UI Confirmation"),
        ("stage_7", "Stage 7 - Assistant"),
        ("assistant_response", "Assistant Response"),
        ("failed_stage", "Failed Stage"),
        ("failure_reason", "Failure Reason"),
        ("total_execution_ms", "Total Time Ms"),
    ]
    for key, label in labels:
        if key in data:
            lines.extend([f"{label}:", str(data[key]), ""])
    lines.append("=" * 50)
    return "\n".join(lines)
