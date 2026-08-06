# VioletAI Architecture

VioletAI is a local desktop chat assistant. The UI is built with PySide6, the
backend talks to a locally running [Ollama](https://ollama.com) server, and all
state is stored as plain files under `data/`.

## Package layout

| Path | Purpose |
| --- | --- |
| `app/main.py` | Entry point. Builds the `QApplication`, creates `MainWindow`, runs the event loop. |
| `core/config.py` | Central configuration: app name, Ollama endpoints, timeouts, data paths, `SYSTEM_PROMPT`. |
| `core/engine.py` | `Engine` — chat orchestration. Owns a `QThread` + `OllamaWorker` per request and re-emits worker signals for the UI. Also re-exports `ModelManager`. |
| `core/identity.py` | Personality. Contains `BASE_SYSTEM_PROMPT`, the assistant's behavioral identity. |
| `core/prompts.py` | Prompt assembly. `build_ollama_messages()` produces the final message list sent to Ollama. |
| `memory/manager.py` | Memory bridge. Defines the `MemoryBackend` interface (`save`, `get`, `search`, `delete`, `archive`, `restore`, `clear`) with `LocalMemoryBackend` (SQLite) and a `MemoryManager` facade. Deliberately an interface, not intelligence — the future home of a Letta adapter. |
| `models/ollama.py` | Low-level Ollama client. `OllamaWorker` streams a chat request off the UI thread; `iter_message_chunks`, `parse_stream_line`, and the error hierarchy. |
| `models/manager.py` | Model discovery. `discover_models()`, `ModelDiscoveryWorker`, and `ModelManager` (threaded wrapper). |
| `conversations/manager.py` | Chat history. `ConversationStore` persists conversations as JSON files (grouping, search, pin, rename, delete). |
| `ui/window.py` | `MainWindow` + `ConfirmBackdrop`. Composition and coordination: sidebar, chat view, composer modes, overlays, engine wiring. |
| `ui/chat_view.py` | `ChatView` — message rendering, scrolling, and streaming display (`viewport_resized`/`regenerate_requested` signals). |
| `ui/sidebar.py` | `ChatSidebar` + `SearchOverlay`. |
| `ui/settings.py` | `SettingsOverlay` — settings page, theme page, and the memory tab. |
| `ui/widgets.py` | Reusable widgets: `AutoGrowingInput`, `MessageBubble`, `MarkdownView`, `CodeBlock`, `MessageActions`, `ThinkingBubble`, `ModelSelector`. |
| `ui/design.py` | Visual tokens: colors, `Motion`, and PNG icon helpers (`asset_icon_path`, `icon`). |
| `ui/styles.py` | QSS application stylesheet. `app_stylesheet()` plus `lighten`/`darken` color helpers. |
| `ui/themes.py` | Theme presets and accent colors. |
| `ui/icons/` | PNG icon assets used by buttons and message actions. |
| `ui/preferences.py` | `Preferences` — persisted user settings (selected model, theme, accent). |
| `tools/manager.py` | Tool registry. `ToolSpec(name, description, handler)` dataclass and `available_tools()`; unimplemented tools render disabled ("Coming soon"). |

## Runtime data

`data/` is gitignored and holds:

- `data/conversations/*.json` — one JSON file per conversation.
- `data/preferences.json` — user preferences.
- `data/memory.db` — SQLite memory store.

Paths are resolved from `core/config.py` (`PROJECT_ROOT` → `data/`).

## Request flow

1. User sends a message; `MainWindow.send_message()` appends it and calls
   `_start_generation()`.
2. `_start_generation()` builds Ollama messages via
   `core.prompts.build_ollama_messages()` and calls `self.engine.start(...)`.
3. `Engine` creates an `OllamaWorker` on a `QThread`; the worker streams
   chunks from Ollama.
4. Worker signals are re-emitted by `Engine` to `MainWindow`, which delegates
   to `ChatView` for the streaming bubble, finalizes the answer, and persists
   the conversation.
5. On completion the thread is cleaned up and the controls re-enable.

Model discovery (`ModelManager`) runs the same way: a `ModelDiscoveryWorker`
fetches `/api/tags` off the UI thread and updates the model selectors.

## Concurrency notes

- `Engine` and `ModelManager` own their worker threads. The UI only observes
  their signals, so widgets are never touched from worker threads.
- `closeEvent` calls `engine.shutdown()` (cancel + join with timeout); if the
  worker does not stop in time, the window defers closing until the thread
  finishes.

## Testing

The suite runs without an Ollama server: HTTP calls are mocked, and a temporary
`ConversationStore`/`LocalMemoryBackend` is injected via `patch` on
`ui.window`. Tests are split by domain in `tests/` (`test_core.py`,
`test_ollama.py`, `test_chat_view.py`, `test_composer.py`, `test_sidebar.py`,
`test_settings.py`) with a shared harness in `tests/base.py`. Run:

```sh
$env:QT_QPA_PLATFORM="offscreen"
python -m unittest discover -s tests -p "test_*.py"
```
