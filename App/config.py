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
POST_MEMORY_READ_TIMEOUT_SECONDS = 15
AUTOMATIC_MEMORY_CLASSIFIER_TIMEOUT_SECONDS = 2.5
AUTOMATIC_MEMORY_CONFIDENCE_THRESHOLD = 0.86

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONVERSATIONS_DIR = PROJECT_ROOT / "memory" / "conversations"
PREFERENCES_PATH = PROJECT_ROOT / "memory" / "preferences.json"
MEMORY_DB_PATH = PROJECT_ROOT / "memory" / "memory.db"
LOGS_DIR = PROJECT_ROOT / "logs"
MEMORY_LOG_PATH = LOGS_DIR / "memory.log"
APP_FOOTER_TEXT = "VioletAI can make mistakes. Check important information."

from prompts import BASE_SYSTEM_PROMPT  # noqa: E402

SYSTEM_PROMPT = BASE_SYSTEM_PROMPT
