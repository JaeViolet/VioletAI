"""Application configuration kept in one place for future settings support."""

from pathlib import Path

APP_NAME = "VioletAI"
DEFAULT_MODEL_NAME = "qwen3.5:9b"
MODEL_NAME = DEFAULT_MODEL_NAME
OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
OLLAMA_BASE_URL = "http://127.0.0.1:11434"
KEEP_ALIVE = "30m"
CONNECT_TIMEOUT_SECONDS = 10
READ_TIMEOUT_SECONDS = 30

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONVERSATIONS_DIR = PROJECT_ROOT / "data" / "conversations"
PREFERENCES_PATH = PROJECT_ROOT / "data" / "preferences.json"
APP_FOOTER_TEXT = "VioletAI can make mistakes. Check important information."

from core.identity import BASE_SYSTEM_PROMPT  # noqa: E402

SYSTEM_PROMPT = BASE_SYSTEM_PROMPT
