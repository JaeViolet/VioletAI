"""SQLite persistence for VioletAI Memory V2.

Schema version 3 replaces the legacy explicit-memory store with a layered
model: durable + archived memories share the `v2_memories` table and
temporary cross-chat memories live in `v2_temporary_memories`. An append-only
`v2_memory_events` log records every mutation with before/after detail.

Version 3 adds structured attribute representation (canonical attribute and
core, natural statement, aliases, related entities, qualifiers, temporal
validity), a full-text search index, cross-chat episodes, and persistent global
counters for the temporary-memory lifecycle.

On startup, if the legacy `memories` table (schema version 1) is present, the
database file is backed up and the legacy table is dropped before the V2
schema is created, so no old code paths can interact with new data.
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from config import MEMORY_DB_PATH
from memory_v2.attributes import attribute_identity
from memory_v2.models import Episode, MemoryLayer, MemoryRecord, Provenance, ProvenanceKind, TemporaryRecord
from memory_v2.normalize import canonical_key as build_canonical_key

SCHEMA_VERSION = 3
_LAYERS = tuple(layer.value for layer in MemoryLayer)
_LEGACY_TABLES = ("memories",)
_TIMESTAMP_FORMAT = "%Y%m%d%H%M%S"


class MemoryStoreError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


class MemoryStore:
    def __init__(self, path: Path = MEMORY_DB_PATH) -> None:
        self.path = path
        self.fts_available = True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = None
        try:
            connection = sqlite3.connect(self.path, timeout=10)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 10000")
            yield connection
            connection.commit()
        except sqlite3.DatabaseError as error:
            raise MemoryStoreError(str(error)) from error
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass

    def _ensure_schema(self) -> None:
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            current = self._read_version(connection)
            if current > SCHEMA_VERSION:
                raise MemoryStoreError("Memory database schema is newer than this app.")
            if current < SCHEMA_VERSION:
                self._migrate_from_legacy(connection)
                if current < 3:
                    self._migrate_to_v3(connection)
            self._create_v2_schema(connection)
            connection.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('version', ?)",
                (str(SCHEMA_VERSION),),
            )

    def _read_version(self, connection: sqlite3.Connection) -> int:
        row = connection.execute("SELECT value FROM schema_meta WHERE key='version'").fetchone()
        return int(row["value"]) if row else 0

    def _migrate_from_legacy(self, connection: sqlite3.Connection) -> None:
        legacy_present = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='memories'"
        ).fetchone()
        if legacy_present and self.path.exists():
            backup = self.path.with_suffix(
                f".legacy-backup-{datetime.now(UTC).strftime(_TIMESTAMP_FORMAT)}.db"
            )
            shutil.copy2(self.path, backup)
            for table in _LEGACY_TABLES:
                connection.execute(f"DROP TABLE IF EXISTS {table}")
            for index_name in _legacy_index_names(connection):
                connection.execute(f"DROP INDEX IF EXISTS {index_name}")

    def _create_v2_schema(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS v2_memories (
                id TEXT PRIMARY KEY,
                layer TEXT NOT NULL CHECK(layer IN {_LAYERS!r}),
                category TEXT NOT NULL,
                subject TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                canonical_key TEXT NOT NULL,
                content TEXT NOT NULL,
                importance INTEGER NOT NULL DEFAULT 5,
                confidence REAL NOT NULL DEFAULT 0.0,
                provenance_kind TEXT NOT NULL DEFAULT 'explicit',
                source_conversation_id TEXT,
                source_message_id TEXT,
                source_user_text TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_accessed_at TEXT,
                access_count INTEGER NOT NULL DEFAULT 0,
                superseded_by_id TEXT,
                supersedes_id TEXT,
                archived_at TEXT,
                manually_edited INTEGER NOT NULL DEFAULT 0,
                edit_count INTEGER NOT NULL DEFAULT 0,
                language TEXT NOT NULL DEFAULT 'en',
                attribute TEXT NOT NULL DEFAULT '',
                attribute_core TEXT NOT NULL DEFAULT '',
                statement TEXT NOT NULL DEFAULT '',
                aliases TEXT NOT NULL DEFAULT '[]',
                related_entities TEXT NOT NULL DEFAULT '[]',
                qualifiers TEXT NOT NULL DEFAULT '{{}}',
                valid_from TEXT,
                valid_to TEXT,
                extra TEXT NOT NULL DEFAULT '{{}}'
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_v2_memories_canonical ON v2_memories(canonical_key, layer)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_v2_memories_subject ON v2_memories(subject, layer)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_v2_memories_updated ON v2_memories(updated_at)"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS v2_temporary_memories (
                id TEXT PRIMARY KEY,
                subject TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                canonical_key TEXT NOT NULL,
                content TEXT NOT NULL,
                importance INTEGER NOT NULL DEFAULT 5,
                confidence REAL NOT NULL DEFAULT 0.0,
                provenance_kind TEXT NOT NULL DEFAULT 'automatic',
                source_conversation_id TEXT,
                source_message_id TEXT,
                source_user_text TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_accessed_at TEXT,
                access_count INTEGER NOT NULL DEFAULT 0,
                token_at_created INTEGER NOT NULL DEFAULT 0,
                token_at_last_seen INTEGER NOT NULL DEFAULT 0,
                conversation_at_created INTEGER NOT NULL DEFAULT 0,
                conversation_at_last_seen INTEGER NOT NULL DEFAULT 0,
                unresolved INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'active',
                language TEXT NOT NULL DEFAULT 'en',
                attribute TEXT NOT NULL DEFAULT '',
                attribute_core TEXT NOT NULL DEFAULT '',
                extra TEXT NOT NULL DEFAULT '{{}}'
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_v2_temp_canonical ON v2_temporary_memories(canonical_key, status)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_v2_temp_status ON v2_temporary_memories(status)"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS v2_memory_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                event TEXT NOT NULL,
                layer TEXT NOT NULL,
                memory_id TEXT,
                actor TEXT NOT NULL DEFAULT 'user',
                source_conversation_id TEXT,
                source_message_id TEXT,
                detail TEXT
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_v2_events_created ON v2_memory_events(created_at)"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS v2_conversation_trackers (
                conversation_id TEXT PRIMARY KEY,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                message_count INTEGER NOT NULL DEFAULT 0,
                token_count INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS v2_episodes (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                kind TEXT NOT NULL,
                subject TEXT NOT NULL DEFAULT 'user',
                title TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL,
                source_conversation_id TEXT,
                start_at TEXT,
                end_at TEXT,
                importance INTEGER NOT NULL DEFAULT 5,
                entity_ids TEXT NOT NULL DEFAULT '[]',
                extra TEXT NOT NULL DEFAULT '{{}}'
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_v2_episodes_created ON v2_episodes(created_at)"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS v2_global_state (
                key TEXT PRIMARY KEY,
                value INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        self._create_fts(connection)

    def _create_fts(self, connection: sqlite3.Connection) -> None:
        try:
            connection.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS v2_memories_fts USING fts5(
                    subject, key, value, content, statement, source_user_text,
                    content='v2_memories'
                )
                """
            )
            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS v2_memories_fts_insert
                AFTER INSERT ON v2_memories
                BEGIN
                    INSERT INTO v2_memories_fts(rowid, subject, key, value, content, statement, source_user_text)
                    VALUES (new.rowid, new.subject, new.key, new.value, new.content, new.statement, new.source_user_text);
                END
                """
            )
            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS v2_memories_fts_delete
                AFTER DELETE ON v2_memories
                BEGIN
                    INSERT INTO v2_memories_fts(v2_memories_fts, rowid, subject, key, value, content, statement, source_user_text)
                    VALUES ('delete', old.rowid, old.subject, old.key, old.value, old.content, old.statement, old.source_user_text);
                END
                """
            )
            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS v2_memories_fts_update
                AFTER UPDATE ON v2_memories
                BEGIN
                    INSERT INTO v2_memories_fts(v2_memories_fts, rowid, subject, key, value, content, statement, source_user_text)
                    VALUES ('delete', old.rowid, old.subject, old.key, old.value, old.content, old.statement, old.source_user_text);
                    INSERT INTO v2_memories_fts(rowid, subject, key, value, content, statement, source_user_text)
                    VALUES (new.rowid, new.subject, new.key, new.value, new.content, new.statement, new.source_user_text);
                END
                """
            )
            self.fts_available = True
        except sqlite3.OperationalError:
            self.fts_available = False

    def _migrate_to_v3(self, connection: sqlite3.Connection) -> None:
        if not _table_exists(connection, "v2_memories"):
            return
        memory_columns = {row["name"] for row in connection.execute("PRAGMA table_info(v2_memories)")}
        additions = {
            "attribute": "TEXT NOT NULL DEFAULT ''",
            "attribute_core": "TEXT NOT NULL DEFAULT ''",
            "statement": "TEXT NOT NULL DEFAULT ''",
            "aliases": "TEXT NOT NULL DEFAULT '[]'",
            "related_entities": "TEXT NOT NULL DEFAULT '[]'",
            "qualifiers": "TEXT NOT NULL DEFAULT '{}'",
            "valid_from": "TEXT",
            "valid_to": "TEXT",
        }
        for name, definition in additions.items():
            if name not in memory_columns:
                connection.execute(f"ALTER TABLE v2_memories ADD COLUMN {name} {definition}")
        rows = connection.execute(
            "SELECT id, key, content FROM v2_memories WHERE attribute_core=''"
        ).fetchall()
        for row in rows:
            core = attribute_identity(row["key"])
            statement = row["content"] or ""
            connection.execute(
                "UPDATE v2_memories SET attribute=?, attribute_core=?, statement=? WHERE id=?",
                (row["key"], core, statement, row["id"]),
            )
        if not _table_exists(connection, "v2_temporary_memories"):
            return
        temporary_columns = {row["name"] for row in connection.execute("PRAGMA table_info(v2_temporary_memories)")}
        for name, definition in (("attribute", "TEXT NOT NULL DEFAULT ''"), ("attribute_core", "TEXT NOT NULL DEFAULT ''")):
            if name not in temporary_columns:
                connection.execute(f"ALTER TABLE v2_temporary_memories ADD COLUMN {name} {definition}")
        temp_rows = connection.execute(
            "SELECT id, key, content FROM v2_temporary_memories WHERE attribute_core=''"
        ).fetchall()
        for row in temp_rows:
            core = attribute_identity(row["key"])
            connection.execute(
                "UPDATE v2_temporary_memories SET attribute=?, attribute_core=? WHERE id=?",
                (row["key"], core, row["id"]),
            )

    def schema_version(self) -> int:
        with self.connect() as connection:
            return self._read_version(connection)

    def record_event(
        self,
        event: str,
        layer: MemoryLayer,
        memory_id: str | None = None,
        actor: str = "user",
        source_conversation_id: str | None = None,
        source_message_id: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO v2_memory_events (
                    created_at, event, layer, memory_id, actor,
                    source_conversation_id, source_message_id, detail
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    utc_now(),
                    event,
                    layer.value,
                    memory_id,
                    actor,
                    source_conversation_id,
                    source_message_id,
                    json.dumps(detail or {}, ensure_ascii=False),
                ),
            )

    def recent_events(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM v2_memory_events ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_row_to_event(row) for row in rows]

    def touch_conversation(self, conversation_id: str, token_count: int = 0) -> None:
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO v2_conversation_trackers (
                    conversation_id, first_seen_at, last_seen_at, message_count, token_count
                ) VALUES (?, ?, ?, 1, ?)
                ON CONFLICT(conversation_id) DO UPDATE SET
                    last_seen_at=excluded.last_seen_at,
                    message_count=message_count + 1,
                    token_count=token_count + excluded.token_count
                """,
                (conversation_id, now, now, token_count),
            )

    def conversation_activity(self) -> dict[str, int]:
        with self.connect() as connection:
            rows = connection.execute("SELECT conversation_id, message_count FROM v2_conversation_trackers").fetchall()
        return {row["conversation_id"]: int(row["message_count"]) for row in rows}

    def insert_memory(
        self,
        parsed_memory: object,
        layer: MemoryLayer = MemoryLayer.DURABLE,
    ) -> MemoryRecord:
        parsed = _as_parsed(parsed_memory)
        now = utc_now()
        record_id = uuid.uuid4().hex
        canonical = build_canonical_key(parsed.category, parsed.subject, parsed.key)
        provenance = parsed.provenance
        attribute = parsed.attribute or parsed.key
        attribute_core = parsed.attribute_core or attribute_identity(parsed.key)
        statement = parsed.statement or parsed.content or f"{parsed.key} is {parsed.value}"
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO v2_memories (
                    id, layer, category, subject, key, value, canonical_key, content,
                    importance, confidence, provenance_kind, source_conversation_id,
                    source_message_id, source_user_text, created_at, updated_at,
                    language, attribute, attribute_core, statement, aliases,
                    related_entities, qualifiers, valid_from, valid_to, extra
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    layer.value,
                    parsed.category,
                    parsed.subject,
                    parsed.key,
                    parsed.value,
                    canonical,
                    parsed.content or f"{parsed.key} is {parsed.value}",
                    parsed.importance,
                    parsed.confidence,
                    provenance.kind.value,
                    provenance.conversation_id,
                    provenance.message_id,
                    provenance.user_text,
                    now,
                    now,
                    parsed.language,
                    attribute,
                    attribute_core,
                    statement,
                    json.dumps(parsed.aliases or [], ensure_ascii=False),
                    json.dumps(parsed.related_entities or [], ensure_ascii=False),
                    json.dumps(parsed.qualifiers or {}, ensure_ascii=False),
                    parsed.valid_from,
                    parsed.valid_to,
                    json.dumps(parsed.extra or {}, ensure_ascii=False),
                ),
            )
        return self.get_memory(record_id)  # type: ignore[return-value]

    def get_memory(self, memory_id: str) -> MemoryRecord | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM v2_memories WHERE id=?", (memory_id,)).fetchone()
            return _row_to_memory(row) if row else None

    def active_by_canonical_key(self, canonical: str) -> MemoryRecord | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM v2_memories WHERE canonical_key=? AND layer='durable' ORDER BY updated_at DESC LIMIT 1",
                (canonical,),
            ).fetchone()
            return _row_to_memory(row) if row else None

    def list_memories(self, include_archived: bool = False) -> list[MemoryRecord]:
        where = "" if include_archived else "WHERE layer='durable'"
        with self.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM v2_memories {where} ORDER BY (layer='durable') DESC, updated_at DESC"
            ).fetchall()
            return [_row_to_memory(row) for row in rows]

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
                f"SELECT * FROM v2_memories {where} ORDER BY updated_at DESC LIMIT ?",
                [*params, limit],
            ).fetchall()
            return [_row_to_memory(row) for row in rows]

    def update_memory(
        self,
        memory_id: str,
        *,
        category: str | None = None,
        subject: str | None = None,
        key: str | None = None,
        value: str | None = None,
        content: str | None = None,
        importance: int | None = None,
        confidence: float | None = None,
        provenance: Provenance | None = None,
        manual: bool = False,
        statement: str | None = None,
        aliases: list[str] | None = None,
        related_entities: list[str] | None = None,
        qualifiers: dict[str, Any] | None = None,
        valid_from: str | None = None,
        valid_to: str | None = None,
    ) -> MemoryRecord | None:
        existing = self.get_memory(memory_id)
        if existing is None:
            return None
        new_category = category if category is not None else existing.category
        new_subject = subject if subject is not None else existing.subject
        new_key = key if key is not None else existing.key
        new_value = value if value is not None else existing.value
        canonical = build_canonical_key(new_category, new_subject, new_key)
        new_attribute = existing.attribute or new_key
        new_core = attribute_identity(new_key)
        new_statement = statement if statement is not None else (content if content is not None else existing.statement or existing.content)
        new_aliases = aliases if aliases is not None else existing.aliases
        new_entities = related_entities if related_entities is not None else existing.related_entities
        new_qualifiers = qualifiers if qualifiers is not None else existing.qualifiers
        new_valid_from = valid_from if valid_from is not None else existing.valid_from
        new_valid_to = valid_to if valid_to is not None else existing.valid_to
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE v2_memories
                SET category=?, subject=?, key=?, value=?, canonical_key=?, content=?,
                    importance=?, confidence=?, source_user_text=?, source_conversation_id=?,
                    source_message_id=?, provenance_kind=?, updated_at=?,
                    attribute=?, attribute_core=?, statement=?, aliases=?, related_entities=?,
                    qualifiers=?, valid_from=?, valid_to=?,
                    manually_edited=CASE WHEN ? THEN 1 ELSE manually_edited END,
                    edit_count=edit_count + 1
                WHERE id=?
                """,
                (
                    new_category,
                    new_subject,
                    new_key,
                    new_value,
                    canonical,
                    content if content is not None else existing.content,
                    importance if importance is not None else existing.importance,
                    confidence if confidence is not None else existing.confidence,
                    provenance.user_text if provenance else existing.provenance.user_text,
                    provenance.conversation_id if provenance else existing.provenance.conversation_id,
                    provenance.message_id if provenance else existing.provenance.message_id,
                    provenance.kind.value if provenance else existing.provenance.kind.value,
                    now,
                    new_attribute,
                    new_core,
                    new_statement,
                    json.dumps(new_aliases or [], ensure_ascii=False),
                    json.dumps(new_entities or [], ensure_ascii=False),
                    json.dumps(new_qualifiers or {}, ensure_ascii=False),
                    new_valid_from,
                    new_valid_to,
                    1 if manual else 0,
                    memory_id,
                ),
            )
        return self.get_memory(memory_id)

    def mark_accessed(self, memory_ids: list[str]) -> None:
        if not memory_ids:
            return
        now = utc_now()
        with self.connect() as connection:
            connection.executemany(
                "UPDATE v2_memories SET last_accessed_at=?, access_count=access_count+1 WHERE id=?",
                [(now, memory_id) for memory_id in memory_ids],
            )

    def archive_memory(self, memory_id: str) -> MemoryRecord | None:
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                "UPDATE v2_memories SET layer='archived', archived_at=?, updated_at=? WHERE id=? AND layer='durable'",
                (now, now, memory_id),
            )
        return self.get_memory(memory_id)

    def restore_memory(self, memory_id: str) -> MemoryRecord | None:
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                "UPDATE v2_memories SET layer='durable', archived_at=NULL, updated_at=? WHERE id=? AND layer='archived'",
                (now, memory_id),
            )
        return self.get_memory(memory_id)

    def delete_memory(self, memory_id: str) -> bool:
        with self.connect() as connection:
            cursor = connection.execute("DELETE FROM v2_memories WHERE id=?", (memory_id,))
            return cursor.rowcount > 0

    def clear_durable(self) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM v2_memories WHERE layer='durable'")

    def clear_archived(self) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM v2_memories WHERE layer='archived'")

    def set_supersede(self, old_id: str, new_id: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE v2_memories SET superseded_by_id=? WHERE id=?",
                (new_id, old_id),
            )
            connection.execute(
                "UPDATE v2_memories SET supersedes_id=? WHERE id=?",
                (old_id, new_id),
            )

    def group_memories_by_canonical_key(self, include_archived: bool = True) -> dict[str, list[MemoryRecord]]:
        groups: dict[str, list[MemoryRecord]] = {}
        for memory in self.list_memories(include_archived=include_archived):
            groups.setdefault(memory.canonical_key, []).append(memory)
        return groups

    def insert_temporary(
        self,
        parsed_memory: object,
        token_counter: int = 0,
        conversation_index: int = 0,
    ) -> TemporaryRecord:
        parsed = _as_parsed(parsed_memory)
        now = utc_now()
        record_id = uuid.uuid4().hex
        canonical = build_canonical_key("Temporary", parsed.subject, parsed.key)
        provenance = parsed.provenance
        attribute = parsed.attribute or parsed.key
        attribute_core = parsed.attribute_core or attribute_identity(parsed.key)
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO v2_temporary_memories (
                    id, subject, key, value, canonical_key, content, importance, confidence,
                    provenance_kind, source_conversation_id, source_message_id, source_user_text,
                    created_at, updated_at, token_at_created, token_at_last_seen,
                    conversation_at_created, conversation_at_last_seen, unresolved,
                    language, attribute, attribute_core, extra
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    parsed.subject,
                    parsed.key,
                    parsed.value,
                    canonical,
                    parsed.content or f"{parsed.key} is {parsed.value}",
                    parsed.importance,
                    parsed.confidence,
                    provenance.kind.value,
                    provenance.conversation_id,
                    provenance.message_id,
                    provenance.user_text,
                    now,
                    now,
                    token_counter,
                    token_counter,
                    conversation_index,
                    conversation_index,
                    1 if parsed.unresolved else 0,
                    parsed.language,
                    attribute,
                    attribute_core,
                    json.dumps(parsed.extra or {}, ensure_ascii=False),
                ),
            )
        return self.get_temporary(record_id)  # type: ignore[return-value]

    def get_temporary(self, memory_id: str) -> TemporaryRecord | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM v2_temporary_memories WHERE id=?", (memory_id,)
            ).fetchone()
            return _row_to_temporary(row) if row else None

    def active_temporary_by_canonical_key(self, canonical: str) -> TemporaryRecord | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM v2_temporary_memories WHERE canonical_key=? AND status='active' ORDER BY updated_at DESC LIMIT 1",
                (canonical,),
            ).fetchone()
            return _row_to_temporary(row) if row else None

    def list_temporary(self, status: str = "active") -> list[TemporaryRecord]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM v2_temporary_memories WHERE status=? ORDER BY updated_at DESC",
                (status,),
            ).fetchall()
            return [_row_to_temporary(row) for row in rows]

    def touch_temporary(self, memory_id: str, token_counter: int, conversation_index: int) -> None:
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE v2_temporary_memories
                SET last_accessed_at=?, access_count=access_count + 1,
                    token_at_last_seen=?, conversation_at_last_seen=?
                WHERE id=?
                """,
                (now, token_counter, conversation_index, memory_id),
            )

    def update_temporary(self, memory_id: str, **fields: Any) -> TemporaryRecord | None:
        allowed = {
            "subject", "key", "value", "content", "importance", "confidence",
            "unresolved", "status", "source_user_text", "source_conversation_id",
            "source_message_id",
        }
        updates = {name: value for name, value in fields.items() if name in allowed}
        if not updates:
            return self.get_temporary(memory_id)
        columns = ", ".join(f"{name}=?" for name in updates)
        values = list(updates.values())
        with self.connect() as connection:
            connection.execute(
                f"UPDATE v2_temporary_memories SET {columns}, updated_at=? WHERE id=?",
                [*values, utc_now(), memory_id],
            )
        return self.get_temporary(memory_id)

    def expire_temporary(self, memory_id: str) -> TemporaryRecord | None:
        return self.update_temporary(memory_id, status="expired")

    def delete_temporary(self, memory_id: str) -> bool:
        with self.connect() as connection:
            cursor = connection.execute("DELETE FROM v2_temporary_memories WHERE id=?", (memory_id,))
            return cursor.rowcount > 0

    def clear_temporary(self) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM v2_temporary_memories")

    # ---------------------------------------------------------- global state

    def get_global(self, key: str, default: int = 0) -> int:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT value FROM v2_global_state WHERE key=?", (key,)
            ).fetchone()
            if not row:
                return default
            try:
                return int(row["value"])
            except (TypeError, ValueError):
                return default

    def set_global(self, key: str, value: int) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO v2_global_state(key, value) VALUES(?, ?)",
                (key, str(value)),
            )

    def get_global_str(self, key: str, default: str = "") -> str:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT value FROM v2_global_state WHERE key=?", (key,)
            ).fetchone()
            return row["value"] if row else default

    def set_global_str(self, key: str, value: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO v2_global_state(key, value) VALUES(?, ?)",
                (key, value),
            )

    # ---------------------------------------------------------------- episodes

    def add_episode(
        self,
        kind: str,
        title: str,
        content: str,
        *,
        subject: str = "user",
        source_conversation_id: str | None = None,
        start_at: str | None = None,
        end_at: str | None = None,
        importance: int = 5,
        entity_ids: list[str] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> Episode:
        episode_id = uuid.uuid4().hex
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO v2_episodes (
                    id, created_at, kind, subject, title, content,
                    source_conversation_id, start_at, end_at, importance, entity_ids, extra
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    episode_id,
                    utc_now(),
                    kind,
                    subject,
                    title,
                    content,
                    source_conversation_id,
                    start_at,
                    end_at,
                    importance,
                    json.dumps(entity_ids or [], ensure_ascii=False),
                    json.dumps(extra or {}, ensure_ascii=False),
                ),
            )
        return self.get_episode(episode_id)  # type: ignore[return-value]

    def get_episode(self, episode_id: str) -> Episode | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM v2_episodes WHERE id=?", (episode_id,)
            ).fetchone()
            return _row_to_episode(row) if row else None

    def list_episodes(self, limit: int = 50) -> list[Episode]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM v2_episodes ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [_row_to_episode(row) for row in rows]

    # --------------------------------------------------------------- full text

    def fts_search(self, query: str, limit: int = 20) -> list[MemoryRecord]:
        if not self.fts_available or not query:
            return []
        tokens = _fts_query_terms(query)
        if not tokens:
            return []
        search = " AND ".join(f'"{token}"' for token in tokens)
        with self.connect() as connection:
            try:
                rows = connection.execute(
                    """
                    SELECT m.* FROM v2_memories AS m
                    JOIN v2_memories_fts AS f ON f.rowid = m.rowid
                    WHERE v2_memories_fts MATCH ? AND m.layer='durable'
                    ORDER BY bm25(v2_memories_fts) LIMIT ?
                    """,
                    (search, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                return []
            return [_row_to_memory(row) for row in rows]


def _as_parsed(parsed_memory: object) -> object:
    if not hasattr(parsed_memory, "category"):
        raise TypeError("expected ParsedMemory-like object")
    return parsed_memory


def _row_to_memory(row: sqlite3.Row) -> MemoryRecord:
    provenance = Provenance(
        conversation_id=row["source_conversation_id"],
        message_id=row["source_message_id"],
        user_text=row["source_user_text"] or "",
        kind=ProvenanceKind(row["provenance_kind"]),
    )
    extra: dict[str, Any] = {}
    try:
        extra = json.loads(row["extra"] or "{}")
    except (json.JSONDecodeError, TypeError):
        extra = {}
    return MemoryRecord(
        id=row["id"],
        layer=MemoryLayer(row["layer"]),
        category=row["category"],
        subject=row["subject"],
        key=row["key"],
        value=row["value"],
        canonical_key=row["canonical_key"],
        content=row["content"],
        importance=int(row["importance"]),
        confidence=float(row["confidence"]),
        provenance=provenance,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_accessed_at=row["last_accessed_at"],
        access_count=int(row["access_count"]),
        superseded_by_id=row["superseded_by_id"],
        supersedes_id=row["supersedes_id"],
        archived_at=row["archived_at"],
        manually_edited=bool(row["manually_edited"]),
        edit_count=int(row["edit_count"]),
        language=row["language"],
        attribute=row["attribute"] or "",
        attribute_core=row["attribute_core"] or "",
        statement=row["statement"] or "",
        aliases=_load_list(row["aliases"]),
        related_entities=_load_list(row["related_entities"]),
        qualifiers=_load_dict(row["qualifiers"]),
        valid_from=row["valid_from"],
        valid_to=row["valid_to"],
        extra=extra,
    )


def _row_to_temporary(row: sqlite3.Row) -> TemporaryRecord:
    provenance = Provenance(
        conversation_id=row["source_conversation_id"],
        message_id=row["source_message_id"],
        user_text=row["source_user_text"] or "",
        kind=ProvenanceKind(row["provenance_kind"]),
    )
    extra: dict[str, Any] = {}
    try:
        extra = json.loads(row["extra"] or "{}")
    except (json.JSONDecodeError, TypeError):
        extra = {}
    return TemporaryRecord(
        id=row["id"],
        subject=row["subject"],
        key=row["key"],
        value=row["value"],
        canonical_key=row["canonical_key"],
        content=row["content"],
        importance=int(row["importance"]),
        confidence=float(row["confidence"]),
        provenance=provenance,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_accessed_at=row["last_accessed_at"],
        access_count=int(row["access_count"]),
        token_at_created=int(row["token_at_created"]),
        token_at_last_seen=int(row["token_at_last_seen"]),
        conversation_at_created=int(row["conversation_at_created"]),
        conversation_at_last_seen=int(row["conversation_at_last_seen"]),
        unresolved=int(row["unresolved"]),
        status=row["status"],
        language=row["language"],
        attribute=row["attribute"] or "",
        attribute_core=row["attribute_core"] or "",
        extra=extra,
    )


def _row_to_event(row: sqlite3.Row) -> dict[str, Any]:
    detail: dict[str, Any] = {}
    try:
        detail = json.loads(row["detail"] or "{}")
    except (json.JSONDecodeError, TypeError):
        detail = {}
    return {
        "id": int(row["id"]),
        "created_at": row["created_at"],
        "event": row["event"],
        "layer": row["layer"],
        "memory_id": row["memory_id"],
        "actor": row["actor"],
        "source_conversation_id": row["source_conversation_id"],
        "source_message_id": row["source_message_id"],
        "detail": detail,
    }


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return (
        connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        is not None
    )


def _row_to_episode(row: sqlite3.Row) -> Episode:
    extra: dict[str, Any] = {}
    try:
        extra = json.loads(row["extra"] or "{}")
    except (json.JSONDecodeError, TypeError):
        extra = {}
    return Episode(
        id=row["id"],
        created_at=row["created_at"],
        kind=row["kind"],
        subject=row["subject"],
        title=row["title"],
        content=row["content"],
        source_conversation_id=row["source_conversation_id"],
        start_at=row["start_at"],
        end_at=row["end_at"],
        importance=int(row["importance"]),
        entity_ids=_load_list(row["entity_ids"]),
        extra=extra,
    )


def _load_list(raw: object) -> list[str]:
    try:
        loaded = json.loads(raw or "[]")
        return [str(item) for item in loaded] if isinstance(loaded, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _load_dict(raw: object) -> dict[str, Any]:
    try:
        loaded = json.loads(raw or "{}")
        return loaded if isinstance(loaded, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


_FTS_TOKEN_RE = re.compile(r"[\w']+")
_FTS_STOPWORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "my", "your", "our", "their",
    "of", "in", "on", "at", "to", "for", "and", "or", "what", "when", "where",
    "who", "which", "how", "i", "you", "we", "it", "me", "us", "them", "do", "does",
})


def _fts_query_terms(query: str) -> list[str]:
    tokens = [token for token in _FTS_TOKEN_RE.findall((query or "").casefold()) if token not in _FTS_STOPWORDS]
    return tokens[:6]


def _legacy_index_names(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name IN ('idx_memories_normalized_key', 'idx_memories_subject')"
    ).fetchall()
    return [row["name"] for row in rows]
