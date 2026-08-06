# VioletAI Developer Handoff

Version: v0.2 Pre-Release  
Last Updated: August 2026  
Status: Living document  
Branch scope: `Memory-V2`

## Read This First

This document is the authoritative technical reference for VioletAI.

Before modifying the project:

1. Read this document completely.
2. Inspect the relevant source files before editing.
3. Preserve existing architecture unless the user explicitly requests a redesign.
4. Prefer small, reversible changes over broad rewrites.
5. Add regression tests for every bug fix.
6. Never claim a feature works without verification.

When this document and the implementation disagree, inspect the current code and explain the discrepancy before making changes.

## Memory-V2 Branch Authority

This branch exists specifically to replace VioletAI's memory architecture. For memory-related work, this section overrides any conflicting instruction elsewhere in this handoff.

On `Memory-V2`, the coding agent is authorized to:

- Replace the existing memory subsystem completely.
- Delete, rename, redesign, or replace memory-related files, classes, schemas, prompts, diagnostics, UI integration, and tests.
- Introduce a new storage schema, hybrid RAG pipeline, temporary cross-chat memory, durable memory, provenance, temporal reasoning, consolidation, and migration strategy.
- Remove legacy memory paths after their replacements are implemented and verified.
- Make broad architectural changes inside the memory subsystem when a clean final design requires them.

The agent must not preserve legacy memory behavior merely because older sections describe it. Those sections are historical context until Memory V2 replaces them.

All non-memory constraints remain in force. Do not redesign unrelated chat rendering, composer behavior, Ollama streaming, conversation persistence, sidebar, themes, or general Settings behavior unless a narrowly required integration change is necessary.

Memory V2 must end with one authoritative pipeline, not parallel legacy and replacement systems. Build replacements safely, test them, switch integrations, then remove obsolete memory code.

## Project Goals

VioletAI aims to be a private, local-first AI assistant that combines:

- Natural conversation
- Reliable long-term memory
- Modern native desktop UI
- Local LLM execution
- Deterministic application behavior
- Extensible architecture

Every architectural decision should support one or more of these goals.

## Table of Contents

