"""Structured memory intent classification.

The classifier is deliberately separated from execution. It can use a local
LLM-facing adapter later, while the deterministic fallback keeps behavior safe
and testable when Ollama is unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from time import perf_counter

import requests

from config import AUTOMATIC_MEMORY_CLASSIFIER_TIMEOUT_SECONDS, DEFAULT_MODEL_NAME, OLLAMA_URL
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


@dataclass(slots=True)
class DurableMemoryDecision:
    is_user_fact: bool = False
    durability_class: str = "NONE"
    durability_confidence: float = 0.0
    extracted_fact: str = ""
    category: str = ""
    subject: str = "user"
    canonical_key: str = ""
    value: str = ""
    save_automatically: bool = False
    rejection_reason: str = "LOW_CONFIDENCE"
    classifier_source: str = "FALLBACK"
    classifier_latency_ms: float = 0.0
    error: str = ""


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

    def classify_durable_memory(self, text: str) -> DurableMemoryDecision:
        started = perf_counter()
        prompt = (
            "Decide if this user message contains a durable personal fact worth storing in long-term memory. "
            "Return only strict JSON with keys: is_user_fact boolean, durability_class string, "
            "durability_confidence number 0-1, extracted_fact string, category string, subject string, "
            "canonical_key string, value string, save_automatically boolean, rejection_reason string. "
            "Durable classes: IDENTITY, PREFERENCE, POSSESSION, RELATIONSHIP, LONG_TERM_INTEREST, SKILL, "
            "LONG_TERM_PROJECT, LOCATION. Reject classes: TEMPORARY_STATE, CURRENT_ACTIVITY, SHORT_TERM_PLAN, "
            "CASUAL_STATEMENT, NONE. Store only durable facts likely useful for weeks or months. "
            "Do not save current activities like playing a game right now, drinking water, brushing teeth, "
            "temporary feelings, short-term plans, greetings, or casual opinions. "
            "Use categories only: User, Preferences, Projects, People, Facts, Temporary. "
            "Examples: 'My favorite color is violet' => PREFERENCE favorite_color violet save true. "
            "'I am playing Archero 2' => CURRENT_ACTIVITY save false. "
            "'I play Archero 2 regularly' => LONG_TERM_INTEREST save true. "
            f"User message: {text}"
        )
        if not self.use_llm:
            return self._fallback_durable_decision(text, started)
        try:
            response = requests.post(
                OLLAMA_URL,
                json={
                    "model": self.model,
                    "stream": False,
                    "messages": [
                        {"role": "system", "content": "You classify durable long-term memory facts and output only strict JSON."},
                        {"role": "user", "content": prompt},
                    ],
                    "options": {"temperature": 0, "num_predict": 180},
                    "think": False,
                },
                timeout=AUTOMATIC_MEMORY_CLASSIFIER_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            content = response.json().get("message", {}).get("content", "")
            data = json.loads(_extract_json(content))
            decision = _decision_from_data(data)
            decision.classifier_source = "LLM"
            decision.classifier_latency_ms = round((perf_counter() - started) * 1000, 3)
            return decision
        except requests.Timeout:
            return DurableMemoryDecision(
                durability_class="CLASSIFIER_FAILED",
                rejection_reason="CLASSIFIER_FAILED",
                classifier_source="FALLBACK",
                classifier_latency_ms=round((perf_counter() - started) * 1000, 3),
                error="timeout",
            )
        except Exception as error:
            return DurableMemoryDecision(
                durability_class="CLASSIFIER_FAILED",
                rejection_reason="CLASSIFIER_FAILED",
                classifier_source="FALLBACK",
                classifier_latency_ms=round((perf_counter() - started) * 1000, 3),
                error=str(error),
            )

    def _fallback_durable_decision(self, text: str, started: float) -> DurableMemoryDecision:
        body = _extract_create_body(text)
        key, value = _extract_key_value(body or text)
        if key and value:
            category = _category_for_key(key)
            return DurableMemoryDecision(
                is_user_fact=True,
                durability_class=_class_for_key(key),
                durability_confidence=0.9,
                extracted_fact=body or text,
                category=category,
                subject="user",
                canonical_key=canonical_key(key),
                value=value,
                save_automatically=True,
                rejection_reason="",
                classifier_source="DETERMINISTIC",
                classifier_latency_ms=round((perf_counter() - started) * 1000, 3),
            )
        return DurableMemoryDecision(
            is_user_fact=False,
            durability_class=_fallback_rejection_class(text),
            durability_confidence=0.9,
            rejection_reason=_fallback_rejection_class(text),
            classifier_source="DETERMINISTIC",
            classifier_latency_ms=round((perf_counter() - started) * 1000, 3),
        )


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
    match = re.search(r"\bi own an? (?P<value>iphone|android phone|phone|ipad|tablet|macbook|laptop|desktop|pc|computer)$", cleaned, flags=re.IGNORECASE)
    if match:
        return "device", _normalize_device_value(match.group("value"))
    match = re.search(r"\b(?:i live in|i am from|i'?m from) (?P<value>.+)$", cleaned, flags=re.IGNORECASE)
    if match:
        return "location", _clean_value(match.group("value"))
    match = re.search(r"\bi(?:'m| am) left[- ]handed$", cleaned, flags=re.IGNORECASE)
    if match:
        return "handedness", "left-handed"
    match = re.search(r"\bi love (?P<value>.+)$", cleaned, flags=re.IGNORECASE)
    if match:
        return "interest", _clean_value(match.group("value"))
    match = re.search(r"\bi(?:'m| am) learning (?P<value>.+)$", cleaned, flags=re.IGNORECASE)
    if match:
        return "learning", _clean_value(match.group("value"))
    match = re.search(r"\bi(?:'m| am) building (?P<value>.+)$", cleaned, flags=re.IGNORECASE)
    if match:
        return "project", _clean_value(match.group("value"))
    match = re.search(r"\bi play (?P<value>.+?) regularly$", cleaned, flags=re.IGNORECASE)
    if match:
        return "interest", _clean_value(match.group("value"))
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


def _decision_from_data(data: object) -> DurableMemoryDecision:
    if not isinstance(data, dict):
        raise ValueError("classifier output was not an object")
    return DurableMemoryDecision(
        is_user_fact=bool(data.get("is_user_fact")),
        durability_class=str(data.get("durability_class") or "NONE").upper(),
        durability_confidence=float(data.get("durability_confidence") or 0.0),
        extracted_fact=str(data.get("extracted_fact") or ""),
        category=str(data.get("category") or ""),
        subject=str(data.get("subject") or "user"),
        canonical_key=canonical_key(str(data.get("canonical_key") or "")),
        value=_clean_value(str(data.get("value") or "")),
        save_automatically=bool(data.get("save_automatically")),
        rejection_reason=str(data.get("rejection_reason") or ""),
    )


def _category_for_key(key: str) -> str:
    normalized = canonical_key(key)
    if normalized in {"favorite_color", "favorite_movie", "favorite_drink", "preference"}:
        return "Preferences"
    if normalized in {"project"}:
        return "Projects"
    return "User"


def _class_for_key(key: str) -> str:
    normalized = canonical_key(key)
    if normalized.startswith("favorite") or normalized in {"interest"}:
        return "PREFERENCE"
    if normalized == "device":
        return "POSSESSION"
    if normalized == "location":
        return "LOCATION"
    if normalized == "project":
        return "LONG_TERM_PROJECT"
    if normalized == "learning":
        return "SKILL"
    return "IDENTITY"


def _fallback_rejection_class(text: str) -> str:
    lowered = text.casefold()
    if re.search(r"\bi(?:'m| am)\s+(playing|drinking|brushing)\b", lowered):
        return "CURRENT_ACTIVITY"
    if re.search(r"\bi(?:'m| am)\s+(tired|hungry)\b", lowered):
        return "TEMPORARY_STATE"
    if re.search(r"\b(i(?:'m| am) going to|i'll be back)\b", lowered):
        return "SHORT_TERM_PLAN"
    if lowered.strip(" .!?") in {"hello", "hi", "how are you", "how are you today"}:
        return "CASUAL_STATEMENT"
    return "LOW_CONFIDENCE"


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
