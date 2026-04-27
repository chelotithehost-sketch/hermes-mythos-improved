"""
Writer Layer — Prose generation for each chapter.

Responsibility: Take the chapter outlines and write full prose for each chapter.
Handles both initial writing and revision passes based on Reviewer feedback.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from core.config import Config
from core.gateway import Gateway

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Writer — the prose artisan in a literary creation pipeline.

You receive detailed chapter outlines from the Planner and must write compelling,
polished prose for each chapter.

Writing guidelines:
- Show, don't tell. Use sensory details and specific imagery.
- Vary sentence length and structure. Mix short punchy sentences with longer flowing ones.
- Each chapter should open with a hook and close with momentum.
- Dialogue should sound natural and reveal character.
- Maintain consistent voice, tone, and POV throughout.
- Plant seeds for later payoffs as indicated in the outline.
- Target ~{words_per_chapter} words per chapter.
- Write in a literary style appropriate to the genre — not generic AI prose.

If this is a REVISION pass, you will receive reviewer feedback. Address all
feedback points while preserving the chapter's strengths. Mark revised sections
clearly.

Output format: Return a JSON object with:
- "chapters": A list of objects, each containing:
  - "chapter_num": Chapter number
  - "title": Chapter title
  - "content": The full prose text of the chapter
- "revision_notes": (only on revision passes) What you changed and why"""

REVISION_PROMPT = """This is a REVISION pass. The Reviewer has provided the following feedback
on your previous draft:

{feedback}

Revise the affected chapters to address this feedback. Output the same JSON format
with the revised content. Include a "revision_notes" field explaining your changes."""


async def execute(context: Dict[str, Any], gateway: Gateway, cfg: Config, **kwargs) -> Dict[str, Any]:
    """Execute the Writer layer.

    Reads context['chapter_outlines'] and optionally context['reviewer_feedback'],
    produces context['chapters'] (list of chapter dicts).
    """
    outlines = context.get("chapter_outlines", "")
    if not outlines:
        raise ValueError("Writer requires 'chapter_outlines' from Planner")

    is_revision = context.get("needs_revision", False)
    feedback = context.get("reviewer_feedback", "")

    system = SYSTEM_PROMPT.replace("{words_per_chapter}", str(cfg.words_per_chapter))

    user_content = (
        f"Creative Brief:\n{context.get('creative_brief', '')}\n\n"
        f"Chapter Outlines:\n{outlines}\n\n"
        f"Write all {cfg.chapter_count} chapters with full prose. "
        "Return ONLY the JSON, no markdown fences."
    )

    if is_revision and feedback:
        user_content = (
            f"Chapter Outlines:\n{outlines}\n\n"
            f"{REVISION_PROMPT.format(feedback=feedback)}\n\n"
            "Return ONLY the JSON, no markdown fences."
        )

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_content},
    ]

    logger.info("Writer: %s chapters (revision=%s)...", cfg.chapter_count, is_revision)
    response, provider = await gateway.complete_with_fallback(messages=messages)
    logger.info("Writer: completed via %s", provider)

    context["chapters_raw"] = response
    context["writer_output"] = response

    # Reset revision flag after writing
    if is_revision:
        context["needs_revision"] = False

    return context
