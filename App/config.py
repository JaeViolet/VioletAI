"""Application configuration kept in one place for future settings support."""

APP_NAME = "AI Agent"
MODEL_NAME = "qwen3.5:9b"
OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
KEEP_ALIVE = "30m"

SYSTEM_PROMPT = (
    "You are a helpful local desktop AI assistant. "
    "Be clear, accurate, concise, and practical."
)

