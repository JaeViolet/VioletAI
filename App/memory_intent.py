"""Structured memory intent classification.

The classifier is deliberately separated from execution. It can use a local
LLM-facing adapter later, while the deterministic fallback keeps behavior safe
and testable when Ollama is unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass
import json

import requests

from config import DEFAULT_MODEL_NAME, OLLAMA_URL


CREATE = "CREATE"
UPDATE = "UPDATE"
DELETE = "DELETE"
RETRIEVE = "RETRIEVE"
IGNORE = "IGNORE"


@dataclass(slots=True)
class MemoryIntent:
    action: str
    confidence: float = 0.0
    reason: str = ""


class MemoryIntentClassifier:
    """Small classifier facade for memory intent routing.

    This keeps LLM-assisted classification pluggable without letting the LLM
    execute memory operations or claim success.
    """

    def __init__(self, use_llm: bool = True, model: str = DEFAULT_MODEL_NAME, timeout_seconds: float = 1.5) -> None:
        self.use_llm = use_llm
        self.model = model
        self.timeout_seconds = timeout_seconds

    def classify(self, text: str, previous_user_text: str | None = None) -> MemoryIntent:
        lowered = text.casefold().strip()
        if any(pattern in lowered for pattern in ("what do you remember", "what memories do you have")):
            return MemoryIntent(RETRIEVE, 0.95, "memory retrieval request")
        if lowered.strip(" .!?") in {
            "remember that",
            "remember this",
            "save that",
            "save this",
            "store that",
            "store this",
        }:
            return MemoryIntent(CREATE, 0.9, "contextual create request")
        if lowered.strip(" .!?") in {
            "forget that",
            "forget this",
            "delete that",
            "delete this",
            "remove that",
            "remove this",
        }:
            return MemoryIntent(DELETE, 0.9, "contextual delete request")
        if lowered.startswith((
            "remember",
            "please remember",
            "save",
            "store",
            "note that",
            "keep in mind",
            "create a memory",
            "add this to",
        )):
            return MemoryIntent(CREATE, 0.9, "explicit create request")
        if lowered.startswith("forget"):
            return MemoryIntent(DELETE, 0.9, "explicit delete request")
        if lowered.startswith(("delete", "remove", "erase", "clear")) and "memory" in lowered:
            return MemoryIntent(DELETE, 0.9, "explicit delete request")
        if lowered.startswith(("update", "change", "replace", "edit")):
            return MemoryIntent(UPDATE, 0.9, "explicit update request")
        if lowered.startswith("my ") and (" is now " in lowered or lowered.endswith(" now." ) or lowered.endswith(" now")):
            return MemoryIntent(UPDATE, 0.85, "natural language update request")
        if lowered.startswith("in your memory it says"):
            return MemoryIntent(UPDATE, 0.9, "saved-memory correction request")
        if self.use_llm and self._looks_memory_related(lowered):
            llm_intent = self._classify_with_local_llm(text, previous_user_text)
            if llm_intent is not None:
                return llm_intent
        return MemoryIntent(IGNORE, 0.0, "no explicit memory intent")

    def _looks_memory_related(self, lowered: str) -> bool:
        markers = ("memory", "remember", "saved", "forget", "delete", "remove", "change", "update", "save", "store")
        return any(marker in lowered for marker in markers)

    def _classify_with_local_llm(self, text: str, previous_user_text: str | None) -> MemoryIntent | None:
        prompt = (
            "Classify the user's message for a local memory system. "
            "Return only JSON with action CREATE, UPDATE, DELETE, RETRIEVE, or IGNORE, "
            "and confidence from 0 to 1. Do not claim any operation succeeded.\n"
            f"Previous user message: {previous_user_text or ''}\n"
            f"Current user message: {text}"
        )
        try:
            response = requests.post(
                OLLAMA_URL,
                json={
                    "model": self.model,
                    "stream": False,
                    "messages": [
                        {"role": "system", "content": "You classify memory intent and output strict JSON."},
                        {"role": "user", "content": prompt},
                    ],
                    "options": {"temperature": 0},
                },
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            content = response.json().get("message", {}).get("content", "")
            data = json.loads(_extract_json(content))
            action = str(data.get("action", "")).upper()
            confidence = float(data.get("confidence", 0.0))
        except Exception:
            return None
        if action not in {CREATE, UPDATE, DELETE, RETRIEVE, IGNORE} or confidence < 0.75:
            return None
        return MemoryIntent(action, confidence, "local LLM classification")


def _extract_json(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return text
    return text[start:end + 1]
