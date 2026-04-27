"""
Planner Layer — Chapter-by-chapter outline generation.

Responsibility: Take the structural analysis and produce a detailed chapter-by-chapter
outline with scene breakdowns, character appearances, and narrative goals for each chapter.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from core.config import Config
from core.gateway import Gateway

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Planner — the tactical architect in a literary creation pipeline.

You receive a structural analysis from the Analyser and must produce a detailed
chapter-by-chapter outline.

Output a JSON object with:
- "chapter_outlines": A list of chapter objects, each containing:
  - "chapter_num": Chapter number (1-indexed)
  - "title": Working title for the chapter
  - "pov": Point-of-view character (if applicable)
  - "setting": Where this chapter takes place
  - "summary": 3-5 sentence summary of what happens
  - "key_scenes": List of 2-4 specific scenes with brief descriptions
  - "character_appearances": Which characters appear
  - "emotional_arc": The emotional journey within this chapter
  - "chapter_goal": What narrative purpose this chapter serves
  - "cliffhanger_or_hook": How the chapter ends to pull the reader forward
  - "estimated_words": Target word count for this chapter

- "overall_pacing": Brief notes on the pacing arc across all chapters
- "key_revelations": When major secrets/revelations are revealed
- "callback_seeds": Early details that pay off later (for the Writer to plant)

Each chapter outline should be detailed enough that a Writer could work from it
independently. Think of these as production-ready scene cards."""


async def execute(context: Dict[str, Any], gateway: Gateway, cfg: Config, **kwargs) -> Dict[str, Any]:
    """Execute the Planner layer.

    Reads context['structural_analysis'], produces context['chapter_outlines'].
    """
    analysis = context.get("structural_analysis", "")
    if not analysis:
        raise ValueError("Planner requires 'structural_analysis' from Analyser")

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": (
            f"Structural Analysis:\n{analysis}\n\n"
            f"Chapter count: {cfg.chapter_count}\n"
            f"Words per chapter: ~{cfg.words_per_chapter}\n"
            f"Total target length: ~{cfg.chapter_count * cfg.words_per_chapter} words\n\n"
            "Produce your chapter outlines as a JSON object. "
            "Return ONLY the JSON, no markdown fences."
        )},
    ]

    logger.info("Planner: generating %d chapter outlines...", cfg.chapter_count)
    response, provider = await gateway.complete_with_fallback(messages=messages)
    logger.info("Planner: completed via %s", provider)

    context["chapter_outlines"] = response
    context["planner_output"] = response

    return context
