"""Structured memory intent classification.

The classifier is deliberately separated from execution. It can use a local
LLM-facing adapter later, while the deterministic fallback keeps behavior safe
and testable when Ollama is unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re

import requests

from config import DEFAULT_MODEL_NAME, OLLAMA_URL
from memory_embeddings import canonical_key


CREATE = "CREATE"
UPDATE = "UPDATE"
DELETE = "DELETE"
RETRIEVE = "RETRIEVE"
IGNORE = "IGNORE"
NONE = "NONE"
TRAILING_EMOTICON = re.compile(r"\s*(?:[:;=8xX][-']?[)(DPpOo/\\]|[🙂😊😉😄😃😂🤣😅🥲]+)\s*$")


@dataclass(slots=True)
class MemoryIntent:
    action: str
    confidence: float = 0.0
    reason: str = ""


@dataclass(slots=True)
class MemoryAnalysis:
    memory_related: bool
    action: str
    confidence: float
    subject: str = ""
    canonical_key: str = ""
    value: str = ""
    referenced_previous_user_message: str | None = None
    diagnostic_reasoning: str = ""
    original_text: str = ""


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
        analysis = self.analyze(text, previous_user_text)
        return MemoryIntent(analysis.action if analysis.memory_related else IGNORE, analysis.confidence, analysis.diagnostic_reasoning)

    def analyze(self, text: str, previous_user_text: str | None = None) -> MemoryAnalysis:
        lowered = text.casefold().strip()
        if any(pattern in lowered for pattern in ("what do you remember", "what memories do you have")):
            return self._analysis(text, RETRIEVE, 0.95, reason="memory retrieval request")
        if re.search(r"\bwhat\b.+\b(color|colour).+\b(like|favorite|favourite|prefer)", lowered):
            return self._analysis(text, RETRIEVE, 0.88, key="favorite color", reason="semantic memory retrieval request")
        if lowered.strip(" .!?") in {
            "remember that",
            "remember this",
            "save that",
            "save this",
            "store that",
            "store this",
        }:
            return self._analysis(text, CREATE, 0.9, referenced=previous_user_text, reason="contextual create request")
        if lowered.strip(" .!?") in {
            "forget that",
            "forget this",
            "delete that",
            "delete this",
            "remove that",
            "remove this",
        }:
            return self._analysis(text, DELETE, 0.9, referenced=previous_user_text, reason="contextual delete request")
        if re.search(r"\b(remember|save|store|note that|keep in mind|create a memory|add this to(?: your)? memory)\b", lowered):
            body = _extract_create_body(text)
            key, value = _extract_key_value(body or text)
            return self._analysis(text, CREATE, 0.9, key=key, value=value, reason="explicit create request")
        if lowered.startswith("forget") or (re.search(r"\b(delete|remove|erase|clear)\b", lowered) and "memory" in lowered):
            key = _extract_delete_key(text)
            return self._analysis(text, DELETE, 0.9, key=key, reason="explicit delete request")
        if lowered.startswith(("update", "change", "replace", "edit")) or lowered.startswith("in your memory it says"):
            key, value = _extract_update_key_value(text)
            return self._analysis(text, UPDATE, 0.9, key=key, value=value, reason="explicit update request")
        if re.search(r"\b(color|colour).+\b(like|favorite|favourite|prefer).+\bnow\b", lowered):
            key, value = "favorite color", _extract_now_value(text)
            return self._analysis(text, UPDATE, 0.86, key=key, value=value, reason="natural language update request")
        if lowered.startswith("my ") and (" is now " in lowered or lowered.endswith(" now." ) or lowered.endswith(" now")):
            key, value = _extract_update_key_value(text)
            return self._analysis(text, UPDATE, 0.85, key=key, value=value, reason="natural language update request")
        if lowered.strip(" .!?") in {"change it to red", "change it to blue", "change it to green"} or re.match(r"^change it to .+", lowered):
            return self._analysis(text, UPDATE, 0.78, key="it", value=re.sub(r"^change it to\s+", "", text, flags=re.IGNORECASE).strip(" ."), reason="contextual update request")
        if self.use_llm and self._looks_memory_related(lowered):
            llm_analysis = self._classify_with_local_llm(text, previous_user_text)
            if llm_analysis is not None:
                return llm_analysis
        return MemoryAnalysis(False, NONE, 0.0, diagnostic_reasoning="no explicit memory intent", original_text=text)

    def _analysis(
        self,
        text: str,
        action: str,
        confidence: float,
        key: str = "",
        value: str = "",
        referenced: str | None = None,
        reason: str = "",
    ) -> MemoryAnalysis:
        return MemoryAnalysis(
            True,
            action,
            confidence,
            subject="user",
            canonical_key=canonical_key(key),
            value=value,
            referenced_previous_user_message=referenced,
            diagnostic_reasoning=reason,
            original_text=text,
        )

    def _looks_memory_related(self, lowered: str) -> bool:
        markers = ("memory", "remember", "saved", "forget", "delete", "remove", "change", "update", "save", "store")
        return any(marker in lowered for marker in markers)

    def _classify_with_local_llm(self, text: str, previous_user_text: str | None) -> MemoryAnalysis | None:
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
            key = str(data.get("canonical_key") or data.get("key") or "")
            value = str(data.get("value") or "")
        except Exception:
            return None
        if action not in {CREATE, UPDATE, DELETE, RETRIEVE, IGNORE, NONE} or confidence < 0.75:
            return None
        if action in {IGNORE, NONE}:
            return MemoryAnalysis(False, NONE, confidence, diagnostic_reasoning="local LLM classification", original_text=text)
        return self._analysis(text, action, confidence, key=key, value=value, referenced=previous_user_text, reason="local LLM classification")


def _extract_json(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return text
    return text[start:end + 1]


def _extract_create_body(text: str) -> str:
    return re.sub(
        r"^.*?\b(?:(?:can|could|would|will)\s+you\s+)?(?:please\s+)?(?:remember(?: that)?|save(?: that)?|store(?: that)?|note that|keep in mind|create a memory|add this to(?: your)? memory)\b[:\s]*",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip(" .")


def _extract_key_value(text: str) -> tuple[str, str]:
    cleaned = text.strip(" .")
    match = re.search(r"\bmy (?P<key>.+?) is (?P<value>.+)$", cleaned, flags=re.IGNORECASE)
    if match:
        return match.group("key").strip(), _clean_value(match.group("value"))
    match = re.search(r"\bi have an? (?P<value>iphone|android phone|phone|ipad|tablet|macbook|laptop|desktop|pc|computer)$", cleaned, flags=re.IGNORECASE)
    if match:
        return "device", _normalize_device_value(match.group("value"))
    match = re.search(r"\b(?:i live in|i am from|i'?m from) (?P<value>.+)$", cleaned, flags=re.IGNORECASE)
    if match:
        return "location", _clean_value(match.group("value"))
    match = re.search(r"\bthe (?P<key>color|colour) i like most is (?P<value>.+?)(?: now)?$", cleaned, flags=re.IGNORECASE)
    if match:
        return "favorite color", _clean_value(match.group("value"))
    return "", ""


def _extract_update_key_value(text: str) -> tuple[str, str]:
    cleaned = text.strip(" .")
    patterns = [
        r"^(?:update|change|edit) my (?P<key>.+?) to (?P<value>.+)$",
        r"^replace (?:the |my )?(?P<key>.+?)(?: you have saved)?(?: with| to)? (?P<value>.+)$",
        r"^my (?P<key>.+?) is now (?P<value>.+)$",
        r"^my (?P<key>.+?) is (?P<value>.+?) now$",
        r"^in your memory it says (?:(?:my )?(?P<key>.+?) is |)(?P<old>.+?)[;,]?\s*change it to (?P<value>.+)$",
    ]
    for pattern in patterns:
        match = re.match(pattern, cleaned, flags=re.IGNORECASE)
        if match:
            key = (match.groupdict().get("key") or match.groupdict().get("old") or "").strip()
            return key, _clean_value(match.group("value"))
    return _extract_key_value(cleaned)


def _extract_delete_key(text: str) -> str:
    cleaned = re.sub(r"\b(delete|remove|erase|clear|forget)\b", "", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(the )?memory (about|of)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(from|in)\s+(long[- ]term\s+)?memory\b", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip(" .")


def _extract_now_value(text: str) -> str:
    match = re.search(r"\bis (?P<value>.+?) now\b", text, flags=re.IGNORECASE)
    return _clean_value(match.group("value")) if match else ""


def _clean_value(value: str) -> str:
    cleaned = " ".join(str(value or "").strip().split())
    while True:
        previous = cleaned
        cleaned = TRAILING_EMOTICON.sub("", cleaned).strip()
        cleaned = cleaned.rstrip(" \t\r\n.!?;:")
        if cleaned == previous:
            break
    return cleaned


def _normalize_device_value(value: str) -> str:
    cleaned = _clean_value(value)
    normalized = cleaned.casefold()
    replacements = {
        "iphone": "iPhone",
        "ipad": "iPad",
        "macbook": "MacBook",
        "pc": "PC",
    }
    return replacements.get(normalized, cleaned)
