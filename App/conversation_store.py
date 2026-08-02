"""JSON persistence for local chat conversations."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from config import CONVERSATIONS_DIR, DEFAULT_MODEL_NAME


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def parse_timestamp(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)


def title_from_first_user_message(messages: list[dict[str, str]]) -> str:
    for message in messages:
        if message.get("role") == "user":
            text = re.sub(r"\s+", " ", message.get("content", "")).strip()
            if not text:
                break
            return text[:54].rstrip() + ("..." if len(text) > 54 else "")
    return "New chat"


@dataclass
class Conversation:
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    title: str = "New chat"
    model: str = DEFAULT_MODEL_NAME
    messages: list[dict[str, str]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "Conversation":
        messages = data.get("messages", [])
        if not isinstance(messages, list):
            messages = []
        cleaned_messages = [
            {"role": str(item.get("role", "")), "content": str(item.get("content", ""))}
            for item in messages
            if isinstance(item, dict)
            and item.get("role") in {"system", "user", "assistant"}
        ]
        title = str(data.get("title") or "").strip() or title_from_first_user_message(
            cleaned_messages
        )
        return cls(
            id=str(data.get("id") or uuid.uuid4().hex),
            created_at=str(data.get("created_at") or utc_now()),
            updated_at=str(data.get("updated_at") or utc_now()),
            title=title,
            model=str(data.get("model") or DEFAULT_MODEL_NAME),
            messages=cleaned_messages,
        )

    def refresh_title(self) -> None:
        if self.title == "New chat":
            self.title = title_from_first_user_message(self.messages)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "title": self.title,
            "model": self.model,
            "messages": self.messages,
        }


class ConversationStore:
    def __init__(self, directory: Path = CONVERSATIONS_DIR) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)

    def create(self, system_prompt: str, model: str = DEFAULT_MODEL_NAME) -> Conversation:
        return Conversation(
            model=model,
            messages=[{"role": "system", "content": system_prompt}],
        )

    def save(self, conversation: Conversation) -> None:
        conversation.refresh_title()
        conversation.updated_at = utc_now()
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.path_for(conversation.id)
        temp_path = path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(conversation.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(path)

    def list_conversations(self) -> list[Conversation]:
        conversations: list[Conversation] = []
        for path in self.directory.glob("*.json"):
            conversation = self.load(path)
            if conversation is not None:
                conversations.append(conversation)
        return sorted(
            conversations,
            key=lambda item: parse_timestamp(item.updated_at),
            reverse=True,
        )

    def search(self, query: str) -> list[Conversation]:
        normalized = query.casefold().strip()
        conversations = self.list_conversations()
        if not normalized:
            return conversations
        return [
            conversation
            for conversation in conversations
            if normalized in conversation.title.casefold()
            or any(normalized in message.get("content", "").casefold() for message in conversation.messages)
        ]

    def grouped(self, query: str = "") -> dict[str, list[Conversation]]:
        groups = {
            "Today": [],
            "Yesterday": [],
            "Previous 7 days": [],
            "Older": [],
        }
        now = datetime.now(UTC)
        today = now.date()
        for conversation in self.search(query):
            updated = parse_timestamp(conversation.updated_at)
            day = updated.date()
            if day == today:
                groups["Today"].append(conversation)
            elif day == today - timedelta(days=1):
                groups["Yesterday"].append(conversation)
            elif updated >= now - timedelta(days=7):
                groups["Previous 7 days"].append(conversation)
            else:
                groups["Older"].append(conversation)
        return groups

    def load_latest(self) -> Conversation | None:
        conversations = self.list_conversations()
        return conversations[0] if conversations else None

    def load_by_id(self, conversation_id: str) -> Conversation | None:
        path = self.path_for(conversation_id)
        if not path.exists():
            return None
        return self.load(path)

    def rename(self, conversation_id: str, title: str) -> Conversation | None:
        conversation = self.load_by_id(conversation_id)
        if conversation is None:
            return None
        conversation.title = title.strip() or "New chat"
        self.save(conversation)
        return conversation

    def delete(self, conversation_id: str) -> bool:
        path = self.path_for(conversation_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    def load(self, path: Path) -> Conversation | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        return Conversation.from_dict(data)

    def path_for(self, conversation_id: str) -> Path:
        safe_id = "".join(ch for ch in conversation_id if ch.isalnum() or ch in "-_")
        return self.directory / f"{safe_id}.json"