- [Read This First](#read-this-first)
- [Memory-V2 Branch Authority](#memory-v2-branch-authority)
- [Project Goals](#project-goals)
- [1. Overall Architecture](#1-overall-architecture)
- [2. File-by-File Explanation](#2-file-by-file-explanation)
  - [App/config.py](#appconfigpy)
  - [App/conversation_store.py](#appconversation_storepy)
  - [App/design.py](#appdesignpy)
  - [App/main.py](#appmainpy)
  - [App/memory_manager.py](#appmemory_managerpy)
  - [App/memory_v2/models.py](#appmemory_v2modelspy)
  - [App/memory_v2/normalize.py](#appmemory_v2normalizepy)
  - [App/memory_v2/embeddings.py](#appmemory_v2embeddingspy)
  - [App/memory_v2/attributes.py](#appmemory_v2attributespy)
  - [App/memory_v2/semantic.py](#appmemory_v2semanticpy)
  - [App/memory_v2/store.py](#appmemory_v2storepy)
  - [App/memory_v2/extract.py](#appmemory_v2extractpy)
  - [App/memory_v2/operations.py](#appmemory_v2operationspy)
  - [App/memory_v2/retrieval.py](#appmemory_v2retrievalpy)
  - [App/memory_v2/temporary.py](#appmemory_v2temporarypy)
  - [App/memory_v2/consolidation.py](#appmemory_v2consolidationpy)
  - [App/memory_v2/pipeline.py](#appmemory_v2pipelinepy)
  - [App/ollama_client.py](#appollama_clientpy)
  - [App/preferences.py](#apppreferencespy)
  - [App/prompts.py](#apppromptspy)
  - [App/sidebar.py](#appsidebarpy)
  - [App/widgets.py](#appwidgetspy)
- [3. Memory System](#3-memory-system)
- [4. Request Lifecycle](#4-request-lifecycle)
- [5. Current Project Philosophy](#5-current-project-philosophy)
- [6. Current State Before v0.2](#6-current-state-before-v02)
- [7. Coding Conventions](#7-coding-conventions)
- [8. Testing](#8-testing)
- [9. Future Roadmap](#9-future-roadmap)
- [Repository Evolution](#repository-evolution)

## 1. Overall Architecture

VioletAI is a native Windows desktop AI assistant built with Python, PySide6, local Ollama, local JSON conversation storage, and local SQLite long-term memory.

At a high level:

```mermaid
flowchart TD
    User["User types in composer"] --> UI["MainWindow / Qt UI thread"]
    UI --> SaveUser["Render + save user message"]
    SaveUser --> PrepThread["RequestPreparationWorker on QThread"]

    PrepThread --> MemorySystem["MemorySystem (memory_v2.pipeline)"]
    MemorySystem --> Extractor["Extractor"]
    MemorySystem --> Operations["Operations"]
    MemorySystem --> Store["MemoryStore SQLite"]
    MemorySystem --> Retriever["Hybrid retrieval"]
    MemorySystem --> Temporary["TemporaryMemory lifecycle"]
    MemorySystem --> Consolidator["Consolidator"]

    PrepThread --> Prompt["Prompt assembly"]
    Prompt --> OllamaThread["OllamaWorker on QThread"]
    OllamaThread --> Ollama["Ollama /api/chat stream=true"]

    Ollama --> Stream["NDJSON stream parser"]
    Stream --> Signals["Qt signals"]
    Signals --> UI

    UI --> Render["Incremental assistant rendering"]
    Render --> Persist["ConversationStore JSON"]
```

Application startup:

- Entry point is `main()` in [App/main.py](../App/main.py).
- It creates a `QApplication`, applies dark styling, builds `MainWindow`, then starts the Qt event loop.
- `MainWindow.__init__()` loads preferences, selected model, conversation store, memory store + `MemorySystem`, creates a new conversation, builds the interface, refreshes model list in the background, rebuilds the sidebar/messages, and focuses the composer.
- Current code intentionally opens to a new chat at startup, not Settings and not the latest conversation.

Major subsystems:

- UI shell: [App/main.py](../App/main.py), [App/widgets.py](../App/widgets.py), [App/sidebar.py](../App/sidebar.py), [App/memory_manager.py](../App/memory_manager.py), [App/design.py](../App/design.py)
- Ollama streaming: [App/ollama_client.py](../App/ollama_client.py)
- Prompt construction: [App/prompts.py](../App/prompts.py)
- Conversations: [App/conversation_store.py](../App/conversation_store.py)
- Preferences: [App/preferences.py](../App/preferences.py)
- Long-term memory: [App/memory_v2/pipeline.py](../App/memory_v2/pipeline.py), [App/memory_v2/store.py](../App/memory_v2/store.py), [App/memory_v2/extract.py](../App/memory_v2/extract.py), [App/memory_v2/operations.py](../App/memory_v2/operations.py), [App/memory_v2/retrieval.py](../App/memory_v2/retrieval.py), [App/memory_v2/temporary.py](../App/memory_v2/temporary.py), [App/memory_v2/consolidation.py](../App/memory_v2/consolidation.py), [App/memory_v2/models.py](../App/memory_v2/models.py)

Threading model:

- Qt widgets are created and mutated only on the UI thread.
- Memory analysis/retrieval/prompt construction runs in `RequestPreparationWorker` on a `QThread`.
- Ollama streaming runs in `OllamaWorker` on a `QThread`.
- Model discovery runs in `ModelDiscoveryWorker` on a `QThread`.
- Workers communicate back via Qt signals: `finished`, `failed`, `stopped`, `chunk_received`, `cancelled`, etc.
- UI updates happen in slots connected to those signals.

Persistence:

- Conversations are JSON files under `memory/conversations/`, managed by `ConversationStore`.
- Empty conversations are not saved.
- Long-term memories are SQLite records in `memory/memory.db`, managed only through `MemoryStore` and the `MemorySystem` facade.
- Preferences live in `memory/preferences.json`.

## 2. File-by-File Explanation

### App/config.py

Purpose: central configuration.

Responsibilities:

- App name, default model, Ollama URLs, keep-alive, timeouts.
- Project paths for conversations, preferences, memory DB, logs.
- Footer text and system prompt import.

Important constraints:

- Keep runtime paths centralized here.
- Do not scatter Ollama endpoint/timeouts across modules.
- Changing timeout constants affects worker behavior and diagnostics.

Subsystem: configuration.

### App/conversation_store.py

Purpose: JSON conversation persistence.

Responsibilities:

- `Conversation` dataclass.
- Stable conversation IDs.
- Unicode-safe JSON read/write.
- Conversation title derived from first user message.
- Grouping by Pinned, Today, Yesterday, Previous 7 days, Older.
- Search, rename, pin, delete.

Important constraints:

- `save()` intentionally skips empty chats.
- Writes use temp file then replace for safer persistence.
- Only `system`, `user`, and `assistant` messages are restored.

Subsystem: conversation architecture.

### App/design.py

Purpose: centralized theme, colors, spacing, icons, and stylesheet.

Responsibilities:

- `Colors`, `Radius`, `Spacing`, `Motion`.
- PNG/SVG icon helpers.
- Global `app_stylesheet()`.

Important constraints:

- UI polish should generally flow through this file instead of one-off styles.
- Preserve dark, minimal ChatGPT-like aesthetic.
- Avoid style changes during non-UI bug fixes.

Subsystem: UI design.

### App/main.py

Purpose: primary application coordinator.

Important classes:

- `PreparedRequest`
- `RequestPreparationWorker`
- `MainWindow`

Main responsibilities:

- Build main UI.
- Own conversation state.
- Render messages.
- Start request preparation.
- Run memory extraction/retrieval through the single `MemorySystem` facade.
- Start Ollama generation for every user turn (single visible response path).
- Handle streaming chunks.
- Finalize responses.
- Manage cancellation/Stop.
- Manage model discovery and model switching.
- Coordinate sidebar/search/settings overlays.
- Keep Qt widget mutation on UI thread.

Important architectural decisions:

- Request preparation runs before Ollama generation and is offloaded to avoid UI blocking.
- Every user message follows exactly one response path: preparation (which may save/retrieve memory invisibly) followed by a single conversational-model stream. There is no separate post-memory response path.
- Memory operates automatically and invisibly; the app never renders memory confirmations.
- `_receive_prepared_request()` records mutation details to the memory log when
  `VIOLETAI_MEMORY_LOG=1` (opt-in, off in tests), then starts generation.
- The regenerate path passes the current temporary counters to `retrieve()` so
  reinforcement stays consistent.
- Response ownership is guarded by `_finalized_current_response` so multiple paths do not append the same assistant message.
- Streaming is throttled through a render timer so every chunk does not force full layout work.
- Composer resizing uses deferred/stable event-loop updates to avoid layout feedback loops.

Mixed responsibility note:

- This is the largest file and contains UI composition, request lifecycle, worker wiring, streaming finalization, and some performance logic. It may eventually deserve careful extraction, but not during focused bug fixes.

Subsystem: application coordination.

### App/memory_manager.py

Purpose: Settings overlay and Memory Manager UI.

Important classes:

- `SettingsOverlay`
- `MemoryRow`

Responsibilities:

- Settings tab layout.
- Memory search/filter/sort.
- Edit, archive/restore, delete, clear memories.

Important constraints:

- Settings should start hidden and refresh only when visible.
- Manual edits go through `MemoryStore.update_memory(...)` (manual provenance).
- Memory Manager must preserve categories and active/archive filtering.
- UI layer only — all logic lives in `MemoryStore`/`MemorySystem`.

Subsystem: settings/memory UI.

### App/memory_v2/models.py

Purpose: typed data models for Memory V2.

Important objects:

- `MemoryLayer` (durable / temporary / archived)
- `MemoryCategory` + `CATEGORIES`
- `ProvenanceKind` (explicit / automatic / imported / restored)
- `MutationKind`, `MutationStatus`
- `ParsedMemory`, `MemoryRecord`, `TemporaryRecord`
- `Episode`
- `Provenance`
- `RankedMemory`, `RetrievalOutcome`
- `MemoryCommand`, `TurnAnalysis`, `TurnOutcome`

Important constraints:

- Category names are part of schema/UI behavior.
- Changing fields affects store, operations, retrieval, UI, and tests.
- `ParsedMemory`/`MemoryRecord` carry structured attribute fields (`attribute`,
  `attribute_core`, `statement`, `aliases`, `related_entities`, `qualifiers`,
  `valid_from`, `valid_to`) used by retrieval and prompt injection.
- `TemporaryRecord.layer` is a property that always returns `TEMPORARY`, so
  prompt/formatting code can treat records uniformly.

Subsystem: memory models.

### App/memory_v2/normalize.py

Purpose: deterministic text/key normalization.

Responsibilities:

- Clean values (trim punctuation, collapse whitespace).
- Canonicalize keys (casefold, remove filler words) so synonyms land on one slot.
- Build canonical keys from category/subject/key.

Important constraints:

- Pure, deterministic, dependency-free.
- Must be stable so retrieval and consolidation agree.

Subsystem: memory normalization.

### App/memory_v2/embeddings.py

Purpose: local deterministic embeddings for retrieval.

Responsibilities:

- Build hashed n-gram feature vectors.
- Compute cosine similarity.
- Provide synonym-aware token mapping.

Important constraints:

- Local-only and dependency-light.
- Deterministic: the same input always yields the same vector.
- Approximate but predictable; exact meaning still wins over loose similarity.
- This is the concrete backend wrapped by `semantic.py`; keep the module-level
  functions stable because tests import them directly.

Subsystem: memory retrieval.

### App/memory_v2/attributes.py

Purpose: deterministic attribute ontology for precision matching.

Responsibilities:

- `GENERIC_MODIFIERS` (favorite/favourite/fav/fave/preferred/current/old/new/
  main/primary/best/top/personal) that must never bridge unrelated attributes.
- `attribute_identity(phrase)` maps attribute phrases to canonical cores
  (job/profession/career/role -> occupation, mobile/cell/telephone -> phone
  number, residence/house/home -> home address, favourite colour -> color, etc.).
- `parse_reference(text)` -> `AttributeReference(subject, phrase, identity,
  is_generic_only, explicit_subject, raw)`.
- `references_record`, `subject_matches`, `is_user_subject`.

Important constraints:

- Pure, deterministic, dependency-free.
- Generic-only references (e.g. "forget my favorite") have an empty identity
  and may not bridge attributes.
- Retrieval and operations use these to stop "what is my job" from matching
  unrelated attributes and to keep person-scoped facts separated.

Subsystem: memory retrieval.

### App/memory_v2/semantic.py

Purpose: semantic embedding abstraction with a safe default.

Responsibilities:

- `SemanticEmbedder` protocol (embed_text / embed_key_value / cosine_similarity /
  available / name).
- `LocalHashSemanticEmbedder` — the deterministic offline default and fallback.
- `OllamaSemanticEmbedder` — optional remote `/api/embed` client that degrades
  to the local embedder on any failure (network, timeout, malformed response).

Important constraints:

- Dense remote vectors use integer-index keys so the existing sparse cosine
  computes correct dense cosine.
- A single embedder must be used for both query and record embeddings.
- Retrieval never depends on the network; the local embedder is always used
  unless an embedder is explicitly injected.

Subsystem: memory retrieval.

### App/memory_v2/store.py

Purpose: SQLite persistence for Memory V2.

Responsibilities:

- `SCHEMA_VERSION = 3` with `v2_memories`, `v2_temporary_memories`,
  `v2_memory_events`, `v2_conversation_trackers`, `v2_episodes`,
  `v2_global_state`, and an optional FTS5 virtual table `v2_memories_fts`
  (created with content-sync triggers when FTS5 is available).
- WAL mode.
- Migrate from legacy: back up and drop the legacy `memories` table.
- Migrate v2 -> v3: add structured attribute columns (`attribute`,
  `attribute_core`, `statement`, `aliases`, `related_entities`, `qualifiers`,
  `valid_from`, `valid_to`) and backfill `attribute_core` via
  `attribute_identity`.
- Insert/get/list/search/update/archive/restore/delete durable memories.
- Insert/list/touch/update/expire/delete temporary memories.
- Record auditable mutation events.
- Track conversation activity and token counters.
- Persist global counters (`token_counter`, `conversation_index`,
  `last_conversation_id`) and episodes.
- Link supersede pairs and group by canonical key.

Important constraints:

- Store stays deterministic and dumb; application logic lives in operations/pipeline.
- Do not bypass it with raw SQL elsewhere.
- Runtime DB must not be committed.

Subsystem: memory persistence.

### App/memory_v2/extract.py

Purpose: deterministic intent + fact extraction from the user message.

Important class:

- `Extractor`

Responsibilities:

- Detect explicit create/update/delete commands.
- Resolve contextual commands (“Remember that.”) against the previous user message.
- Extract durable personal facts (“my favorite color is purple”).
- Extract temporary facts/tasks (“I'm working on X”, “remember to Y”).
- Detect questions about memory (read-only) and suppression statements.
- Decide when retrieval should run (threshold-gated vs `include_all` for meta questions).

Important constraints:

- It interprets and classifies only; it never writes.
- No LLM dependency — fully deterministic and testable.
- Must be conservative: ordinary conversation must not become memory writes.

Subsystem: memory analysis.

### App/memory_v2/operations.py

Purpose: validated, atomic, typed mutation execution.

Important class:

- `Operations`

Responsibilities:

- Create/update/merge/supersede/archive/restore/delete durable memories.
- Create/update temporary memories.
- Enforce provenance (automatic vs explicit).
- Resolve previous-user-message references.
- Return structured `MutationOutcome` with `MutationStatus`.

Important constraints:

- Mutations only flow through here (validated/atomic/verified).
- Ambiguous updates return `MULTIPLE_MATCHES` instead of guessing.
- Failed writes never claim success.

Subsystem: memory execution.

### App/memory_v2/retrieval.py

Purpose: hybrid retrieval and ranking.

Important class:

- `Retriever`

Responsibilities:

- Combine canonical-key token overlap, key/subject match, structured attribute
  identity match, semantic similarity, importance, recency, and access count.
- Parse the query through `parse_reference` and gate candidates by explicit
  subject and attribute identity so generic modifiers never bridge unrelated
  attributes (favorite color never returns favorite drink; "what is my job"
  returns only the stored occupation).
- Embed via the injected `SemanticEmbedder` (default `LocalHashSemanticEmbedder`).
- Boost current-state temporary records for “what am I doing right now” queries.
- Inject only when the score clears the threshold (or when explicitly asked via `include_all`).
- Never inject archived or superseded records.
- Reinforce accessed temporary records with the current token/conversation
  counters (passed by the pipeline) instead of their stale `_last_seen` values.

Important constraints:

- Exact meaning beats loose similarity.
- Archived/old facts never override current facts.
- Return `RetrievalOutcome` with `selected`, `injected`, `reason`.

Subsystem: memory retrieval.

### App/memory_v2/temporary.py

Purpose: temporary cross-chat context lifecycle.

Important classes:

- `TemporaryConfig`
- `TemporaryMemory`

Responsibilities:

- Begin turns with token counters and conversation indices.
- Score temporary records by recency and context distance.
- Expire records by token/conversation distance (never purely by time).
- Enforce a budget via eviction.
- Emit `temporary_expired` audit events with reasons.
- Persist `token_counter` / `conversation_index` / `last_conversation_id` in
  `v2_global_state` so counters survive restarts; `begin_turn` writes them on
  every turn.

Important constraints:

- Counter state must round-trip through `MemoryStore.get_global*` /
  `set_global*`; a restarted `MemorySystem` continues token/context accounting.
- Retrieval reinforcement advances `token_at_last_seen` /
  `conversation_at_last_seen` to the *current* counters (see `retrieval.py`),
  not the record's stale values.

Subsystem: temporary memory lifecycle.

### App/memory_v2/consolidation.py

Purpose: periodic memory hygiene.

Important classes:

- `ConsolidationConfig`
- `ConsolidationResult`
- `Consolidator`

Responsibilities:

- Merge duplicate records on the same canonical key (archive older + link supersede).
- Refuse to merge conflicting values (emit skip-conflict events).
- Archive stale, unaccessed, low-importance records.
- Recompute importance boosts.

Important constraints:

- Never merge different meanings (favorite color vs favorite drink).
- Keep history via archival rather than hard deletion.
- Durable-retention guard: never stale-archive records that are
  `manually_edited` or whose `attribute_core` is in
  `ConsolidationConfig.protected_attribute_cores` (identity attributes such as
  name, occupation, phone, email, address, birthday), and never archive a
  `manually_edited` record when merging duplicates.

Subsystem: memory consolidation.

### App/memory_v2/pipeline.py

Purpose: `MemorySystem` facade — the single application entry point for memory.

Important classes:

- `MemorySystemConfig`, `MemorySystemStats`
- `MemorySystem`

Responsibilities:

- Wire extractor, operations, retriever, temporary memory, and consolidator.
- `handle_user_message()`: analyze, retrieve, optionally mutate, sweep, consolidate periodically.
- Enforce: questions are read-only; suppression is honored; mutations flow only through operations.
- Expose `retrieve()`, `archive()`, `restore()`, `delete()`, `clear_durable()`, `stats()`, and other manager APIs.
- `retrieve()` forwards the temporary manager's current token/conversation
  counters so temporary reinforcement is always current.

Important constraints:

- The app talks to `MemorySystem` and nothing else for memory.
- A memory question never triggers a write or delete.
- No false confirmations; mutations report structured outcomes.

Subsystem: memory orchestration.

### App/ollama_client.py

Purpose: Ollama API integration and streaming.

Important classes/functions:

- `OllamaWorker`
- `ModelDiscoveryWorker`
- `parse_stream_line()`
- `iter_message_chunks()`
- `discover_models()`
- `OllamaError`, `InvalidStreamError`, `EmptyResponseError`, `ModelMissingError`

Responsibilities:

- POST to `/api/chat` with `stream: true`.
- Parse newline-delimited JSON safely.
- Emit chunks incrementally.
- Track empty events, done flag, first event/token diagnostics.
- Handle cancellation by closing the response.
- Surface connection, timeout, malformed stream, missing model, empty response errors.

Important constraints:

- Must never discard valid streamed content.
- Must distinguish “no events,” “events but no visible text,” timeout, cancellation, invalid JSON, and HTTP errors.
- Network work must stay off the UI thread.

Subsystem: Ollama streaming.

### App/preferences.py

Purpose: small JSON preference store.

Responsibilities:

- Selected model.
- Memory-related flags are read defensively and ignored (Memory V2 is always on).

Important constraints:

- Keep preference writes local and minimal.
- Preferences file is runtime data and ignored by git.

Subsystem: preferences.

### App/prompts.py

Purpose: system prompt and message assembly.

Responsibilities:

- Base system prompt grounding current capabilities.
- Inject relevant memories into the single conversational prompt via `format_relevant_memories`.

Important constraints:

- Do not let VioletAI claim unimplemented capabilities.
- Retrieved memories are context, not verified facts.
- Memory is invisible to the user: no confirmation or follow-up prompts are generated.
- `format_relevant_memories` prefers the structured `statement` field when
  present and tags temporary records with "(temporary context)".

Subsystem: prompt construction.

### App/sidebar.py

Purpose: conversation sidebar and search overlay.

Important classes:

- `ConversationRow`
- `SearchOverlay`
- `ChatSidebar`

Responsibilities:

- New chat, search, settings, collapse/expand sidebar.
- Conversation grouping/selection.
- Pin/rename/delete context menu.
- Search overlay result list.

Important constraints:

- Sidebar should not trigger persistence itself except through main callbacks.
- Collapsed and expanded modes must remain stable/responsive.

Subsystem: conversation navigation UI.

### App/widgets.py

Purpose: reusable UI components.

Important classes/functions:

- `apply_interaction_cursors()`
- `ModelSelector`
- `AutoGrowingInput`
- `MarkdownView`
- `CodeHighlighter`
- `CodeBlock`
- `MessageBubble`
- `MessageActions`
- `ThinkingBubble`

Responsibilities:

- Composer input behavior.
- Markdown rendering.
- Fenced code blocks and copy button.
- Message bubbles.
- Message action buttons.
- Thinking animation.
- Cursor styling.

Important constraints:

- No internal scrollbars in messages/code blocks.
- Message height must grow with content.
- Composer must not enter compact/expanded feedback loops.
- Markdown/code rendering changes can affect layout tests.

Subsystem: reusable UI widgets.

## 3. Memory System

Memory V2 is a four-layer, deterministic, invisible memory system. It replaces the legacy `MemoryService`/`MemoryStore`/`MemoryIntentClassifier`/`MemoryDiagnostics` stack entirely; those modules were deleted.

Core rule:

```text
Extractor interprets (deterministically, never writes).
Operations validates and executes typed mutations.
Store persists.
Retriever decides whether retrieval genuinely helps.
One visible response comes from the conversational model, always.
```

Full pipeline for a user turn (`MemorySystem.handle_user_message`):

```text
User message
    ↓
Extractor: analyze (intent, facts, questions, suppression)
    ↓
Retriever: hybrid scoring against durable + temporary layers
    ↓
Threshold gating: inject only when it genuinely helps
    ↓
Operations: validated create/update/supersede/archive/delete if needed
    ↓
Temporary: current-state tracking, token-budget sweep
    ↓
Consolidator: periodic hygiene (duplicates, staleness, importance)
    ↓
Structured TurnOutcome returned to main.py
```

Layers:

- Durable: validated long-term facts (`v2_memories`).
- Archived: retired/old values, kept for history, never injected.
- Temporary: current-state facts/tasks (`v2_temporary_memories`), expired by token/conversation distance, never by time alone.
- Evidence: auditable `v2_memory_events` of every mutation.

Intent extraction (`Extractor`):

- Detects explicit create/update/delete commands.
- Resolves contextual commands (“Remember that.”) against the previous user message.
- Extracts durable personal facts (“my favorite color is purple”).
- Extracts temporary facts/tasks (“I'm working on X”, “remember to Y”).
- Detects questions about memory and suppression statements.
- Fully deterministic and conservative — ordinary conversation must never become a write.

Validation and execution (`Operations`):

- Every mutation flows through validated, atomic, typed operations with provenance (automatic vs explicit).
- Ambiguous updates return `MULTIPLE_MATCHES` (plus `clarification_needed`) instead of guessing.
- Supersession archives the old value and links `superseded_by_id`; old facts never override current facts.

Retrieval (`Retriever`):

- Hybrid score: canonical-key token overlap, structured attribute identity
  match, key/subject match, semantic similarity (via `SemanticEmbedder`),
  importance, recency, access count.
- Generic attribute slots (`favorite/favourite/fav/preferred/current/old/...`)
  are never used to bridge unrelated attributes — favorite color never returns
  favorite drink, and "what is my job" resolves to the stored occupation only.
- Person-scoped references (e.g. "when is Alice's birthday") restrict matching
  to that subject.
- Injection happens only when the score clears the threshold (or on `include_all` for explicit memory questions).
- Archived and superseded records are never injected.
- Accessed temporary records are reinforced with the current token/conversation
  counters, so active context stays alive while being used.

Durability guarantees (asserted by `tests/test_memory_v2_adversarial.py`):

- Memory questions are read-only: zero writes, zero deletes.
- Suppression statements write nothing.
- No-match deletions return `NO_MATCH` and change nothing.
- Unrelated queries inject nothing.
- Durable and temporary layers never cross-inject.

Persistence (`Store`):

- `SCHEMA_VERSION = 3`, WAL mode; migrates by backing up and dropping the legacy `memories` table, then adds v3 structured attribute columns with backfilled `attribute_core`.
- Insert/get/list/search/update/archive/restore/delete durable memories; insert/list/touch/update/expire/delete temporary memories; auditable mutation events; episodes; global counters; optional FTS5 search.
- Runtime DB is ignored by git.

Memory Manager (`App/memory_manager.py`):

- Settings overlay: search/filter/sort, edit, archive/restore, delete, clear.
- Manual edits route through `MemoryStore.update_memory(...)` with manual provenance.
- No memory mode or diagnostics toggles — memory is always on and invisible.

Invisibility:

- `main.py` has exactly one response path per turn: preparation (memory work) then a single conversational-model stream.
- No post-memory follow-up request, no confirmation labels, no diagnostics UI.
- The user never sees a memory success/failure message.

## 4. Request Lifecycle

Normal user request:

```text
User presses Enter
→ MainWindow.send_message()
→ user bubble renders immediately
→ user message appended to conversation
→ conversation saved if non-empty
→ RequestPreparationWorker starts on QThread
→ MemorySystem.handle_user_message() (analyze/retrieve/mutate)
→ relevant memories injected as context when threshold cleared
→ build_ollama_messages()
→ MainWindow receives PreparedRequest
→ OllamaWorker starts on QThread (always)
→ POST /api/chat stream=true
→ parse NDJSON stream
→ chunk_received signals update streamed_answer
→ render timer updates assistant bubble
→ finished signal finalizes full assistant response
→ conversation JSON saved
→ controls return to Ready
```

Thread ownership:

- UI thread:
  - reading composer text
  - adding/removing widgets
  - scroll decisions
  - final render
  - conversation/sidebar UI refresh
- Request preparation thread:
  - deterministic memory analysis (`MemorySystem.handle_user_message`)
  - memory retrieval/injection
  - prompt assembly
- Ollama thread:
  - HTTP request
  - streaming response iteration
- Model discovery thread:
  - `/api/tags` request

Cancellation/Stop:

- Stop calls `worker.cancel()`.
- Worker marks `_cancelled = True` and closes the active `requests.Response`.
- If partial text exists, the partial answer is preserved and marked stopped.
- Controls and status return to Stopped/Ready through cleanup.
- `closeEvent()` cancels or waits for active threads safely.

Errors:

- Request-preparation failures become `Response failed` with stage `request preparation`.
- Ollama failures report stage-specific errors.
- Empty responses are distinguished from timeout, invalid JSON, missing model, and connection failure.
- If partial output exists before an error, visible partial response is preserved.

Single response path:

- Every user turn is prepared once (memory analysis/retrieval inside `RequestPreparationWorker`) and then streamed once from the conversational model.
- There is no post-memory second call and no confirmation attachment.
- Relevant memories are injected into the single prompt as context.

Request ownership:

- `_finalized_current_response` prevents duplicate assistant appends.
- Active `thread` and `prep_thread` checks prevent sending/regenerating/model-switching while a request is active.
- `_receive_prepared_request()` is the only entry point into generation.

Timers/deferred rendering:

- `_render_timer` throttles streaming UI updates.
- `_composer_mode_timer` defers composer compact/multiline recalculation to one event-loop cycle.
- Scroll operations use `QTimer.singleShot(0, ...)` to let layouts settle first.
- Appearance/scroll animations use Qt animation/timer primitives.

Conversation/model switching:

- Model selectors are disabled during preparation/generation.
- Switching model updates preferences and current conversation model only when no request is active.
- Selecting/deleting conversations is handled through `ConversationStore` and UI rebuilds.

## 5. Current Project Philosophy

VioletAI’s architecture is guided by a few strong principles:

- Local-first: Ollama, memory, conversations, preferences, and logs are local.
- Privacy-first: runtime data is ignored by git; memory stays on disk locally.
- Native UI: PySide6 widgets, no Electron, no browser UI.
- Lightweight dependencies: standard library, PySide6, requests; no large frameworks.
- Deterministic execution: database writes are controlled by application code, not by LLM text.
- Deterministic interpretation: memory analysis is fully deterministic and conservative; ordinary conversation never becomes a write.
- No false confirmations: there are no memory confirmations at all — memory is invisible.
- Structured failures: expose specific failure reasons instead of generic replies.
- Preserve provenance/history: superseding archives older records instead of silently overwriting.
- Exact meaning wins: old facts never override current facts; favorite color is never confused with favorite drink.
- Do not hallucinate capabilities: prompts explicitly forbid claiming tools that are not implemented.
- Keep Qt widget access on the UI thread.
- Move blocking work to workers.
- Measure before optimizing.
- Keep fixes narrow and reversible.
- Preserve working behavior during focused bug fixes.
- Add regression tests for every bug class.
- Do not claim a fix unless verified.
- Keep git history recoverable.
- Runtime artifacts must not be committed.

The reasoning behind the main decisions:

- Memory is deterministic and layered because LLM output is useful as prose but unsafe as an execution authority; a fully deterministic extractor with validated typed operations removes that risk.
- Memory is invisible because conversational confirmations can be wrong, delayed, cancelled, or filtered; with no second response path there is nothing to go wrong.
- Streaming has its own worker because network and NDJSON iteration must never freeze Qt.
- Startup opens to a clean chat because it avoids surprising the user with stale context and keeps the app feeling instant.
- The prompt explicitly grounds capabilities because VioletAI’s planned future tools do not exist yet.

## 6. Current Memory-V2 Work

The Settings and UI-polish work has been completed and merged into `master`. The active `Memory-V2` branch replaced the legacy memory system.

Legacy failures that motivated the rewrite and are now resolved by Memory V2:

- Questions about memory could be mistaken for memory commands.
- Normal conversational responses could be replaced by a separate memory-response path.
- Vague deletion such as “forget it” could affect multiple unrelated memories.
- Exact attributes could be confused, such as favorite color versus favorite drink.
- Memory-related replies could feel detached from VioletAI's normal conversational model.
- Similarity-based matching could outrank exact subject, entity, attribute, recency, or temporal validity.
- Updates, duplicates, contradictions, failures, and confirmations were not reliably consistent.

Memory V2 now provides:

- One natural response path using VioletAI's primary conversational model.
- Current-chat context, temporary cross-chat episodic memory, durable memory, and archived history as distinct layers.
- Hybrid RAG using exact, lexical, semantic, structured, temporal, and provenance-aware retrieval.
- Typed, atomic, verified create/update/merge/supersede/archive/restore/delete operations.
- Conservative deletion and structured clarification when a target is ambiguous.
- Provenance, version history, contradiction handling, consolidation, and migration with legacy backup.
- Extensive deterministic and adversarial testing (265 tests passing, including real-store UI tests).

Status: Memory V2 is implemented and verified as an alpha, with a stabilization
pass underway on the `Memory-V2` branch. The stabilization pass covers:

- Attribute precision: a deterministic attribute ontology
  (`App/memory_v2/attributes.py`) maps phrases to canonical attribute cores and
  stops generic modifiers from bridging unrelated attributes. Retrieval and
  operations score on attribute identity and explicit subject, so "what is my
  job" retrieves the stored occupation while negative controls ("what project
  am I working on") never do.
- Schema v3 (`store.py`): structured fields on durable/temporary records
  (`attribute`, `attribute_core`, `statement`, `aliases`, `related_entities`,
  `qualifiers`, `valid_from`, `valid_to`), a new `v2_episodes` table, a
  `v2_global_state` table for persistent counters, and an optional FTS5
  `search_memories` index. v2 databases migrate with backfilled
  `attribute_core`.
- Persistent temporary counters (`temporary.py`): `token_counter`,
  `conversation_index`, and `last_conversation_id` are stored in
  `v2_global_state` and survive restarts.
- Reinforcement fix (`retrieval.py`): retrieved temporary records are touched
  with the current counters, not their stale `_last_seen` values.
- Semantic abstraction (`semantic.py`): retrieval depends on a
  `SemanticEmbedder`; the deterministic local hash embedder is the default and
  fallback, with an optional Ollama embedder that degrades safely.
- Consolidation durable-retention guard (`consolidation.py`): identity
  attributes and manually edited records are never stale-archived, and
  manually edited records are never archived as merge duplicates.
- Prompt/main wiring (`prompts.py`, `main.py`): injected memories prefer the
  structured `statement` and tag temporary context; mutation details can be
  logged opt-in via `VIOLETAI_MEMORY_LOG=1`; the regenerate path passes current
  counters for consistent reinforcement.

Remaining for this stabilization pass: none currently planned beyond the above;
any future episodic/semantic work continues on the same branch. Do not commit or
push unless explicitly asked.

v0.2 should not be tagged until the remaining task is complete and verified.

## 7. Coding Conventions

General principles:

- Keep fixes focused outside the explicitly authorized subsystem.
- Broad redesign is allowed for Memory V2 when required for one clean final architecture.
- Avoid unrelated refactoring.
- Measure before optimizing.
- Preserve UI behavior whenever possible.
- Prefer deterministic logic over LLM decisions.
- Keep blocking work off the UI thread.
- Runtime data must never be committed.
- Every bug fix should include regression tests.
- Explain root cause before explaining the fix.

Git workflow:

- Develop new work on feature branches.
- Keep master stable.
- Small logical commits.
- Push frequently.
- Tag only verified releases.

## 8. Testing

Before every significant commit:

```powershell
python -m unittest discover -s tests -v
python -m compileall -q App tests
git diff --check
git status --short
```

Headless UI tests use `QT_QPA_PLATFORM=offscreen`. Memory V2 has dedicated coverage: `tests/test_memory_v2_*` for each layer plus `tests/test_memory_v2_adversarial.py`, which asserts the corruption guards (question read-only, exact meaning wins, no-match deletion, suppression, threshold-gated injection, no layer cross-injection). `tests/test_memory_v2_attributes.py` covers the attribute-precision matrix end to end through `MemorySystem.handle_user_message`; `tests/test_memory_v2_semantic.py` covers the embedder abstraction and Ollama fallback.

Manual verification is required whenever changes affect:

- UI
- streaming
- memory
- startup
- settings
- threading
- Ollama integration

Automated tests should focus on regressions rather than implementation details.

## 9. Future Roadmap

After v0.2 the project roadmap is:

### Memory V2 foundation — current work

Implemented in Memory V2 (`App/memory_v2/temporary.py`): temporary facts/tasks are remembered across chats and expired by token/conversation distance rather than time, keeping long-term memory clean. A new chat begins with a fresh conversational focus while relevant recent conversation stays retrievable within a token budget; temporary episodes decay and expire separately from durable saved memory.

---

### Internet search

Allow VioletAI to search the web when local knowledge is insufficient.

Search results should integrate naturally into the existing request pipeline.

---

### File understanding

Support attachments using the existing + button.

Initial supported types:

- Images
- PDF
- Text
- Source code
- Markdown
- JSON

Additional common document formats may be added later.

Files become contextual information for the active conversation.

---

### Personality improvements

Make VioletAI:

- more natural
- less scripted
- more context aware
- warmer
- more conversational
- avoid hallucinating abilities
- maintain consistent identity

---

### Real-time voice conversations

Native low-latency voice conversations.

Conversation should feel continuous instead of request/response driven.

---

### Windows OS Agent

Vision-assisted desktop interaction.

Ability to:

- understand the screen
- operate Windows
- launch applications
- interact with UI
- complete tasks safely

---

### Coding Agent

Integrated software engineering assistant capable of:

- repository understanding
- planning
- implementation
- testing
- debugging
- documentation
- Git workflow

Eventually replacing external coding agents.

---

### Continuous polishing

Before every tagged release:

- UI polish
- Settings improvements
- Performance
- Reliability
- Bug fixes
- Regression testing

Tagged releases:

v0.2

v0.3

v0.4

...

## Repository Evolution

Current milestone:

v0.1 completed

Current work:

`Memory-V2` — final memory architecture rewrite

Memory V2 rewrite: legacy `memory_service` / `memory_store` / `memory_intent` / `memory_models` / `memory_embeddings` / `memory_diagnostics` modules were removed and replaced by the deterministic `App/memory_v2` package with a single visible response path.

Development model:

Stable releases are tagged.

Experimental work is performed on feature branches.

Every tagged release should be stable enough that development can continue from it indefinitely.

Release philosophy:

- Never sacrifice stability for speed.
- Each tagged release should always be usable as a long-term foundation.
- New features should not introduce regressions in existing functionality.
