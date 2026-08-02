"""JSON persistence for local chat conversations."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from config import CONVERSATIONS_DIR, MODEL_NAME


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class Conversation:
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    model: str = MODEL_NAME
    messages: list[dict[str, str]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "Conversation":
        messages = data.get("messages", [])
        if not isinstance(messages, list):
            messages = []
        return cls(
            id=str(data.get("id") or uuid.uuid4().hex),
            created_at=str(data.get("created_at") or utc_now()),
            updated_at=str(data.get("updated_at") or utc_now()),
            model=str(data.get("model") or MODEL_NAME),
            messages=[
                {"role": str(item.get("role", "")), "content": str(item.get("content", ""))}
                for item in messages
                if isinstance(item, dict)
                and item.get("role") in {"system", "user", "assistant"}
            ],
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "model": self.model,
            "messages": self.messages,
        }


class ConversationStore:
    def __init__(self, directory: Path = CONVERSATIONS_DIR) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)

    def create(self, system_prompt: str) -> Conversation:
        return Conversation(messages=[{"role": "system", "content": system_prompt}])

    def save(self, conversation: Conversation) -> None:
        conversation.updated_at = utc_now()
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.path_for(conversation.id)
        temp_path = path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(conversation.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(path)

    def load_latest(self) -> Conversation | None:
        conversations: list[Conversation] = []
        for path in self.directory.glob("*.json"):
            conversation = self.load(path)
            if conversation is not None:
                conversations.append(conversation)
        if not conversations:
            return None
        return max(conversations, key=lambda item: item.updated_at)

    def load(self, path: Path) -> Conversation | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        return Conversation.from_dict(data)

    def path_for(self, conversation_id: str) -> Path:
        return self.directory / f"{conversation_id}.json"
