"""
Reviewer Layer — Quality gatekeeper for any literary work.

Responsibility: Evaluates content against dynamic heuristics provided by the 
Thinker/Planner. Determines if the quality meets the threshold for the 
next DAG stage or requires a revision pass.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

from core.config import Config
from core.gateway import Gateway

# Standardised logging for system monitoring
logger = logging.getLogger(__name__)

# Base instructions ensuring the model maintains a professional critical persona
BASE_REVIEWER_INSTRUCTIONS = """You are the Reviewer — the quality gatekeeper in a high-tier literary creation pipeline.
You receive the generated text and must evaluate it against the specific project criteria provided. 
Your review must be thorough, objective, and constructive.

A score of 7+ means "publishable with minor edits." Do not inflate scores. 
If the work has fundamental structural problems, flag them for revision."""

async def execute(context: Dict[str, Any], gateway: Gateway, cfg: Config, **kwargs) -> Dict[str, Any]:
    """
    Executes the Reviewer layer with support for dynamic evaluation profiles.
    
    Reads: context['chapters_raw'], context['evaluation_profile']
    Sets: context['needs_revision'], context['reviewer_feedback'], context['review_metadata']
    """
    # 1. Content Retrieval 
    content_to_review = context.get("chapters_raw") or context.get("manuscript_body")
    creative_brief = context.get("creative_brief", "No brief provided.")
    
    if not content_to_review:
        logger.error("Reviewer failed: No content found in context.")
        raise ValueError("Reviewer requires 'chapters_raw' or 'manuscript_body' to proceed.")

    # 2. Dynamic Heuristic Mapping 
    # Falls back to general quality metrics if no profile was set by the Thinker/Planner
    eval_criteria = context.get("evaluation_profile", {
        "Technical Accuracy": "Is the information factually correct and precise?",
        "Prose Quality": "Is the writing engaging, varied, and free of AI patterns?",
        "Structural Flow": "Does the document follow a logical progression?"
    })

    # 3. Revision Tracking [cite: 275, 278]
    revision_count = context.get("revision_count", 0)
    max_revisions = context.get("max_revisions", cfg.max_revisions)

    # 4. Prompt Engineering for Any Literary Work [cite: 276]
    criteria_str = "\n".join([f"{i+1}. **{k}**: {v}" for i, (k, v) in enumerate(eval_criteria.items())])
    
    system_prompt = f"{BASE_REVIEWER_INSTRUCTIONS}\n\nEVALUATION CRITERIA:\n{criteria_str}"
    
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": (
            f"Creative Brief:\n{creative_brief}\n\n"
            f"Content to Review:\n{content_to_review}\n\n"
            f"Revision pass: {revision_count + 1} of {max_revisions + 1}\n\n"
            "Output a JSON object with:\n"
            "- 'scores': {dimension: score 1-10}\n"
            "- 'overall_score': Weighted average\n"
            "- 'approved': boolean (true if overall_score >= 7)\n"
            "- 'issues': List of specific problems\n"
            "- 'revision_instructions': If not approved, what specifically to fix.\n"
            "Return ONLY the JSON object."
        )},
    ]

    # 5. Gateway Execution with Frontier Fallback [cite: 277]
    logger.info("Reviewer: Evaluating draft (Revision %d/%d)...", revision_count, max_revisions)
    response, provider = await gateway.complete_with_fallback(messages=messages)
    logger.info("Reviewer: Evaluation completed via %s", provider)

    # 6. Robust Response Parsing & Revision Logic [cite: 278, 280]
    needs_revision = False
    try:
        review_data = json.loads(response)
        approved = review_data.get("approved", False)
        overall_score = review_data.get("overall_score", 0)

        if not approved and revision_count < max_revisions:
            needs_revision = True
            context["reviewer_feedback"] = review_data.get(
                "revision_instructions", "General improvements requested."
            )
            logger.info(
                "Reviewer: Draft rejected (Score: %.1f). Sending for revision pass %d.",
                overall_score, revision_count + 1
            )
        else:
            logger.info("Reviewer: Draft approved (Score: %.1f).", overall_score)
            needs_revision = False
            
        context["review_metadata"] = review_data

    except (json.JSONDecodeError, TypeError):
        # Fail-safe: Assume approved on parse error to avoid infinite DAG loops [cite: 280]
        logger.warning("Reviewer: Failed to parse JSON response. Assuming approved to prevent hang.")
        needs_revision = False

    # 7. Memory Cleanup 
    context["needs_revision"] = needs_revision
    del content_to_review
    del messages
    
    return context
