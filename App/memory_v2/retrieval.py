"""Hybrid retrieval and ranking for VioletAI Memory V2.

Retrieval blends exact lexical signals with local deterministic embeddings and
applies strict precision gates so that a query about one attribute never
retrieves a semantically similar but different attribute ("favorite color"
must never match "favorite drink", and an outdated value must not surface as
if it were current).

Only memories that clear the injection threshold are surfaced; otherwise the
pipeline injects nothing, preserving the principle that retrieval must
genuinely improve a response or not happen at all.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from memory_v2.embeddings import cosine_similarity, embed_key_value, embed_text
from memory_v2.models import MemoryLayer, MemoryRecord, RankedMemory, RetrievalOutcome, TemporaryRecord
from memory_v2.normalize import canonical_text, keys_equivalent, subjects_overlap
from memory_v2.store import MemoryStore

STOPWORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "my", "your", "our", "their", "his", "her", "its", "i", "you", "we",
    "they", "he", "she", "it", "me", "us", "them", "do", "does", "did",
    "what", "when", "where", "who", "whom", "whose", "which", "why", "how",
    "of", "in", "on", "at", "to", "for", "and", "or", "but", "not", "no",
    "this", "that", "these", "those", "about", "with", "from", "have", "has",
    "had", "can", "could", "would", "will", "shall", "should", "may", "might",
    "please", "really", "very", "just", "tell", "know", "remember", "saved",
})

_WORD_RE = re.compile(r"[\w']+")

_GENERIC_ATTRIBUTE_MODIFIERS = frozenset({"favorite", "favourite", "fav", "preferred"})

_CURRENT_STATE_QUERY = (
    re.compile(
        r"^(?:what(?:'s| is| was| were)|\bwhat)\s+(?:am|are|was|were|is)\s+(?:i|we)\s+"
        r"(?:working on|doing|playing|up to|currently doing|reading|building)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:what(?:'s| is| are)|\bwhat)\s+my\s+current\s+(?:project|activity|task)\b",
        re.IGNORECASE,
    ),
    re.compile(r"^where did we leave off", re.IGNORECASE),
)

_TEMPORARY_CONTEXT_KEYS = frozenset({"current project", "current activity", "issue", "plan"})


class Retriever:
    def __init__(
        self,
        store: MemoryStore,
        max_results: int = 6,
        injection_threshold: float = 6.0,
        semantic_threshold: float = 0.20,
    ) -> None:
        self.store = store
        self.max_results = max_results
        self.injection_threshold = injection_threshold
        self.semantic_threshold = semantic_threshold

    def retrieve(
        self,
        query: str,
        *,
        include_all: bool = False,
        mark_accessed: bool = True,
        max_results: int | None = None,
    ) -> RetrievalOutcome:
        query = (query or "").strip()
        outcome = RetrievalOutcome(query=query)
        limit = max_results or self.max_results
        if not query:
            outcome.reason = "empty query"
            return outcome

        query_terms = set(_WORD_RE.findall(query.casefold()))
        query_content_terms = query_terms - STOPWORDS
        query_embedding = embed_text(query)

        ranked: list[RankedMemory] = []
        current_state = any(pattern.search(query) for pattern in _CURRENT_STATE_QUERY)
        for record in self.store.list_memories():
            score, reason = self._score_record(record, query, query_content_terms, query_embedding)
            if include_all or score > 0:
                ranked.append(RankedMemory(record=record, score=score, reason=reason, layer=MemoryLayer.DURABLE))
        for record in self.store.list_temporary():
            score, reason = self._score_temporary(
                record, query, query_content_terms, query_embedding, current_state=current_state
            )
            if include_all or score > 0:
                ranked.append(RankedMemory(record=record, score=score, reason=reason, layer=MemoryLayer.TEMPORARY))

        ranked.sort(key=lambda item: (item.score, _timestamp(item.record.updated_at)), reverse=True)
        outcome.candidates = ranked
        selected = [item for item in ranked if item.score >= self.injection_threshold or include_all][:limit]
        outcome.selected = selected
        if include_all:
            outcome.injected = bool(selected)
            outcome.reason = "explicit retrieval request"
        elif selected:
            outcome.injected = True
            outcome.reason = f"best score {selected[0].score:.2f} above threshold {self.injection_threshold}"
        else:
            outcome.reason = f"no memory scored above threshold {self.injection_threshold}"
        if selected and mark_accessed:
            self.store.mark_accessed([item.record.id for item in selected if item.layer == MemoryLayer.DURABLE])
            for item in selected:
                if item.layer == MemoryLayer.TEMPORARY and isinstance(item.record, TemporaryRecord):
                    self.store.touch_temporary(
                        item.record.id,
                        token_counter=item.record.token_at_last_seen,
                        conversation_index=item.record.conversation_at_last_seen,
                    )
        return outcome

    def _score_record(
        self,
        record: MemoryRecord,
        query: str,
        query_content_terms: set[str],
        query_embedding: dict[str, float],
    ) -> tuple[float, str]:
        record_key_terms = set(canonical_text(record.key).split()) - _GENERIC_ATTRIBUTE_MODIFIERS
        query_key_terms = query_content_terms - _GENERIC_ATTRIBUTE_MODIFIERS
        record_subject_terms = set(canonical_text(record.subject).split())
        record_value_terms = set(canonical_text(record.value).split())
        key_overlap = len(query_key_terms & record_key_terms)
        subject_overlap = len(query_content_terms & record_subject_terms)
        value_overlap = len(query_content_terms & record_value_terms)
        exact_key = keys_equivalent(record.key, query)

        score = 0.0
        reasons: list[str] = []
        if exact_key:
            score += 18.0
            reasons.append("exact key")
        if key_overlap:
            score += 6.0 + 1.5 * key_overlap
            reasons.append("key tokens")
        if record.key.casefold() in query.casefold():
            score += 8.0
            reasons.append("key in query")
        if subject_overlap or (record.subject.casefold() in query.casefold()):
            score += 5.0 + 1.0 * subject_overlap
            reasons.append("subject")
        if value_overlap:
            score += 1.5 * value_overlap
            reasons.append("value tokens")

        similarity = cosine_similarity(
            query_embedding,
            embed_key_value(record.key, record.value, record.subject, record.category),
        )
        guard_suppressed = len(query_content_terms) >= 2 and key_overlap == 0 and subject_overlap == 0
        if similarity >= self.semantic_threshold and not guard_suppressed:
            score += 10.0 * similarity
            reasons.append("semantic")

        score += min(record.importance, 10) * 0.35
        score += min(record.access_count, 8) * 0.25
        score += _recency(record.updated_at)
        if not score:
            return 0.0, ""
        return round(score, 3), ", ".join(reasons)

    def _score_temporary(
        self,
        record: TemporaryRecord,
        query: str,
        query_content_terms: set[str],
        query_embedding: dict[str, float],
        *,
        current_state: bool = False,
    ) -> tuple[float, str]:
        record_key_terms = set(canonical_text(record.key).split()) - _GENERIC_ATTRIBUTE_MODIFIERS
        query_key_terms = query_content_terms - _GENERIC_ATTRIBUTE_MODIFIERS
        record_value_terms = set(canonical_text(record.value).split())
        key_overlap = len(query_key_terms & record_key_terms)
        value_overlap = len(query_content_terms & record_value_terms)
        subject_overlap = len(query_content_terms & set(canonical_text(record.subject).split()))

        score = 0.0
        reasons: list[str] = []
        if current_state and record.key in _TEMPORARY_CONTEXT_KEYS:
            score += 9.0
            reasons.append("current-state query")
        if key_overlap:
            score += 8.0 + 1.5 * key_overlap
            reasons.append("key tokens")
        if value_overlap:
            score += 2.0 * value_overlap
            reasons.append("value tokens")
        if subject_overlap:
            score += 3.0
            reasons.append("subject")
        similarity = cosine_similarity(
            query_embedding,
            embed_key_value(record.key, record.value, record.subject, "Temporary"),
        )
        guard_suppressed = len(query_content_terms) >= 2 and key_overlap == 0 and subject_overlap == 0
        if similarity >= self.semantic_threshold and not guard_suppressed:
            score += 8.0 * similarity
            reasons.append("semantic")
        score += min(record.importance, 10) * 0.35
        score += _recency(record.updated_at)
        if not score:
            return 0.0, ""
        return round(score, 3), ", ".join(reasons)


def _timestamp(value: str) -> str:
    return value or ""


def _recency(value: str) -> float:
    now = datetime.now(UTC)
    try:
        updated = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=UTC)
    except ValueError:
        return 0.0
    age_days = max((now - updated).total_seconds() / 86400, 0)
    return round(max(0.0, 2.0 - min(age_days / 30, 2.0)), 3)
