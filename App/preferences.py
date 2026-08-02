"""Small JSON preferences store for local UI settings."""

from __future__ import annotations

import json

from config import DEFAULT_MODEL_NAME, PREFERENCES_PATH


class Preferences:
    def __init__(self) -> None:
        self.path = PREFERENCES_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.selected_model = DEFAULT_MODEL_NAME
        self.load()

    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if isinstance(data, dict) and isinstance(data.get("selected_model"), str):
            self.selected_model = data["selected_model"]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"selected_model": self.selected_model}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
