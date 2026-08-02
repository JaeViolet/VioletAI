"""Application configuration kept in one place for future settings support."""

from pathlib import Path

APP_NAME = "AI Agent"
MODEL_NAME = "qwen3.5:9b"
OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
KEEP_ALIVE = "30m"
CONNECT_TIMEOUT_SECONDS = 10
READ_TIMEOUT_SECONDS = 600

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONVERSATIONS_DIR = PROJECT_ROOT / "memory" / "conversations"

SYSTEM_PROMPT = (
    "You are a helpful local desktop AI assistant. "
    "Be clear, accurate, concise, and practical."
)
