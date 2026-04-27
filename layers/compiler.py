"""
Compiler Layer — Assemble chapters into a cohesive manuscript.

Responsibility: Take the reviewed chapters, apply final polish, ensure consistency
across chapters, and produce a single unified manuscript document.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from core.config import Config
from core.gateway import Gateway

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Compiler — the final assembly specialist in a literary creation pipeline.

You receive reviewed and approved chapters and must assemble them into a cohesive,
polished manuscript. Your job is the final editorial pass.

Tasks:
1. Check for continuity errors between chapters (timeline, character details, locations)
2. Ensure smooth transitions between chapters
3. Verify that planted seeds have payoffs (callback consistency)
4. Polish any rough prose that survived review
5. Ensure consistent formatting and style throughout
6. Generate a table of contents
7. Write a brief author's note if appropriate

Output a JSON object with:
- "manuscript": The complete text, with chapters separated by "## Chapter N: Title" headers
- "table_of_contents": List of {chapter_num, title, summary}
- "continuity_fixes": Any continuity issues you found and fixed
- "total_word_count": Approximate total word count
- "dedication": A brief dedication or epigraph suggestion
- "blurb": A 150-word back-cover blurb for the book"""


async def execute(context: Dict[str, Any], gateway: Gateway, cfg: Config, **kwargs) -> Dict[str, Any]:
    """Execute the Compiler layer.

    Reads context['chapters_raw'], produces context['manuscript'].
    """
    chapters_raw = context.get("chapters_raw", "")
    creative_brief = context.get("creative_brief", "")

    if not chapters_raw:
        raise ValueError("Compiler requires 'chapters_raw' from Writer")

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": (
            f"Creative Brief:\n{creative_brief}\n\n"
            f"Chapters:\n{chapters_raw}\n\n"
            "Compile the final manuscript as a JSON object. "
            "Return ONLY the JSON, no markdown fences."
        )},
    ]

    logger.info("Compiler: assembling manuscript...")
    response, provider = await gateway.complete_with_fallback(messages=messages)
    logger.info("Compiler: completed via %s", provider)

    context["manuscript"] = response
    context["compiler_output"] = response

    return context
