"""
Analyser Layer — Structural analysis of the creative brief.

Responsibility: Take the creative brief from the Thinker and analyze it for
narrative structure, identify potential plot holes, character arcs, pacing
considerations, and produce a structured analysis document.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from core.config import Config
from core.gateway import Gateway

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Analyser — the structural architect in a literary creation pipeline.

You receive a creative brief from the Thinker and must produce a rigorous structural analysis.

Output a JSON object with:
- "narrative_arc": The 3-act structure breakdown (setup, confrontation, resolution)
- "plot_points": List of 8-15 major plot points in chronological order
- "character_arcs": For each major character, their internal journey/transformation
- "pacing_plan": How tension should rise and fall across chapters
- "themes_to_explore": 3-5 subthemes that reinforce the main theme
- "potential_plot_holes": Any logical inconsistencies you detect in the brief
- "world_rules": Key rules of the story world that must be consistent
- "emotional_beats": The emotional journey the reader should experience
- "chapter_distribution": Suggested allocation of plot points across chapters

Be analytical and thorough. Your job is to find weaknesses before writing begins.
Think like an editor, not a fan."""


async def execute(context: Dict[str, Any], gateway: Gateway, cfg: Config, **kwargs) -> Dict[str, Any]:
    """Execute the Analyser layer.

    Reads context['creative_brief'], produces context['structural_analysis'].
    """
    brief = context.get("creative_brief", "")
    if not brief:
        raise ValueError("Analyser requires 'creative_brief' from Thinker")

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": (
            f"Creative Brief:\n{brief}\n\n"
            f"Target chapter count: {cfg.chapter_count}\n"
            f"Words per chapter: ~{cfg.words_per_chapter}\n\n"
            "Produce your structural analysis as a JSON object. "
            "Return ONLY the JSON, no markdown fences."
        )},
    ]

    logger.info("Analyser: analyzing creative brief...")
    response, provider = await gateway.complete_with_fallback(messages=messages)
    logger.info("Analyser: completed via %s", provider)

    context["structural_analysis"] = response
    context["analyser_output"] = response

    return context
