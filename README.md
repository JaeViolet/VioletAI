# VioletAI

A private, local desktop chat assistant powered by [Ollama](https://ollama.com).

## Features

- Native desktop chat window (PySide6, Fusion style)
- Streaming responses from a local Ollama model
- Conversation history with search, pin, rename, and delete
- Model selection from locally installed Ollama models
- Memory tab for saved facts and preferences
- Theme presets with custom accent colors
- No cloud, no tracking — everything runs on your machine

## Requirements

- Python 3.11+
- [Ollama](https://ollama.com) running locally (default: `http://127.0.0.1:11434`)
- A pulled model (default: `qwen3.5:9b` — set in `core/config.py`)

## Install

```sh
pip install -r requirements.txt
```

## Run

```sh
python app/main.py
```

## Tests

```sh
$env:QT_QPA_PLATFORM="offscreen"
python -m unittest discover -s tests -p "test_*.py"
```

## Project layout

```
app/                 Application entry point
core/                Config, engine orchestration, identity, prompts
memory/              Memory bridge (interface + local SQLite backend; future Letta adapter)
models/              Ollama connection and model management
conversations/       Chat history storage
ui/                  Window, chat view, sidebar, settings, widgets, styles, icons
tools/               Tool registry (ToolSpec + available_tools())
data/                Runtime data (conversations, preferences, memory db)
tests/               Domain-split tests (no Ollama required)
```

See `docs/ARCHITECTURE.md` for details.
