# VioletAI Architecture

## Purpose

VioletAI is a local-first personal AI assistant designed around continuity and growth.

The goal is to create one persistent assistant that grows with the user instead of a collection of disconnected conversations.

## Core Principles

- Local-first design
- Modular architecture
- Clear separation of responsibilities
- Replaceable components
- User-controlled data and personalization

## Project Structure

```text
VioletAI/
│
├── app/
│   └── main.py              # Application startup
│
├── core/
│   ├── engine.py            # Main assistant orchestration
│   ├── identity.py          # Personality and behavior
│   ├── prompts.py           # Prompt construction
│   └── config.py            # Application configuration
│
├── models/
│   ├── ollama.py             # Local model connection
│   └── manager.py            # Model selection
│
├── conversations/
│   └── manager.py            # Conversation history
│
├── ui/
│   ├── window.py             # Main interface
│   ├── chat_view.py          # Chat display
│   ├── settings.py           # Settings UI
│   └── design.py             # UI styling and helpers
│
├── tools/
│   └── manager.py            # Future tool system
│
├── tests/                    # Automated tests
└── docs/                     # Project documentation
```

## System Responsibilities

### Core

The core layer represents VioletAI's behavior and orchestration.

Responsible for:

- coordinating assistant actions
- managing identity and behavior
- preparing prompts

The core should remain independent from specific UI or model implementations.

### Models

Handles communication with AI models.

Current support:

- Ollama local models
- model selection

Future model providers should be replaceable without changing the assistant architecture.

### Memory

The legacy memory implementation has been removed. The settings UI keeps a
Memory tab as a placeholder, disconnected from any backend, reserved for future
Letta memory management.

When Letta is integrated it should provide VioletAI with access to persistent
context while avoiding ownership of memory intelligence. The memory system
should not contain:

- personality logic
- retrieval decisions
- AI reasoning
- memory ranking

Letta will connect behind this placeholder.

### Conversations

Handles chat history and conversation persistence.

Conversation history is separate from long-term assistant memory.

### UI

Responsible for the desktop experience:

- main window
- chat interface
- sidebar
- settings
- themes
- reusable components

The UI should not contain assistant logic.

### Tools

Provides the foundation for future capabilities and integrations.

## Design Goal

VioletAI should evolve as one continuous assistant.

New systems should extend the same identity and experience rather than creating disconnected features.

## Current State

Completed:

- Desktop application foundation
- Local model integration
- Conversation system
- Settings and themes
- Modular project structure
- Legacy memory system removed (Memory tab kept as a placeholder)

In progress:

- Persistent AI memory
- Long-term continuity
- Expanded assistant capabilities

## Development Direction

VioletAI is designed to evolve over time. Components should be replaceable while preserving the user's experience and the assistant's identity.

## Testing

Run:

```sh
$env:QT_QPA_PLATFORM="offscreen"
python -m unittest discover -s tests -p "test_*.py"
```
