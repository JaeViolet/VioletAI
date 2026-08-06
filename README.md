# VioletAI

VioletAI is a local-first personal AI assistant designed to grow with the user over time.

The goal is not just a chatbot that answers questions. The goal is a persistent assistant that maintains continuity, understands the user, and evolves through interaction.

## Vision

VioletAI aims to feel like an extension of the user:

- remembers important context
- understands ongoing projects
- adapts to preferences
- maintains continuity across conversations
- keeps the user in control

## Current Features

- Desktop AI interface built with PySide6
- Local AI models through Ollama
- Model selection and management
- Conversation history
- Settings and themes
- Modular architecture for future capabilities

## Architecture

VioletAI is separated into independent systems:

- **Core** — assistant orchestration, identity, and prompts
- **Models** — AI model connections and management
- **Memory** — memory interface designed for future backends
- **Conversations** — chat history management
- **UI** — desktop experience
- **Tools** — future capabilities

## Requirements

- Python 3.11+
- Ollama running locally
- A local model installed

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

## Project Status

VioletAI is actively evolving. The current foundation focuses on clean architecture, local AI, and creating a platform for persistent personal intelligence.

Future goals:

- persistent AI memory
- deeper personalization
- tool usage
- continuous improvement

See `docs/ARCHITECTURE.md` for details.
