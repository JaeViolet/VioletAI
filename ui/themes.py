"""Curated color themes for VioletAI."""

BUILTIN_THEMES = [
    {"name": "Violet", "accent": "#8b5cf6"},
    {"name": "Indigo", "accent": "#6366f1"},
    {"name": "Ocean", "accent": "#0ea5e9"},
    {"name": "Emerald", "accent": "#10b981"},
    {"name": "Lime", "accent": "#a3e635"},
    {"name": "Amber", "accent": "#f59e0b"},
    {"name": "Rose", "accent": "#f43f5e"},
    {"name": "Crimson", "accent": "#ef4444"},
    {"name": "Slate", "accent": "#64748b"},
]

DEFAULT_THEME_NAME = "Violet"
DEFAULT_ACCENT = "#8b5cf6"


def is_builtin(name: str) -> bool:
    return any(theme["name"] == name for theme in BUILTIN_THEMES)
