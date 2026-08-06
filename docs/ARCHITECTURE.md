# VioletAI Architecture

## Purpose

VioletAI is a local-first personal AI assistant designed around continuity and growth.

The goal is to create an assistant that feels like a persistent extension of the user rather than a collection of disconnected conversations.

## Core Principles

- Local-first design
- Modular architecture
- Clear separation of responsibilities
- Replaceable components
- User-controlled data

## Structure

### Core

Responsible for VioletAI's main behavior and orchestration.

Includes:

- assistant logic
- identity and behavior
- prompt construction

### Models

Handles AI model communication.

Current support:

- Ollama local models
- model selection and management

### Memory

The memory layer provides an interface for persistent context.

It is intentionally designed as a bridge between VioletAI and future memory systems. It should not contain AI decision-making, retrieval intelligence, or personality logic.

Future integrations, such as Letta, should be replaceable without changing the rest of VioletAI.

### Conversations

Handles conversation history and storage.

### UI

Responsible for the desktop experience:

- main window
- chat interface
- sidebar
- settings
- reusable components
- themes

### Tools

Provides a foundation for future capabilities and integrations.

## Design Goal

VioletAI should grow with the user.

Future systems should enhance the same assistant identity instead of creating disconnected features.

## Current State

Completed:

- Desktop application foundation
- Local model integration
- Conversation system
- Settings and themes
- Modular project structure
- Memory interface foundation

In progress:

- Persistent AI memory
- Long-term continuity
- Expanded assistant capabilities

## Development Direction

VioletAI is designed to evolve over time. New systems should extend the assistant while preserving a consistent identity and user experience.

## Testing

Run:

```sh
$env:QT_QPA_PLATFORM="offscreen"
python -m unittest discover -s tests -p "test_*.py"
```
