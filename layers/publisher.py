"""
Publisher Layer — Final output formatting and delivery.

Responsibility: Take the compiled manuscript and produce publication-ready output
in multiple formats, then trigger delivery via configured channels.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict

from core.config import Config
from core.gateway import Gateway

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Publisher — the final stage in a literary creation pipeline.

You receive a compiled manuscript and must produce publication-ready metadata
and formatting guidance.

Output a JSON object with:
- "title": Final polished title
- "subtitle": Subtitle (if applicable)
- "author_suggested": Suggested author name or pen name
- "genre_tags": List of genre/subgenre tags
- "keywords": 10-15 SEO/discovery keywords
- "synopsis_short": 50-word synopsis
- "synopsis_long": 200-word synopsis
- "content_warnings": Any content warnings for readers
- "age_rating": Suggested age rating
- "series_potential": Whether this could be the start of a series, and hooks for sequels"""


async def execute(context: Dict[str, Any], gateway: Gateway, cfg: Config, **kwargs) -> Dict[str, Any]:
    """Execute the Publisher layer.

    Reads context['manuscript'], produces publication metadata and saves
    the final manuscript to disk.
    """
    manuscript = context.get("manuscript", "")
    creative_brief = context.get("creative_brief", "")

    if not manuscript:
        raise ValueError("Publisher requires 'manuscript' from Compiler")

    # Generate metadata via LLM
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": (
            f"Creative Brief:\n{creative_brief}\n\n"
            f"Manuscript (excerpt):\n{manuscript[:3000]}\n\n"
            "Produce publication metadata as a JSON object. "
            "Return ONLY the JSON, no markdown fences."
        )},
    ]

    logger.info("Publisher: generating metadata...")
    response, provider = await gateway.complete_with_fallback(messages=messages)
    logger.info("Publisher: completed via %s", provider)

    context["publication_metadata"] = response
    context["publisher_output"] = response

    # Save manuscript to disk
    ms_id = context.get("manuscript_id", "unknown")
    data_dir = Path(cfg.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    # Save raw manuscript
    ms_path = data_dir / f"{ms_id}_manuscript.txt"
    ms_path.write_text(manuscript, encoding="utf-8")
    logger.info("Publisher: saved manuscript to %s", ms_path)

    # Save metadata
    meta_path = data_dir / f"{ms_id}_metadata.json"
    meta_path.write_text(response, encoding="utf-8")
    logger.info("Publisher: saved metadata to %s", meta_path)

    context["manuscript_path"] = str(ms_path)
    context["metadata_path"] = str(meta_path)

    return context
