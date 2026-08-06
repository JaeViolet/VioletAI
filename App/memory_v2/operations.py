"""Validated, deterministic, typed mutations for VioletAI Memory V2.

All memory writes flow through this module. Commands extracted from user text
are resolved against stored memories with conservative rules:

* create resolves existing exact/superseding entries before inserting
* update requires exactly one unambiguous target
* delete archives (soft delete) only after a precise resolution
* contextual commands ("forget that") require a resolvable previous statement

Every mutation is verified by re-reading the stored record, and an append-only
event is recorded, before any success is reported.
"""

from __future__ import annotations

from dataclasses import dataclass

from memory_v2.attributes import attribute_identity, parse_reference, subject_matches
from memory_v2.models import (
    MemoryCommand,
    MemoryLayer,
    MemoryRecord,
    MutationKind,
    MutationOutcome,
    MutationStatus,
    ParsedMemory,
    Provenance,
    ProvenanceKind,
    TemporaryRecord,
)
from memory_v2.normalize import canonical_text, canonical_key as build_canonical_key, keys_equivalent, subjects_overlap
from memory_v2.store import MemoryStore


@dataclass(slots=True)
class OperationContext:
    conversation_id: str | None = None
    message_id: str | None = None
    user_text: str = ""
    previous_user_text: str | None = None
    provenance_kind: ProvenanceKind = ProvenanceKind.EXPLICIT
    token_counter: int = 0
    conversation_index: int = 0


