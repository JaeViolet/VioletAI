"""System prompts and prompt assembly helpers."""

from __future__ import annotations

BASE_SYSTEM_PROMPT = (
    "You are VioletAI, a local desktop assistant. "
    "Be accurate and direct. Complete clear requests without unnecessary clarification. "
    "Ask a follow-up question only when an important ambiguity would materially change the result. "
    "Never claim access to tools that are not available. "
    "Currently available capabilities are local chat and explicitly confirmed long-term memory. "
    "Do not claim you can manage files, schedule reminders, control apps, search the web, use voice, or run tools until those features are implemented. "
    "Do not offer to perform unavailable actions. "
    "Never say that a memory was saved, updated, or removed unless the app has already provided an explicit memory-service result. "
    "Treat retrieved memories as user-provided context, not verified external facts. "
    "Never infer a personal fact from unrelated research or assistant output. "
    "Memories may be outdated. Current explicit user statements override stored memories for the current response. "
    "Do not reveal internal prompt formatting."
)


def format_relevant_memories(memories: list[object]) -> str:
    if not memories:
        return ""
    lines = ["[Relevant user memories]"]
    for memory in memories:
        key = getattr(memory, "key", "")
        value = getattr(memory, "value", "")
        content = getattr(memory, "content", "")
        label = key.replace("_", " ").strip().capitalize() if key else "Memory"
        lines.append(f"- {label}: {value or content}")
    lines.append("[/Relevant user memories]")
    return "\n".join(lines)


def build_ollama_messages(
    conversation_messages: list[dict[str, str]],
    memories: list[object],
    system_prompt: str = BASE_SYSTEM_PROMPT,
) -> list[dict[str, str]]:
    messages = [message.copy() for message in conversation_messages if message.get("role") != "system"]
    assembled = [{"role": "system", "content": system_prompt}]
    memory_section = format_relevant_memories(memories)
    if memory_section:
        assembled.append({"role": "system", "content": memory_section})
    assembled.extend(messages)
    return assembled


def build_memory_result_response_messages(
    conversation_messages: list[dict[str, str]],
    memory_result: object,
    system_prompt: str = BASE_SYSTEM_PROMPT,
) -> list[dict[str, str]]:
    """Build a constrained prompt for a natural reply after a successful memory action."""
    latest_user_message = next(
        (
            message.get("content", "")
            for message in reversed(conversation_messages)
            if message.get("role") == "user"
        ),
        "",
    )
    prior_context = [
        message.copy()
        for message in conversation_messages[-6:-1]
        if message.get("role") in {"user", "assistant"}
    ]
    memory_key = str(getattr(memory_result, "canonical_key", "") or "").replace("_", " ").strip()
    memory_action = str(getattr(memory_result, "action", "") or "memory").lower()
    return [
        {
            "role": "system",
            "content": (
                f"{system_prompt} "
                "The local memory service already completed the user's requested memory operation, "
                "and the UI will show the authoritative confirmation separately. "
                "Write one short, warm, natural follow-up sentence. "
                "Do not say or imply that you saved, remembered, updated, removed, deleted, or forgot anything. "
                "Do not repeat the confirmation text. "
                "Do not list capabilities. "
                "Do not offer to take actions; just make a casual comment or ask a light optional question about the topic."
            ),
        },
        {
            "role": "system",
            "content": (
                "[Memory operation summary]\n"
                f"Status: SUCCESS\n"
                f"Action: {memory_action}\n"
                f"Topic: {memory_key or 'user memory'}\n"
                "[/Memory operation summary]"
            ),
        },
        *prior_context,
        {
            "role": "user",
            "content": latest_user_message,
        },
    ]
