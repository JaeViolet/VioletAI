"""Registry of tool capabilities.

available_tools() is the single source of truth the tool menu is built
from. Each entry carries a name, description, and an optional handler.
A handler is wired to the UI only once a tool is actually implemented;
until then the menu action stays disabled ("Coming soon").
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    handler: Callable[..., Any] | None = None


_TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec("Web Search", "Search the web for up-to-date information"),
    ToolSpec("Upload Files", "Attach files to the conversation"),
    ToolSpec("Upload Images", "Attach images to the conversation"),
    ToolSpec("Deep Research", "Multi-step research across sources"),
    ToolSpec("Image Generation", "Generate images from a prompt"),
)


def available_tools() -> tuple[ToolSpec, ...]:
    """Return the canonical list of tool capabilities."""
    return _TOOLS
