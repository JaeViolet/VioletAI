"""Application configuration kept in one place for future settings support."""

from pathlib import Path

APP_NAME = "VioletAI"
DEFAULT_MODEL_NAME = "qwen3.5:9b"
MODEL_NAME = DEFAULT_MODEL_NAME
OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
OLLAMA_BASE_URL = "http://127.0.0.1:11434"
KEEP_ALIVE = "30m"
CONNECT_TIMEOUT_SECONDS = 10
READ_TIMEOUT_SECONDS = 600

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONVERSATIONS_DIR = PROJECT_ROOT / "memory" / "conversations"
PREFERENCES_PATH = PROJECT_ROOT / "memory" / "preferences.json"
APP_FOOTER_TEXT = "VioletAI can make mistakes. Check important information."

SYSTEM_PROMPT = (
    "You are a helpful local desktop AI assistant. "
    "Be clear, accurate, concise, and practical."
)
