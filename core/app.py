"""
Hermes-Mythos FastAPI Application — REST API for the literature pipeline.

Provides endpoints for:
- Creating and managing manuscripts
- Starting and monitoring pipeline runs
- Downloading completed manuscripts (fixed scope bug)
- Webhook handlers for Telegram and WhatsApp
"""

from __future__ import annotations

import json
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from core.brain import BrainDAG
from core.config import Config, load_config
from core.gateway import Gateway
from core.state import StateManager

import channels.telegram as telegram_channel
import channels.whatsapp as whatsapp_channel

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Global singletons (initialized in lifespan)
# ---------------------------------------------------------------------------
cfg: Config
state: StateManager
gateway: Gateway
brain: BrainDAG


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup/shutdown lifecycle."""
    global cfg, state, gateway, brain

    cfg = load_config()
    state = StateManager(db_path=cfg.db_path)
    gateway = Gateway(cfg=cfg)
    brain = BrainDAG(gateway=gateway, state=state, cfg=cfg)

    logger.info("Hermes-Mythos started on %s:%d", cfg.host, cfg.port)
    yield

    await gateway.close()
    state.close()
    logger.info("Hermes-Mythos shut down")


app = FastAPI(
    title="Hermes-Mythos",
    description="7-layer cognitive DAG pipeline for long-form literature generation",
    version="2.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class ManuscriptCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    genre: str = Field(default="fiction", max_length=100)
    premise: str = Field(..., min_length=10, max_length=5000)
    chapter_count: Optional[int] = Field(default=None, ge=1, le=50)
    words_per_chapter: Optional[int] = Field(default=None, ge=100, le=20000)


class RunResponse(BaseModel):
    run_id: str
    manuscript_id: str
    status: str


class ManuscriptResponse(BaseModel):
    id: str
    title: str
    genre: str
    premise: str
    status: str


# ---------------------------------------------------------------------------
# Background pipeline runner
# ---------------------------------------------------------------------------

async def _run_pipeline(ms_id: str, run_id: str, initial_ctx: Optional[dict] = None):
    """Run the pipeline in background."""
    try:
        result = await brain.run(
            manuscript_id=ms_id,
            run_id=run_id,
            initial_context=initial_ctx,
        )
        state.update_manuscript_status(ms_id, "completed")
        logger.info("Pipeline completed for manuscript %s", ms_id)

        # Deliver via channels if configured
        await _deliver_result(ms_id, result)

    except Exception as e:
        logger.error("Pipeline failed for manuscript %s: %s", ms_id, e)
        state.update_manuscript_status(ms_id, "failed")


async def _deliver_result(ms_id: str, result: dict):
    """Deliver completed manuscript via configured channels."""
    manuscript_path = result.get("manuscript_path")
    if not manuscript_path:
        return

    metadata_str = result.get("publication_metadata", "{}")
    try:
        metadata = json.loads(metadata_str)
    except (json.JSONDecodeError, TypeError):
        metadata = {}

    # Telegram delivery
    if cfg.telegram_bot_token and cfg.telegram_chat_id:
        try:
            await telegram_channel.deliver_manuscript(
                bot_token=cfg.telegram_bot_token,
                chat_id=cfg.telegram_chat_id,
                manuscript_path=manuscript_path,
                metadata=metadata,
            )
            logger.info("Manuscript delivered via Telegram")
        except Exception as e:
            logger.error("Telegram delivery failed: %s", e)

    # WhatsApp delivery
    if cfg.whatsapp_account_sid and cfg.whatsapp_auth_token:
        try:
            await whatsapp_channel.deliver_manuscript(
                account_sid=cfg.whatsapp_account_sid,
                auth_token=cfg.whatsapp_auth_token,
                from_number=cfg.whatsapp_from,
                to_number=cfg.whatsapp_to,
                manuscript_path=manuscript_path,
                metadata=metadata,
            )
            logger.info("Manuscript delivered via WhatsApp")
        except Exception as e:
            logger.error("WhatsApp delivery failed: %s", e)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index():
    """Landing page."""
    return """<!DOCTYPE html>