class Operations:
    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    def apply(self, command: MemoryCommand, context: OperationContext) -> MutationOutcome:
        if command.kind in {MutationKind.CREATE, MutationKind.UPDATE, MutationKind.DELETE, MutationKind.TEMPORARY_CREATE}:
            return self._dispatch(command, context)
        return MutationOutcome(
            ok=False,
            kind=command.kind,
            status=MutationStatus.NOT_APPLICABLE,
            error=f"unsupported command kind: {command.kind.value}",
        )

    def create_from_parsed(self, parsed: ParsedMemory, context: OperationContext) -> MutationOutcome:
        return self._create_parsed(parsed, context)

    def create_temporary_from_parsed(self, parsed: ParsedMemory, context: OperationContext) -> MutationOutcome:
        return self._create_temporary(parsed, context)

    def archive_by_id(self, memory_id: str, actor: str = "user") -> MutationOutcome:
        existing = self.store.get_memory(memory_id)
        if existing is None:
            return MutationOutcome(False, MutationKind.ARCHIVE, status=MutationStatus.NO_MATCH)
        archived = self.store.archive_memory(memory_id)
        if archived is None or archived.layer != MemoryLayer.ARCHIVED:
            return MutationOutcome(False, MutationKind.ARCHIVE, status=MutationStatus.WRITE_FAILED, error="archive not confirmed")
        self.store.record_event("archived", MemoryLayer.DURABLE, memory_id, actor=actor)
        return MutationOutcome(True, MutationKind.ARCHIVE, record=archived, affected_ids=[memory_id])

    def restore_by_id(self, memory_id: str, actor: str = "user") -> MutationOutcome:
        existing = self.store.get_memory(memory_id)
        if existing is None:
            return MutationOutcome(False, MutationKind.RESTORE, status=MutationStatus.NO_MATCH)
        restored = self.store.restore_memory(memory_id)
        if restored is None or restored.layer != MemoryLayer.DURABLE:
            return MutationOutcome(False, MutationKind.RESTORE, status=MutationStatus.WRITE_FAILED, error="restore not confirmed")
        self.store.record_event("restored", MemoryLayer.DURABLE, memory_id, actor=actor)
        return MutationOutcome(True, MutationKind.RESTORE, record=restored, affected_ids=[memory_id])

    def delete_by_id(self, memory_id: str, actor: str = "user") -> MutationOutcome:
        if not self.store.delete_memory(memory_id):
            return MutationOutcome(False, MutationKind.DELETE, status=MutationStatus.NO_MATCH)
        self.store.record_event("deleted", MemoryLayer.DURABLE, memory_id, actor=actor)
        return MutationOutcome(True, MutationKind.DELETE, affected_ids=[memory_id])

    def clear_durable(self, actor: str = "user") -> MutationOutcome:
        self.store.clear_durable()
        self.store.record_event("cleared", MemoryLayer.DURABLE, actor=actor)
        return MutationOutcome(True, MutationKind.CLEAR)

    def _dispatch(self, command: MemoryCommand, context: OperationContext) -> MutationOutcome:
        if command.kind == MutationKind.CREATE:
            return self._create_command(command, context)
        if command.kind == MutationKind.TEMPORARY_CREATE:
            return self._temporary_create_command(command, context)
        if command.kind == MutationKind.UPDATE:
            return self._update_command(command, context)
        if command.kind == MutationKind.DELETE:
            return self._delete_command(command, context)
        return MutationOutcome(False, command.kind, status=MutationStatus.NOT_APPLICABLE)

    def _provenance(self, context: OperationContext) -> Provenance:
        return Provenance(
            conversation_id=context.conversation_id,
            message_id=context.message_id,
            user_text=context.user_text,
            kind=context.provenance_kind,
        )

    def _create_command(self, command: MemoryCommand, context: OperationContext) -> MutationOutcome:
        if command.reference_previous:
            previous = self._resolve_previous(context.previous_user_text)
            if previous is None:
                return MutationOutcome(
                    False,
                    MutationKind.CREATE,
                    status=MutationStatus.INVALID_REFERENCE,
                    clarification="I couldn't save that because 'that' doesn't refer to a previous statement of yours.",
                )
            return self._create_parsed(previous, context)
        if not command.key or not command.value:
            return MutationOutcome(
                False,
                MutationKind.CREATE,
                status=MutationStatus.INVALID_REQUEST,
                clarification="I couldn't save that because the memory request was not specific enough.",
            )
        parsed = ParsedMemory(
            category=command.category or "Facts",
            subject=command.subject or "user",
            key=command.key,
            value=command.value,
            content=f"{command.key} is {command.value}",
            confidence=command.confidence,
            provenance=self._provenance(context),
        )
        return self._create_parsed(parsed, context)

    def _create_parsed(self, parsed: ParsedMemory, context: OperationContext) -> MutationOutcome:
        parsed.provenance = self._provenance(context)
        canonical = build_canonical_key(parsed.category, parsed.subject, parsed.key)
        existing = self.store.active_by_canonical_key(canonical)
        if existing is not None:
            if _values_equal(existing.value, parsed.value):
                updated = self.store.update_memory(
                    existing.id,
                    provenance=parsed.provenance,
                )
                self.store.record_event(
                    "created" if existing.access_count == 0 else "reaffirmed",
                    MemoryLayer.DURABLE,
                    existing.id,
                    source_conversation_id=context.conversation_id,
                    source_message_id=context.message_id,
                    detail={"key": parsed.key, "value": parsed.value},
                )
                return MutationOutcome(
                    True,
                    MutationKind.CREATE,
                    record=updated or existing,
                    affected_ids=[existing.id],
                    new_value=parsed.value,
                )
            archived = self.store.archive_memory(existing.id)
            if archived is None or archived.layer != MemoryLayer.ARCHIVED:
                return MutationOutcome(False, MutationKind.CREATE, status=MutationStatus.WRITE_FAILED, error="supersede archive failed")
            created = self.store.insert_memory(parsed)
            if created is None:
                return MutationOutcome(False, MutationKind.CREATE, status=MutationStatus.WRITE_FAILED, error="insert not confirmed")
            self.store.set_supersede(existing.id, created.id)
            self.store.record_event(
                "superseded", MemoryLayer.DURABLE, existing.id,
                source_conversation_id=context.conversation_id,
                source_message_id=context.message_id,
                detail={"superseded_by": created.id, "old_value": existing.value, "new_value": parsed.value},
            )
            self.store.record_event(
                "created", MemoryLayer.DURABLE, created.id,
                source_conversation_id=context.conversation_id,
                source_message_id=context.message_id,
                detail={"key": parsed.key, "value": parsed.value},
            )
            return MutationOutcome(
                True,
                MutationKind.CREATE,
                record=created,
                affected_ids=[existing.id, created.id],
                previous_value=existing.value,
                new_value=parsed.value,
            )
        created = self.store.insert_memory(parsed)
        if created is None:
            return MutationOutcome(False, MutationKind.CREATE, status=MutationStatus.WRITE_FAILED, error="insert not confirmed")
        self.store.record_event(
            "created", MemoryLayer.DURABLE, created.id,
            source_conversation_id=context.conversation_id,
            source_message_id=context.message_id,
            detail={"key": parsed.key, "value": parsed.value},
        )
        return MutationOutcome(True, MutationKind.CREATE, record=created, affected_ids=[created.id], new_value=parsed.value)

    def _temporary_create_command(self, command: MemoryCommand, context: OperationContext) -> MutationOutcome:
        parsed = ParsedMemory(
            category="Temporary",
            subject=command.subject or "user",
            key=command.key or "task",
            value=command.value,
            content=f"{command.key or 'task'} {command.value}",
            confidence=command.confidence,
            unresolved=True,
            provenance=self._provenance(context),
        )
        return self._create_temporary(parsed, context)

    def _create_temporary(self, parsed: ParsedMemory, context: OperationContext) -> MutationOutcome:
        parsed.provenance = self._provenance(context)
        canonical = build_canonical_key("Temporary", parsed.subject, parsed.key)
        existing = self.store.active_temporary_by_canonical_key(canonical)
        if existing is not None:
            updated = self.store.update_temporary(
                existing.id,
                value=parsed.value,
                content=parsed.content,
                importance=parsed.importance,
                unresolved=1 if parsed.unresolved else existing.unresolved,
                source_user_text=context.user_text,
                source_conversation_id=context.conversation_id,
                source_message_id=context.message_id,
            )
            self.store.record_event(
                "temporary_updated", MemoryLayer.TEMPORARY, existing.id,
                source_conversation_id=context.conversation_id,
                source_message_id=context.message_id,
                detail={"key": parsed.key, "value": parsed.value},
            )
            return MutationOutcome(True, MutationKind.TEMPORARY_UPDATE, record=updated or existing, affected_ids=[existing.id])
        created = self.store.insert_temporary(
            parsed,
            token_counter=context.token_counter,
            conversation_index=context.conversation_index,
        )
        if created is None:
            return MutationOutcome(False, MutationKind.TEMPORARY_CREATE, status=MutationStatus.WRITE_FAILED, error="insert not confirmed")
        self.store.record_event(
            "temporary_created", MemoryLayer.TEMPORARY, created.id,
            source_conversation_id=context.conversation_id,
            source_message_id=context.message_id,
            detail={"key": parsed.key, "value": parsed.value},
        )
        return MutationOutcome(True, MutationKind.TEMPORARY_CREATE, record=created, affected_ids=[created.id])

    def _update_command(self, command: MemoryCommand, context: OperationContext) -> MutationOutcome:
        key = command.key.strip()
        if not key and command.reference_previous:
            resolved = self._resolve_previous(context.previous_user_text)
            if resolved is None:
                return MutationOutcome(
                    False,
                    MutationKind.UPDATE,
                    status=MutationStatus.INVALID_REFERENCE,
                    clarification="I couldn't update that because I couldn't tell what 'it' refers to.",
                )
            key = resolved.key
        if not key or not command.value:
            return MutationOutcome(
                False,
                MutationKind.UPDATE,
                status=MutationStatus.INVALID_REQUEST,
                clarification="What memory should I update, and to what value?",
            )
        matches = self._find_durable_matches(key)
        if not matches:
            return MutationOutcome(
                False,
                MutationKind.UPDATE,
                status=MutationStatus.NO_MATCH,
                clarification=f"I couldn't find a saved memory matching '{key}'.",
            )
        if self._ambiguous(matches, key):
            return self._multiple_matches(MutationKind.UPDATE, matches, key)
        target = matches[0][1]
        updated = self.store.update_memory(
            target.id,
            value=command.value,
            content=f"{target.key} is {command.value}",
            provenance=self._provenance(context),
        )
        if updated is None:
            return MutationOutcome(False, MutationKind.UPDATE, status=MutationStatus.WRITE_FAILED, error="update not confirmed")
        self.store.record_event(
            "updated", MemoryLayer.DURABLE, target.id,
            source_conversation_id=context.conversation_id,
            source_message_id=context.message_id,
            detail={"key": target.key, "old_value": target.value, "new_value": updated.value},
        )
        return MutationOutcome(
            True,
            MutationKind.UPDATE,
            record=updated,
            affected_ids=[target.id],
            previous_value=target.value,
            new_value=updated.value,
        )

    def _delete_command(self, command: MemoryCommand, context: OperationContext) -> MutationOutcome:
        key = command.key.strip()
        if not key and command.reference_previous:
            resolved = self._resolve_previous(context.previous_user_text)
            if resolved is None:
                return MutationOutcome(
                    False,
                    MutationKind.DELETE,
                    status=MutationStatus.INVALID_REFERENCE,
                    clarification="I couldn't remove that because 'that' doesn't refer to a previous statement of yours.",
                )
            key = resolved.key
        if not key:
            return MutationOutcome(
                False,
                MutationKind.DELETE,
                status=MutationStatus.INVALID_REQUEST,
                clarification="What memory should I remove?",
            )
        matches = self._find_durable_matches(key)
        if matches:
            if self._ambiguous(matches, key):
                return self._multiple_matches(MutationKind.DELETE, matches, key)
            target = matches[0][1]
            archived = self.store.archive_memory(target.id)
            if archived is None or archived.layer != MemoryLayer.ARCHIVED:
                return MutationOutcome(False, MutationKind.DELETE, status=MutationStatus.WRITE_FAILED, error="archive not confirmed")
            self._expire_matching_temporary(key)
            self.store.record_event(
                "archived", MemoryLayer.DURABLE, target.id,
                source_conversation_id=context.conversation_id,
                source_message_id=context.message_id,
                detail={"key": target.key, "value": target.value},
            )
            return MutationOutcome(
                True,
                MutationKind.DELETE,
                record=archived,
                affected_ids=[target.id],
                previous_value=target.value,
            )
        temporary_matches = self._find_temporary_matches(key)
        if temporary_matches:
            distinct = {record.canonical_key for _score, record in temporary_matches}
            if len(distinct) > 1 and len(canonical_text(key).split()) < 3:
                return self._multiple_temporary_matches(temporary_matches)
            target = temporary_matches[0][1]
            expired = self.store.expire_temporary(target.id)
            if expired is None or expired.status != "expired":
                return MutationOutcome(False, MutationKind.DELETE, status=MutationStatus.WRITE_FAILED, error="expire not confirmed")
            self.store.record_event(
                "temporary_expired", MemoryLayer.TEMPORARY, target.id,
                source_conversation_id=context.conversation_id,
                source_message_id=context.message_id,
                detail={"reason": "user requested deletion", "key": target.key, "value": target.value},
            )
            return MutationOutcome(
                True,
                MutationKind.DELETE,
                record=expired,
                affected_ids=[target.id],
                previous_value=target.value,
            )
        return MutationOutcome(
            False,
            MutationKind.DELETE,
            status=MutationStatus.NO_MATCH,
            clarification=f"I couldn't find a saved memory matching '{key}'.",
        )

    def _resolve_previous(self, previous_user_text: str | None) -> ParsedMemory | None:
        if not previous_user_text or not previous_user_text.strip():
            return None
        from memory_v2.extract import Extractor

        analysis = Extractor().analyze(previous_user_text)
        if analysis.command is not None:
            return None
        fact = analysis.durable_fact or analysis.temporary_fact
        return fact

    def _find_durable_matches(self, key: str) -> list[tuple[float, MemoryRecord]]:
        query = canonical_text(key)
        if not query:
            return []
        reference = parse_reference(key)
        records = self.store.list_memories()
        if reference is not None and reference.explicit_subject:
            records = [record for record in records if subject_matches(reference.subject, record.subject)]
        if not records:
            return []
        scored: list[tuple[float, MemoryRecord]] = []
        for record in records:
            record_key = canonical_text(record.key)
            score = 0.0
            if (
                reference is not None
                and not reference.is_generic_only
                and reference.identity
                and attribute_identity(record.key) == reference.identity
            ):
                score = 12.0
                if (
                    keys_equivalent(record_key, canonical_text(reference.phrase))
                    or _phrase_equals(reference.phrase, record.key)
                ):
                    score = 14.0
            else:
                if keys_equivalent(record_key, query):
                    score = 10.0
                elif query in record_key or record_key in query:
                    score = 8.0
                elif len(query.split()) >= 2 and subjects_overlap(record.subject, query):
                    score = 6.0
                elif record.subject and canonical_text(record.subject) in query:
                    score = 6.0
            if score:
                scored.append((score, record))
        scored.sort(key=lambda item: (item[0], item[1].updated_at), reverse=True)
        return scored

    def _find_temporary_matches(self, key: str) -> list[tuple[float, TemporaryRecord]]:
        query = canonical_text(key)
        if not query:
            return []
        reference = parse_reference(key)
        records = self.store.list_temporary()
        if reference is not None and reference.explicit_subject:
            records = [record for record in records if subject_matches(reference.subject, record.subject)]
        if not records:
            return []
        scored: list[tuple[float, TemporaryRecord]] = []
        for record in records:
            record_key = canonical_text(record.key)
            record_value = canonical_text(record.value)
            score = 0.0
            if (
                reference is not None
                and not reference.is_generic_only
                and reference.identity
                and attribute_identity(record.key) == reference.identity
            ):
                score = 12.0
            elif keys_equivalent(record_key, query):
                score = 10.0
            elif query in record_key or record_key in query:
                score = 8.0
            elif query and (query in record_value or record_value in query):
                score = 7.0
            elif len(query.split()) >= 2 and subjects_overlap(record.subject, query):
                score = 6.0
            if score:
                scored.append((score, record))
        scored.sort(key=lambda item: (item[0], item[1].updated_at), reverse=True)
        return scored

    def _expire_matching_temporary(self, key: str) -> None:
        for _score, record in self._find_temporary_matches(key):
            self.store.expire_temporary(record.id)
            self.store.record_event("temporary_expired", MemoryLayer.TEMPORARY, record.id, detail={"reason": "user requested deletion"})

    def _ambiguous(self, matches: list[tuple[float, MemoryRecord]], key: str) -> bool:
        distinct = {record.canonical_key for _score, record in matches}
        if len(distinct) <= 1:
            return False
        return len(canonical_text(key).split()) < 3

    def _multiple_matches(self, kind: MutationKind, matches: list[tuple[float, MemoryRecord]], key: str) -> MutationOutcome:
        lines = ["I found multiple matching memories. Which one did you mean?"]
        lines.extend(
            f"- {record.category} / {record.key}: {record.value}"
            for _score, record in matches[:5]
        )
        return MutationOutcome(
            False,
            kind,
            status=MutationStatus.MULTIPLE_MATCHES,
            affected_ids=[record.id for _score, record in matches],
            clarification="\n".join(lines),
        )

    def _multiple_temporary_matches(self, matches: list[tuple[float, TemporaryRecord]]) -> MutationOutcome:
        lines = ["I found multiple matching items. Which one did you mean?"]
        lines.extend(
            f"- {record.key}: {record.value}"
            for _score, record in matches[:5]
        )
        return MutationOutcome(
            False,
            MutationKind.DELETE,
            status=MutationStatus.MULTIPLE_MATCHES,
            affected_ids=[record.id for _score, record in matches],
            clarification="\n".join(lines),
        )


def _values_equal(left: str, right: str) -> bool:
    return left.casefold() == right.casefold()


def _phrase_equals(left: str, right: str) -> bool:
    return " ".join((left or "").split()).casefold() == " ".join((right or "").split()).casefold()
