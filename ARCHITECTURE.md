# ARCHITECTURE.md — Hermes-Mythos v2.0

## Overview

Hermes-Mythos is a 7-layer cognitive DAG pipeline that generates long-form literature
(novels, novellas, serial fiction) using LLM providers. The system runs on FastAPI
with SQLite state management, Docker deployment, and a strict <2GB RAM budget.

## Core Innovation: importlib Layer Unloading

Each cognitive layer (Thinker, Analyser, etc.) is implemented as a standalone Python
module under `layers/`. The `LayerNode` class in `brain.py` uses `importlib.import_module()`
to dynamically load these modules at execution time. After a layer completes, the module
is removed from `sys.modules` and `gc.collect()` is called to free memory.

This design ensures that only one layer's code is in memory at any given time, which is
critical for the <2GB RAM constraint. The 7 layers are:

1. **Thinker** — Generates creative briefs from premises
2. **Analyser** — Structural analysis and plot hole detection
3. **Planner** — Chapter-by-chapter outline generation
4. **Writer** — Prose generation with revision support
5. **Reviewer** — Quality evaluation and revision decisions
6. **Compiler** — Final assembly and continuity checking
7. **Publisher** — Metadata generation and file output

## DAG Execution Model

The pipeline is a true directed acyclic graph, not a simple linear chain. The key
non-linear edge is the **Reviewer→Writer revision loop**:

```
Thinker → Analyser → Planner → Writer → Reviewer → Compiler → Publisher
                                    ↑          |
                                    └──(revision)──┘
```

This is implemented as conditional edges in the DAG:
- `Edge("reviewer", "writer", CONDITIONAL, condition=needs_revision)` — loops back if quality is insufficient
- `Edge("reviewer", "compiler", CONDITIONAL, condition=not needs_revision)` — proceeds if approved

The revision count is capped at `max_revisions` (default: 3) to prevent infinite loops.

## State Management

SQLite with WAL journal mode and connection pooling:
- `ConnectionPool` manages a pool of reusable connections
- `StateManager` provides CRUD for manuscripts, runs, completions, and narrative fragments
- Narrative fragments enable proper pipeline resume — if the Writer completed chapters 1-5
  before a crash, resume reads those fragments and rebuilds the context

## Gateway: Retry and Fallback

The `Gateway` class wraps all LLM provider calls with:
- **Exponential backoff**: 3 retries with 1s → 2s → 4s delays
- **Fallback chain**: If frontier providers (OpenAI/Anthropic/Gemini) fail, automatically
  tries mid-tier (Mistral), then lightweight (Ollama)
- **Error classification**: 429s and 5xx are retryable; 4xx (except 429) are not

## Channel Delivery

Completed manuscripts are delivered via configured channels:
- **Telegram**: Bot API for document/message delivery
- **WhatsApp**: Twilio API for document/message delivery

The Publisher layer triggers delivery after saving the manuscript to disk.
Webhook endpoints in `app.py` handle inbound messages for interactive story creation.

## Memory Budget

Target: <2GB RAM for the entire pipeline. Key strategies:
- importlib layer loading/unloading (only 1 layer in memory at a time)
- SQLite WAL mode (avoids holding full DB in memory)
- Connection pooling (bounded connection count)
- Streaming LLM responses where possible (future optimization)

## Directory Structure

```
hermes-mythos-improved/
├── core/           # Application core (FastAPI, brain, gateway, state, config)
├── layers/         # Importlib-loadable layer modules (7 cognitive layers)
├── channels/       # Outbound delivery modules (Telegram, WhatsApp)
├── templates/      # HTML templates (manuscript rendering)
├── tests/          # Unit tests
├── data/           # Runtime data (database, manuscripts)
├── Dockerfile      # Container definition
├── docker-compose.yml
├── requirements.txt
└── .env.example
```