<html><head><title>Hermes-Mythos</title></head>
<body>
<h1>📚 Hermes-Mythos v2.0</h1>
<p>7-layer cognitive DAG pipeline for long-form literature generation.</p>
<ul>
<li><a href="/docs">API Documentation (Swagger)</a></li>
<li><a href="/manuscripts">List Manuscripts</a></li>
</ul>
</body></html>"""


@app.post("/manuscripts", response_model=ManuscriptResponse)
async def create_manuscript(req: ManuscriptCreate):
    """Create a new manuscript."""
    ms_id = str(uuid.uuid4())[:8]
    ms = state.create_manuscript(
        ms_id=ms_id,
        title=req.title,
        genre=req.genre,
        premise=req.premise,
    )
    return ManuscriptResponse(
        id=ms["id"],
        title=ms["title"],
        genre=ms["genre"],
        premise=ms["premise"],
        status="draft",
    )


@app.get("/manuscripts")
async def list_manuscripts():
    """List all manuscripts."""
    return state.list_manuscripts()


@app.get("/manuscripts/{ms_id}")
async def get_manuscript(ms_id: str):
    """Get a specific manuscript."""
    ms = state.get_manuscript(ms_id)
    if ms is None:
        raise HTTPException(404, "Manuscript not found")
    return ms


@app.post("/manuscripts/{ms_id}/run", response_model=RunResponse)
async def start_pipeline(ms_id: str, background_tasks: BackgroundTasks):
    """Start a pipeline run for a manuscript."""
    ms = state.get_manuscript(ms_id)
    if ms is None:
        raise HTTPException(404, "Manuscript not found")

    run_id = str(uuid.uuid4())[:8]
    state.create_run(run_id, ms_id)
    state.update_manuscript_status(ms_id, "running")

    initial_ctx = {
        "premise": ms["premise"],
        "genre": ms["genre"],
        "title": ms["title"],
    }

    background_tasks.add_task(_run_pipeline, ms_id, run_id, initial_ctx)

    return RunResponse(run_id=run_id, manuscript_id=ms_id, status="running")


@app.get("/manuscripts/{ms_id}/run/{run_id}")
async def get_run(ms_id: str, run_id: str):
    """Get pipeline run status."""
    run = state.get_run(run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    return run


@app.post("/manuscripts/{ms_id}/run/{run_id}/resume")
async def resume_pipeline(ms_id: str, run_id: str, background_tasks: BackgroundTasks):
    """Resume a failed or interrupted pipeline run."""
    run = state.get_run(run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    if run["status"] == "completed":
        raise HTTPException(400, "Run already completed")

    state.update_manuscript_status(ms_id, "running")
    background_tasks.add_task(_resume_pipeline, ms_id, run_id)

    return {"run_id": run_id, "manuscript_id": ms_id, "status": "resuming"}


async def _resume_pipeline(ms_id: str, run_id: str):
    """Resume pipeline in background."""
    try:
        result = await brain.resume(manuscript_id=ms_id, run_id=run_id)
        state.update_manuscript_status(ms_id, "completed")
        await _deliver_result(ms_id, result)
    except Exception as e:
        logger.error("Resume failed for manuscript %s: %s", ms_id, e)
        state.update_manuscript_status(ms_id, "failed")


@app.get("/manuscripts/{ms_id}/download")
async def download_manuscript(ms_id: str):
    """Download the completed manuscript file.

    Fixed: uses ms_id (from path parameter) instead of undefined manuscript_id variable.
    """
    ms = state.get_manuscript(ms_id)
    if ms is None:
        raise HTTPException(404, "Manuscript not found")
    if ms["status"] != "completed":
        raise HTTPException(400, f"Manuscript not ready (status: {ms['status']})")

    # Look for the manuscript file — use ms['id'] to construct the path
    manuscript_path = Path(cfg.data_dir) / f"{ms['id']}_manuscript.txt"
    if not manuscript_path.exists():
        raise HTTPException(404, "Manuscript file not found on disk")

    return FileResponse(
        path=str(manuscript_path),
        filename=f"{ms['title']}.txt",
        media_type="text/plain",
    )


@app.get("/manuscripts/{ms_id}/fragments")
async def get_fragments(ms_id: str):
    """Get narrative fragments (chapters) for a manuscript."""
    ms = state.get_manuscript(ms_id)
    if ms is None:
        raise HTTPException(404, "Manuscript not found")
    return state.get_fragments(ms_id)


# ---------------------------------------------------------------------------
# Webhook endpoints for inbound messages
# ---------------------------------------------------------------------------

@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    """Handle incoming Telegram messages."""
    data = await request.json()
    message = data.get("message", {})
    text = message.get("text", "")
    chat_id = str(message.get("chat", {}).get("id", ""))

    if text.startswith("/newstory"):
        parts = text.split(" ", 1)
        premise = parts[1] if len(parts) > 1 else "A mysterious tale"
        ms_id = str(uuid.uuid4())[:8]
        state.create_manuscript(ms_id=ms_id, title="Telegram Story", genre="fiction", premise=premise)

        if cfg.telegram_bot_token and chat_id:
            await telegram_channel.send_message(
                bot_token=cfg.telegram_bot_token,
                chat_id=chat_id,
                text=f"✨ Created manuscript `{ms_id}` — starting pipeline...",
            )

        # Start pipeline
        run_id = str(uuid.uuid4())[:8]
        state.create_run(run_id, ms_id)
        state.update_manuscript_status(ms_id, "running")
        import asyncio
        asyncio.create_task(
            _run_pipeline(ms_id, run_id, {"premise": premise, "genre": "fiction"})
        )

        return {"ok": True}

    return {"ok": True}


@app.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request):
    """Handle incoming WhatsApp messages (Twilio format)."""
    form = await request.form()
    body = str(form.get("Body", ""))
    from_number = str(form.get("From", ""))

    if body.lower().startswith("new story:"):
        premise = body[10:].strip()
        ms_id = str(uuid.uuid4())[:8]
        state.create_manuscript(ms_id=ms_id, title="WhatsApp Story", genre="fiction", premise=premise)

        run_id = str(uuid.uuid4())[:8]
        state.create_run(run_id, ms_id)
        state.update_manuscript_status(ms_id, "running")
        import asyncio
        asyncio.create_task(
            _run_pipeline(ms_id, run_id, {"premise": premise, "genre": "fiction"})
        )

        return {"ok": True}

    return {"ok": True}


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "version": "2.0.0",
        "providers": cfg.active_fallback_chain,
    }
