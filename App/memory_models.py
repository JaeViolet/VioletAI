"""Typed records for VioletAI long-term memory."""

from __future__ import annotations

from dataclasses import dataclass


CATEGORIES = ("User", "Preferences", "Projects", "People", "Facts", "Temporary")


@dataclass(slots=True)
class MemoryRecord:
    id: str
    category: str
    subject: str
    key: str
    value: str
    normalized_key: str
    content: str
    importance: int
    confidence: float
    source_conversation_id: str
    source_message_id: str | None
    source_user_text: str
    created_at: str
    updated_at: str
    last_accessed_at: str | None
    access_count: int
    active: bool
    archived_at: str | None
    supersedes_memory_id: str | None
    expires_at: str | None
    language: str
    manually_edited: bool = False


@dataclass(slots=True)
class ParsedMemory:
    category: str
    subject: str
    key: str
    value: str
    content: str
    importance: int = 5
    confidence: float = 0.85
    expires_at: str | None = None
    language: str = "en"
