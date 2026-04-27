"""
Reviewer Layer — Quality review and revision decision.

Responsibility: Evaluate the written chapters against the outlines and creative brief.
Produce actionable feedback and decide whether revision is needed.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

from core.config import Config
from core.gateway import Gateway

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Reviewer — the quality gatekeeper in a literary creation pipeline.

You receive the written chapters and must evaluate them against the creative brief
and chapter outlines. Your review should be thorough but constructive.

Evaluate on these dimensions (score 1-10 each):
1. **Narrative coherence**: Does the story hang together logically?
2. **Character consistency**: Do characters behave consistently? Are arcs satisfying?
3. **Prose quality**: Is the writing engaging, varied, and free of AI-ish patterns?
4. **Pacing**: Does tension build appropriately? Are there dead zones?
5. **Theme execution**: Is the central theme explored effectively?
6. **Outline adherence**: Does the draft follow the planned structure?
7. **Reader engagement**: Would a reader want to keep reading?

Output a JSON object with:
- "scores": {dimension: score} for each dimension above
- "overall_score": Weighted average (1-10)
- "approved": boolean — true if overall_score >= 7
- "strengths": List of what works well
- "issues": List of specific problems with chapter references
- "revision_instructions": If not approved, specific instructions for the Writer
  (which chapters to revise, what to fix, what to preserve)
- "line_notes": Optional specific line-level feedback

Be honest. A score of 7+ means "publishable with minor edits." Don't inflate scores.
If the draft has fundamental structural problems, say so — it's cheaper to fix now
than after publication."""


async def execute(context: Dict[str, Any], gateway: Gateway, cfg: Config, **kwargs) -> Dict[str, Any]:
    """Execute the Reviewer layer.

    Reads context['chapters_raw'], sets context['needs_revision'] and
    context['reviewer_feedback'].
    """
    chapters_raw = context.get("chapters_raw", "")
    creative_brief = context.get("creative_brief", "")
    outlines = context.get("chapter_outlines", "")

    if not chapters_raw:
        raise ValueError("Reviewer requires 'chapters_raw' from Writer")

    revision_count = context.get("revision_count", 0)
    max_revisions = context.get("max_revisions", cfg.max_revisions)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": (
            f"Creative Brief:\n{creative_brief}\n\n"
            f"Chapter Outlines:\n{outlines}\n\n"
            f"Written Chapters:\n{chapters_raw}\n\n"
            f"Revision pass: {revision_count + 1} of {max_revisions + 1}\n\n"
            "Provide your review as a JSON object. "
            "Return ONLY the JSON, no markdown fences."
        )},
    ]

    logger.info("Reviewer: evaluating draft (revision %d/%d)...", revision_count, max_revisions)
    response, provider = await gateway.complete_with_fallback(messages=messages)
    logger.info("Reviewer: completed via %s", provider)

    # Parse the review to determine if revision is needed
    needs_revision = False
    try:
        # Try to parse JSON from response
        review_data = json.loads(response)
        approved = review_data.get("approved", False)
        overall_score = review_data.get("overall_score", 0)

        if not approved and revision_count < max_revisions:
            needs_revision = True
            context["reviewer_feedback"] = review_data.get(
                "revision_instructions", "Improve based on the issues listed."
            )
            logger.info(
                "Reviewer: draft not approved (score=%.1f), sending for revision %d",
                overall_score, revision_count + 1,
            )
        else:
            logger.info("Reviewer: draft approved (score=%.1f)", overall_score)
            needs_revision = False
    except (json.JSONDecodeError, TypeError):
        # If we can't parse the review, assume approved to avoid infinite loops
        logger.warning("Reviewer: could not parse review JSON, assuming approved")
        needs_revision = False

    context["needs_revision"] = needs_revision
    context["review_output"] = response
    context["review_data"] = response

    return context
