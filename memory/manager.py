"""Memory interface, local SQLite backend, and app-facing facade.

This module only defines the memory *contract* and storage mechanics. It
contains no retrieval logic, extraction pipeline, or decision-making.
The application talks to MemoryManager, which delegates to an injectable
MemoryBackend so the local implementation can later be swapped for a
remote memory service (e.g. Letta) without touching the UI.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator, Protocol

from core.config import MEMORY_DB_PATH

CATEGORIES = ["User", "Preferences", "Projects", "People", "Facts"]

DURABLE = "durable"
ARCHIVED = "archived"


class MemoryStoreError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(slots=True)
class MemoryRecord:
    id: str
    category: str
    subject: str
    key: str
    value: str
    content: str
    created_at: str
    updated_at: str
    layer: str = DURABLE
    importance: int = 5
    confidence: float = 0.0
    last_accessed_at: str | None = None
    access_count: int = 0
    source_user_text: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def active(self) -> bool:
        return self.layer != ARCHIVED


class MemoryBackend(Protocol):
    """Contract any memory backend (local or remote) must satisfy."""

    def save(self, record: MemoryRecord) -> MemoryRecord:
        """Insert or update a memory record, returning the stored record."""

    def get(self, memory_id: str) -> MemoryRecord | None:
        """Return the record for memory_id, or None."""

    def search(
        self,
        query: str = "",
        category: str | None = None,
        include_archived: bool = False,
        limit: int = 100,
    ) -> list[MemoryRecord]:
        """Return matching records, newest first."""

    def delete(self, memory_id: str) -> bool:
        """Delete the record; return True when it existed."""

    def archive(self, memory_id: str) -> MemoryRecord | None:
        """Move a durable record to archived; return the stored record or None."""

    def restore(self, memory_id: str) -> MemoryRecord | None:
        """Move an archived record back to durable; return the stored record or None."""

    def clear(self) -> None:
        """Delete all durable records."""


class LocalMemoryBackend:
    """SQLite storage for memory records. Storage only, no intelligence."""

    def __init__(self, path: Path = MEMORY_DB_PATH) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(str(self.path))
        try:
            connection.row_factory = sqlite3.Row
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _ensure_schema(self) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    layer TEXT NOT NULL DEFAULT 'durable',
                    category TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    content TEXT NOT NULL,
                    importance INTEGER NOT NULL DEFAULT 5,
                    confidence REAL NOT NULL DEFAULT 0.0,
                    source_user_text TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_accessed_at TEXT,
                    access_count INTEGER NOT NULL DEFAULT 0,
                    extra TEXT NOT NULL DEFAULT '{}'
                )
                """
            )

    def save(self, record: MemoryRecord) -> MemoryRecord:
        existing = self.get(record.id)
        if existing is None:
            with self.connect() as connection:
                connection.execute(
                    """
                    INSERT INTO memories (
                        id, layer, category, subject, key, value, content,
                        importance, confidence, source_user_text, created_at, updated_at,
                        last_accessed_at, access_count, extra
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.id,
                        record.layer,
                        record.category,
                        record.subject,
                        record.key,
                        record.value,
                        record.content or f"{record.key}: {record.value}",
                        record.importance,
                        record.confidence,
                        record.source_user_text,
                        record.created_at,
                        record.updated_at,
                        record.last_accessed_at,
                        record.access_count,
                        json.dumps(record.extra),
                    ),
                )
        else:
            with self.connect() as connection:
                connection.execute(
                    """
                    UPDATE memories
                    SET layer=?, category=?, subject=?, key=?, value=?, content=?,
                        importance=?, confidence=?, source_user_text=?, updated_at=?,
                        last_accessed_at=?, access_count=?, extra=?
                    WHERE id=?
                    """,
                    (
                        record.layer,
                        record.category,
                        record.subject,
                        record.key,
                        record.value,
                        record.content,
                        record.importance,
                        record.confidence,
                        record.source_user_text,
                        utc_now(),
                        record.last_accessed_at,
                        record.access_count,
                        json.dumps(record.extra),
                        record.id,
                    ),
                )
        stored = self.get(record.id)
        if stored is None:
            raise MemoryStoreError("memory save failed")
        return stored

    def get(self, memory_id: str) -> MemoryRecord | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
            return self._row_to_memory(row) if row else None

    def search(
        self,
        query: str = "",
        category: str | None = None,
        include_archived: bool = False,
        limit: int = 100,
    ) -> list[MemoryRecord]:
        clauses: list[str] = []
        params: list[str] = []
        if not include_archived:
            clauses.append("layer='durable'")
        if category and category != "All":
            clauses.append("category=?")
            params.append(category)
        if query:
            like = f"%{query.casefold()}%"
            clauses.append(
                "(lower(subject) LIKE ? OR lower(key) LIKE ? OR lower(value) LIKE ? OR lower(content) LIKE ? OR lower(source_user_text) LIKE ?)"
            )
            params.extend([like] * 5)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        with self.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM memories {where} ORDER BY updated_at DESC LIMIT ?",
                [*params, limit],
            ).fetchall()
            return [self._row_to_memory(row) for row in rows]

    def delete(self, memory_id: str) -> bool:
        with self.connect() as connection:
            cursor = connection.execute("DELETE FROM memories WHERE id=?", (memory_id,))
            return cursor.rowcount > 0

    def archive(self, memory_id: str) -> MemoryRecord | None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE memories SET layer='archived', updated_at=? WHERE id=? AND layer='durable'",
                (utc_now(), memory_id),
            )
        return self.get(memory_id)

    def restore(self, memory_id: str) -> MemoryRecord | None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE memories SET layer='durable', updated_at=? WHERE id=? AND layer='archived'",
                (utc_now(), memory_id),
            )
        return self.get(memory_id)

    def clear(self) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM memories WHERE layer='durable'")

    def _row_to_memory(self, row: sqlite3.Row) -> MemoryRecord:
        return MemoryRecord(
            id=row["id"],
            layer=row["layer"],
            category=row["category"],
            subject=row["subject"],
            key=row["key"],
            value=row["value"],
            content=row["content"],
            importance=row["importance"],
            confidence=row["confidence"],
            source_user_text=row["source_user_text"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_accessed_at=row["last_accessed_at"],
            access_count=row["access_count"],
            extra=json.loads(row["extra"] or "{}"),
        )


class MemoryManager:
    """Thin application-facing facade over a memory backend.

    The backend is injectable so the local implementation can be swapped
    for a remote memory service (e.g. Letta) without touching the UI.
    """

    def __init__(self, backend: MemoryBackend) -> None:
        self._backend = backend

    def save(self, record: MemoryRecord) -> MemoryRecord:
        return self._backend.save(record)

    def get(self, memory_id: str) -> MemoryRecord | None:
        return self._backend.get(memory_id)

    def search(
        self,
        query: str = "",
        category: str | None = None,
        include_archived: bool = False,
        limit: int = 100,
    ) -> list[MemoryRecord]:
        return self._backend.search(query, category, include_archived, limit)

    def delete(self, memory_id: str) -> bool:
        return self._backend.delete(memory_id)

    def archive(self, memory_id: str) -> MemoryRecord | None:
        return self._backend.archive(memory_id)

    def restore(self, memory_id: str) -> MemoryRecord | None:
        return self._backend.restore(memory_id)

    def clear(self) -> None:
        self._backend.clear()
