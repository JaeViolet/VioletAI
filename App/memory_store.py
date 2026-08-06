"""Minimal persistent store backing the Memory Manager settings tab.

Scaffolding for a future memory update: durable memory records live in a
SQLite database so the Memory tab can list, search, edit, archive, and delete
them. No extraction, pipeline, or retrieval logic lives here.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from config import MEMORY_DB_PATH

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


class MemoryStore:
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

    def insert_memory(
        self,
        *,
        category: str,
        subject: str,
        key: str,
        value: str,
        content: str = "",
        importance: int = 5,
        confidence: float = 0.0,
        source_user_text: str = "",
    ) -> MemoryRecord:
        now = utc_now()
        record_id = uuid.uuid4().hex
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO memories (
                    id, layer, category, subject, key, value, content,
                    importance, confidence, source_user_text, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    DURABLE,
                    category,
                    subject,
                    key,
                    value,
                    content or f"{key}: {value}",
                    importance,
                    confidence,
                    source_user_text,
                    now,
                    now,
                ),
            )
        record = self.get_memory(record_id)
        if record is None:
            raise MemoryStoreError("memory insert failed")
        return record

    def get_memory(self, memory_id: str) -> MemoryRecord | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
            return self._row_to_memory(row) if row else None

    def list_memories(self, include_archived: bool = False) -> list[MemoryRecord]:
        where = "" if include_archived else "WHERE layer='durable'"
        with self.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM memories {where} ORDER BY updated_at DESC"
            ).fetchall()
            return [self._row_to_memory(row) for row in rows]

    def search_memories(
        self,
        query: str = "",
        category: str = "",
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

    def update_memory(
        self,
        memory_id: str,
        *,
        category: str | None = None,
        subject: str | None = None,
        key: str | None = None,
        value: str | None = None,
        content: str | None = None,
        manual: bool = False,
    ) -> MemoryRecord | None:
        existing = self.get_memory(memory_id)
        if existing is None:
            return None
        new_category = category if category is not None else existing.category
        new_subject = subject if subject is not None else existing.subject
        new_key = key if key is not None else existing.key
        new_value = value if value is not None else existing.value
        new_content = content if content is not None else existing.content
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE memories
                SET category=?, subject=?, key=?, value=?, content=?, updated_at=?
                WHERE id=?
                """,
                (new_category, new_subject, new_key, new_value, new_content, utc_now(), memory_id),
            )
        return self.get_memory(memory_id)

    def archive_memory(self, memory_id: str) -> MemoryRecord | None:
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                "UPDATE memories SET layer='archived', updated_at=? WHERE id=? AND layer='durable'",
                (now, memory_id),
            )
        return self.get_memory(memory_id)

    def restore_memory(self, memory_id: str) -> MemoryRecord | None:
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                "UPDATE memories SET layer='durable', updated_at=? WHERE id=? AND layer='archived'",
                (now, memory_id),
            )
        return self.get_memory(memory_id)

    def delete_memory(self, memory_id: str) -> bool:
        with self.connect() as connection:
            cursor = connection.execute("DELETE FROM memories WHERE id=?", (memory_id,))
            return cursor.rowcount > 0

    def clear_durable(self) -> None:
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
