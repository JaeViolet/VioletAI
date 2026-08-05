"""Small JSON preferences store for local UI settings."""

from __future__ import annotations

import json
import re

from config import DEFAULT_MODEL_NAME, PREFERENCES_PATH
from themes import DEFAULT_ACCENT, DEFAULT_THEME_NAME

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _is_hex(value: object) -> bool:
    return isinstance(value, str) and bool(_HEX_RE.match(value))


class Preferences:
    def __init__(self) -> None:
        self.path = PREFERENCES_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.selected_model = DEFAULT_MODEL_NAME
        self.theme_name = DEFAULT_THEME_NAME
        self.theme_accent = DEFAULT_ACCENT
        self.custom_themes: list[dict] = []
        self.load()

    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if isinstance(data, dict) and isinstance(data.get("selected_model"), str):
            self.selected_model = data["selected_model"]
        if isinstance(data, dict) and isinstance(data.get("theme_name"), str):
            self.theme_name = data["theme_name"]
        if isinstance(data, dict) and _is_hex(data.get("theme_accent")):
            self.theme_accent = data["theme_accent"]
        custom = data.get("custom_themes") if isinstance(data, dict) else None
        if isinstance(custom, list):
            cleaned = []
            for item in custom:
                if (
                    isinstance(item, dict)
                    and isinstance(item.get("name"), str)
                    and isinstance(item.get("accent"), str)
                ):
                    cleaned.append({"name": item["name"], "accent": item["accent"]})
            self.custom_themes = cleaned

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {
                    "selected_model": self.selected_model,
                    "theme_name": self.theme_name,
                    "theme_accent": self.theme_accent,
                    "custom_themes": self.custom_themes,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
