# Hermes-Mythos v2.0

A 7-layer cognitive DAG pipeline for generating long-form literature via LLM providers. 

The system is strictly optimized for a <2GB RAM footprint through serial execution and lazy module loading.

## Architecture
```
Thinker → Analyser → Planner → Writer → Reviewer → Compiler → Publisher
                                            
                                   
```

---

## 🚀 Quick Start: One-Click Deployment

For rapid deployment on a clean Linux server (Ubuntu/Debian recommended), run the following command. 
This script installs Docker, clones the repository, configures your environment, and starts the orchestrator.

```bash
curl -sSL https://raw.githubusercontent.com/chelotithehost-sketch/hermes-mythos-improved/main/install.sh | bash
```

### What this script does:
1. **Dependency Audit**: Checks for and installs Docker, Docker Compose, and Git.
2. **Environment Setup**: Automatically creates a `.env` file and prompts you for your API keys (OpenAI, Anthropic, Gemini).
3. **Containerization**: Builds the optimized Python 3.12-slim image with 2GB memory hard-limits.
4. **Volume Persistence**: Sets up `/mnt/data` for manuscript storage and `/app/library.db` for the SQLite metadata store.

---

## Manual Installation

### Docker (Recommended)
If you already have Docker installed, follow these steps:
```bash
cp .env.example .env
# Edit .env with your API keys
docker compose up --build -d
```

### Local Development
```bash
pip install -r requirements-dev.txt
uvicorn core.app:app --reload
```

---

## API Usage

| Action | Endpoint | Description |
| :--- | :--- | :--- |
| **Create** | `POST /manuscripts` | Initialize a new literary project with title/genre. |
| **Start** | `POST /manuscripts/{id}/run` | Triggers the 7-layer DAG pipeline. |
| **Status** | `GET /manuscripts/{id}/run/{run_id}` | Monitor real-time layer progress. |
| **Download** | `GET /manuscripts/{id}/download` | Retrieve the final EPUB/TXT file. |

---

## Supported LLM Gateway
| Provider | Tier | Model |
| :--- | :--- | :--- |
| **Anthropic** | Frontier | claude-sonnet-4-20250514 |
| **OpenAI** | Frontier | gpt-4o |
| **Gemini** | Frontier | gemini-2.0-flash |
| **Ollama** | Local | llama3:8b |

## License
MIT
```

### Recommended `install.sh` Script Content
To make the "one-click" command work, you should include this `install.sh` in your repository root:

```bash
#!/bin/bash
set -e

echo "Starting Hermes-Mythos One-Click Installation..."

# 1. Install System Dependencies
sudo apt-get update
sudo apt-get install -y docker.io docker-compose git curl

# 2. Setup Environment
if [ ! -f .env ]; then
    echo "Configuring Environment Variables..."
    read -p "Enter Anthropic API Key: " ANTHROPIC_KEY
    read -p "Enter OpenAI API Key: " OPENAI_KEY
    echo "ANTHROPIC_API_KEY=$ANTHROPIC_KEY" >> .env
    echo "OPENAI_API_KEY=$OPENAI_KEY" >> .env
    echo "OLLAMA_BASE=http://host.docker.internal:11434" >> .env
fi

# 3. Build and Launch
sudo docker-compose up --build -d

echo "Installation Complete. Hermes-Mythos is running on port 8000."
```
This configuration ensures that any user can deploy the entire stack—including the message queue, SQLite database, and the 7-layer DAG—in a single session while strictly adhering to the 2GB RAM budget.

This configuration ensures that any user can deploy the entire stack—including the message queue, SQLite database, and the 7-layer DAG—in a single session while strictly adhering to the 2GB RAM budget.
