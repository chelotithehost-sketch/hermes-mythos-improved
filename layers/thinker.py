"""
Thinker Layer — Genesis of narrative concepts.

Responsibility: Take the raw premise and genre, then brainstorm a high-level
story concept including theme, tone, setting, and a one-paragraph synopsis.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from core.config import Config
from core.gateway import Gateway

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Thinker — the first mind in a literary creation pipeline.
Your role is to take a raw story premise and genre, then produce a rich creative brief.

You must output a JSON object with these fields:
- "theme": The central theme or message of the story (1-2 sentences)
- "tone": The narrative tone (e.g., "dark and introspective", "lighthearted and adventurous")
- "setting": A vivid description of the world/setting
- "synopsis": A compelling 2-3 paragraph synopsis of the story arc
- "protagonist": Brief description of the main character(s)
- "central_conflict": The core dramatic tension driving the plot
- "target_audience": Who this story is for
- "comparable_works": 2-3 comparable published works for tone/style reference
- "narrative_voice": First person, third person limited, omniscient, etc.

Be creative and bold. This is the generative phase — take risks. The synopsis should
hint at a satisfying arc with clear escalation and resolution potential."""


async def execute(context: Dict[str, Any], gateway: Gateway, cfg: Config, **kwargs) -> Dict[str, Any]:
    """Execute the Thinker layer.

    Reads context['premise'] and context['genre'], produces a creative brief
    stored in context['creative_brief'].
    """
    premise = context.get("premise", "")
    genre = context.get("genre", "fiction")

    if not premise:
        raise ValueError("Thinker requires 'premise' in context")

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": (
            f"Genre: {genre}\n\n"
            f"Premise: {premise}\n\n"
            f"Chapter count: {cfg.chapter_count}\n"
            f"Target length per chapter: ~{cfg.words_per_chapter} words\n\n"
            "Produce your creative brief as a JSON object. "
            "Return ONLY the JSON, no markdown fences."
        )},
    ]

    logger.info("Thinker: generating creative brief for premise: %s...", premise[:80])
    response, provider = await gateway.complete_with_fallback(messages=messages)
    logger.info("Thinker: completed via %s", provider)

    context["creative_brief"] = response
    context["thinker_output"] = response
    context["provider_used"] = provider

    return context
