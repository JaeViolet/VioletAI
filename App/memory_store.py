"""SQLite persistence for explicit long-term memories."""

from __future__ import annotations

import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

from config import MEMORY_DB_PATH
from memory_models import CATEGORIES, MemoryRecord, ParsedMemory

SCHEMA_VERSION = 1
CATEGORY_ALIASES = {
    "profile": "User",
    "user": "User",
    "preference": "Preferences",
    "preferences": "Preferences",
    "project": "Projects",
    "projects": "Projects",
    "person": "People",
    "people": "People",
    "fact": "Facts",
    "facts": "Facts",
    "temporary": "Temporary",
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class MemoryStoreError(RuntimeError):
    pass


class MemoryStore:
    def __init__(self, path: Path = MEMORY_DB_PATH) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        try:
            connection = sqlite3.connect(self.path)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            yield connection
            connection.commit()
        except sqlite3.DatabaseError as error:
            raise MemoryStoreError(str(error)) from error
        finally:
            try:
                connection.close()
            except Exception:
                pass

    def _ensure_schema(self) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            current = connection.execute(
                "SELECT value FROM schema_meta WHERE key = 'version'"
            ).fetchone()
            if current is not None and int(current["value"]) > SCHEMA_VERSION:
                raise MemoryStoreError("Memory database schema is newer than this app.")
            if current is not None and int(current["value"]) < SCHEMA_VERSION:
                self._backup_before_migration()
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    category TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    normalized_key TEXT NOT NULL,
                    content TEXT NOT NULL,
                    importance INTEGER NOT NULL DEFAULT 5,
                    confidence REAL NOT NULL DEFAULT 0.85,
                    source_conversation_id TEXT NOT NULL,
                    source_message_id TEXT,
                    source_user_text TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_accessed_at TEXT,
                    access_count INTEGER NOT NULL DEFAULT 0,
                    active INTEGER NOT NULL DEFAULT 1,
                    archived_at TEXT,
                    supersedes_memory_id TEXT,
                    expires_at TEXT,
                    language TEXT NOT NULL DEFAULT 'en',
                    manually_edited INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_normalized_key ON memories(normalized_key, active)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_subject ON memories(subject, active)"
            )
            self._migrate_category_names(connection)
            connection.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('version', ?)",
                (str(SCHEMA_VERSION),),
            )

    def _migrate_category_names(self, connection: sqlite3.Connection) -> None:
        rows = connection.execute("SELECT id, category, subject, key FROM memories").fetchall()
        for row in rows:
            category = normalize_category(row["category"])
            connection.execute(
                """
                UPDATE memories
                SET category=?, normalized_key=?
                WHERE id=?
                """,
                (category, normalize_key(category, row["subject"], row["key"]), row["id"]),
            )

    def _backup_before_migration(self) -> None:
        if self.path.exists():
            backup = self.path.with_suffix(f".backup-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}.db")
            shutil.copy2(self.path, backup)

    def schema_version(self) -> int:
        with self.connect() as connection:
            row = connection.execute("SELECT value FROM schema_meta WHERE key='version'").fetchone()
            return int(row["value"]) if row else 0

    def add_memory(
        self,
        parsed: ParsedMemory,
        source_conversation_id: str,
        source_message_id: str | None,
        source_user_text: str,
        supersedes_memory_id: str | None = None,
    ) -> MemoryRecord:
        now = utc_now()
        record_id = uuid.uuid4().hex
        with self.connect() as connection:
            if supersedes_memory_id:
                connection.execute(
                    "UPDATE memories SET active=0, archived_at=?, updated_at=? WHERE id=?",
                    (now, now, supersedes_memory_id),
                )
            connection.execute(
                """
                INSERT INTO memories (
                    id, category, subject, key, value, normalized_key, content, importance,
                    confidence, source_conversation_id, source_message_id, source_user_text,
                    created_at, updated_at, last_accessed_at, access_count, active,
                    archived_at, supersedes_memory_id, expires_at, language, manually_edited
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 0, 1, NULL, ?, ?, ?, 0)
                """,
                (
                    record_id,
                    normalize_category(parsed.category),
                    parsed.subject,
                    parsed.key,
                    parsed.value,
                    normalize_key(parsed.category, parsed.subject, parsed.key),
                    parsed.content,
                    parsed.importance,
                    parsed.confidence,
                    source_conversation_id,
                    source_message_id,
                    source_user_text,
                    now,
                    now,
                    supersedes_memory_id,
                    parsed.expires_at,
                    parsed.language,
                ),
            )
        return self.get(record_id)  # type: ignore[return-value]

    def get(self, memory_id: str) -> MemoryRecord | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
            return row_to_record(row) if row else None

    def active_by_normalized_key(self, normalized_key: str) -> MemoryRecord | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM memories WHERE normalized_key=? AND active=1 ORDER BY updated_at DESC LIMIT 1",
                (normalized_key,),
            ).fetchone()
            return row_to_record(row) if row else None

    def update_existing_provenance(
        self,
        memory_id: str,
        source_conversation_id: str,
        source_message_id: str | None,
        source_user_text: str,
    ) -> MemoryRecord | None:
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE memories
                SET source_conversation_id=?, source_message_id=?, source_user_text=?, updated_at=?
                WHERE id=?
                """,
                (source_conversation_id, source_message_id, source_user_text, now, memory_id),
            )
        return self.get(memory_id)

    def list_memories(self, include_archived: bool = False) -> list[MemoryRecord]:
        where = "" if include_archived else "WHERE active=1"
        with self.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM memories {where} ORDER BY active DESC, updated_at DESC"
            ).fetchall()
            return [row_to_record(row) for row in rows]

    def search(self, query: str = "", category: str = "", include_archived: bool = False) -> list[MemoryRecord]:
        clauses: list[str] = []
        params: list[str] = []
        if not include_archived:
            clauses.append("active=1")
        if category and category != "All":
            clauses.append("category=?")
            params.append(normalize_category(category))
        if query:
            like = f"%{query.casefold()}%"
            clauses.append(
                "(lower(subject) LIKE ? OR lower(key) LIKE ? OR lower(value) LIKE ? OR lower(content) LIKE ? OR lower(source_user_text) LIKE ?)"
            )
            params.extend([like] * 5)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        with self.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM memories {where} ORDER BY active DESC, updated_at DESC",
                params,
            ).fetchall()
            return [row_to_record(row) for row in rows]

    def mark_accessed(self, memory_ids: list[str]) -> None:
        if not memory_ids:
            return
        now = utc_now()
        with self.connect() as connection:
            connection.executemany(
                "UPDATE memories SET last_accessed_at=?, access_count=access_count+1 WHERE id=?",
                [(now, memory_id) for memory_id in memory_ids],
            )

    def archive(self, memory_id: str) -> None:
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                "UPDATE memories SET active=0, archived_at=?, updated_at=? WHERE id=?",
                (now, now, memory_id),
            )

    def restore(self, memory_id: str) -> None:
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                "UPDATE memories SET active=1, archived_at=NULL, updated_at=? WHERE id=?",
                (now, memory_id),
            )

    def delete(self, memory_id: str) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM memories WHERE id=?", (memory_id,))

    def clear_all(self) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM memories")

    def edit(self, memory_id: str, category: str, subject: str, key: str, value: str, content: str) -> MemoryRecord | None:
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE memories
                SET category=?, subject=?, key=?, value=?, normalized_key=?, content=?,
                    updated_at=?, manually_edited=1
                WHERE id=?
                """,
                (normalize_category(category), subject, key, value, normalize_key(category, subject, key), content, now, memory_id),
            )
        return self.get(memory_id)


def normalize_category(category: str) -> str:
    return CATEGORY_ALIASES.get(category.casefold().strip(), category if category in CATEGORIES else "Facts")


def normalize_key(category: str, subject: str, key: str) -> str:
    def clean(value: str) -> str:
        return " ".join(value.casefold().replace("_", " ").split())

    return f"{clean(normalize_category(category))}:{clean(subject)}:{clean(key)}"


def row_to_record(row: sqlite3.Row) -> MemoryRecord:
    return MemoryRecord(
        id=row["id"],
        category=row["category"],
        subject=row["subject"],
        key=row["key"],
        value=row["value"],
        normalized_key=row["normalized_key"],
        content=row["content"],
        importance=int(row["importance"]),
        confidence=float(row["confidence"]),
        source_conversation_id=row["source_conversation_id"],
        source_message_id=row["source_message_id"],
        source_user_text=row["source_user_text"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_accessed_at=row["last_accessed_at"],
        access_count=int(row["access_count"]),
        active=bool(row["active"]),
        archived_at=row["archived_at"],
        supersedes_memory_id=row["supersedes_memory_id"],
        expires_at=row["expires_at"],
        language=row["language"],
        manually_edited=bool(row["manually_edited"]),
    )
