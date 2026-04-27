# Hermes-Mythos v2.0

A 7-layer cognitive DAG pipeline for generating long-form literature via LLM providers.

## Architecture

```
Thinker → Analyser → Planner → Writer → Reviewer → Compiler → Publisher
                                   ↑          |
                                   └──(revision loop)──┘
```

Each layer is an importlib-loadable module that is loaded into memory only during execution, then garbage collected to stay within the <2GB RAM budget.

## Quick Start

### Local Development

```bash
cp .env.example .env
# Edit .env with your API keys

pip install -r requirements-dev.txt
uvicorn core.app:app --reload
```

### Docker

```bash
cp .env.example .env
# Edit .env with your API keys

docker compose up --build
```

### API Usage

```bash
# Create a manuscript
curl -X POST http://localhost:8000/manuscripts \
  -H "Content-Type: application/json" \
  -d '{"title": "My Story", "genre": "sci-fi", "premise": "A lone astronaut discovers an alien signal"}'

# Start the pipeline
curl -X POST http://localhost:8000/manuscripts/{id}/run

# Check status
curl http://localhost:8000/manuscripts/{id}/run/{run_id}

# Download when complete
curl http://localhost:8000/manuscripts/{id}/download -o manuscript.txt
```

## Supported LLM Providers

| Provider   | Tier        | Models                    |
|-----------|-------------|---------------------------|
| OpenAI    | Frontier    | gpt-4o, gpt-4-turbo      |
| Anthropic | Frontier    | claude-sonnet-4-20250514  |
| Gemini    | Frontier    | gemini-2.0-flash          |
| Mistral   | Mid-tier    | mistral-large-latest      |
| Ollama    | Lightweight | llama3 (local)            |

## Channels

- **Telegram**: Deliver manuscripts via Telegram Bot API
- **WhatsApp**: Deliver manuscripts via Twilio WhatsApp API

## Development

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

## License

MIT
