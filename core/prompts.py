"""System prompts and prompt assembly helpers."""

from __future__ import annotations

from core.identity import BASE_SYSTEM_PROMPT


def build_ollama_messages(
    conversation_messages: list[dict[str, str]],
    system_prompt: str = BASE_SYSTEM_PROMPT,
) -> list[dict[str, str]]:
    messages = [message.copy() for message in conversation_messages if message.get("role") != "system"]
    return [{"role": "system", "content": system_prompt}, *messages]
