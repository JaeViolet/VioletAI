"""Typed records, enums, and outcomes for the VioletAI Memory V2 pipeline.

Memory V2 replaces the legacy explicit-memory service with an invisible,
layered memory system. This module defines the contracts shared across the
pipeline without any application or storage dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MemoryLayer(str, Enum):
    CURRENT = "current"
    TEMPORARY = "temporary"
    DURABLE = "durable"
    ARCHIVED = "archived"


class MemoryCategory(str, Enum):
    USER = "User"
    PREFERENCES = "Preferences"
    PROJECTS = "Projects"
    PEOPLE = "People"
    FACTS = "Facts"


CATEGORIES = tuple(category.value for category in MemoryCategory)


class ProvenanceKind(str, Enum):
    EXPLICIT = "explicit"
    AUTOMATIC = "automatic"
    SUGGESTED = "suggested"
    MANUAL = "manual"


class MutationKind(str, Enum):
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    MERGE = "MERGE"
    SUPERSEDE = "SUPERSEDE"
    ARCHIVE = "ARCHIVE"
    RESTORE = "RESTORE"
    DELETE = "DELETE"
    CLEAR = "CLEAR"
    TEMPORARY_CREATE = "TEMPORARY_CREATE"
    TEMPORARY_UPDATE = "TEMPORARY_UPDATE"


class MutationStatus(str, Enum):
    SUCCESS = "SUCCESS"
    NO_MATCH = "NO_MATCH"
    MULTIPLE_MATCHES = "MULTIPLE_MATCHES"
    INVALID_REFERENCE = "INVALID_REFERENCE"
    INVALID_REQUEST = "INVALID_REQUEST"
    WRITE_FAILED = "WRITE_FAILED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(slots=True)
class Provenance:
    conversation_id: str | None = None
    message_id: str | None = None
    user_text: str = ""
    kind: ProvenanceKind = ProvenanceKind.EXPLICIT


@dataclass(slots=True)
class ParsedMemory:
    category: str
    subject: str
    key: str
    value: str
    content: str = ""
    importance: int = 5
    confidence: float = 0.0
    provenance: Provenance = field(default_factory=Provenance)
    unresolved: bool = False
    language: str = "en"
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MemoryRecord:
    id: str
    layer: MemoryLayer
    category: str
    subject: str
    key: str
    value: str
    canonical_key: str
    content: str
    importance: int
    confidence: float
    provenance: Provenance
    created_at: str
    updated_at: str
    last_accessed_at: str | None = None
    access_count: int = 0
    superseded_by_id: str | None = None
    supersedes_id: str | None = None
    archived_at: str | None = None
    manually_edited: bool = False
    edit_count: int = 0
    language: str = "en"
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def active(self) -> bool:
        return self.layer != MemoryLayer.ARCHIVED

    @property
    def durable(self) -> bool:
        return self.layer == MemoryLayer.DURABLE


@dataclass(slots=True)
class TemporaryRecord:
    id: str
    subject: str
    key: str
    value: str
    canonical_key: str
    content: str
    importance: int
    confidence: float
    provenance: Provenance
    created_at: str
    updated_at: str
    last_accessed_at: str | None = None
    access_count: int = 0
    token_at_created: int = 0
    token_at_last_seen: int = 0
    conversation_at_created: int = 0
    conversation_at_last_seen: int = 0
    unresolved: int = 0
    status: str = "active"
    language: str = "en"
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MutationOutcome:
    ok: bool
    kind: MutationKind
    status: MutationStatus = MutationStatus.SUCCESS
    record: MemoryRecord | TemporaryRecord | None = None
    affected_ids: list[str] = field(default_factory=list)
    previous_value: str = ""
    new_value: str = ""
    clarification: str = ""
    error: str = ""


@dataclass(slots=True)
class RankedMemory:
    record: MemoryRecord | TemporaryRecord
    score: float
    reason: str
    layer: MemoryLayer

    @property
    def is_temporary(self) -> bool:
        return isinstance(self.record, TemporaryRecord)


@dataclass(slots=True)
class RetrievalOutcome:
    query: str
    candidates: list[RankedMemory] = field(default_factory=list)
    selected: list[RankedMemory] = field(default_factory=list)
    injected: bool = False
    reason: str = ""


@dataclass(slots=True)
class MemoryCommand:
    kind: MutationKind
    confidence: float
    key: str = ""
    value: str = ""
    subject: str = ""
    category: str = ""
    reference_previous: bool = False
    raw: str = ""
    reason: str = ""


@dataclass(slots=True)
class TurnAnalysis:
    memory_related: bool = False
    is_question_about_memory: bool = False
    command: MemoryCommand | None = None
    durable_fact: ParsedMemory | None = None
    temporary_fact: ParsedMemory | None = None
    reason: str = ""
    raw: str = ""


@dataclass(slots=True)
class TurnOutcome:
    memory_related: bool
    messages: list[dict[str, str]]
    notice: str = ""
    action: MutationKind | None = None
    action_status: MutationStatus | None = None
    retrieval: RetrievalOutcome | None = None
    clarification_needed: bool = False
    diagnostics: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)
