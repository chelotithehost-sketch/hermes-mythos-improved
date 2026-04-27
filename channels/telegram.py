"""
Telegram Channel — outbound document delivery via Telegram Bot API.

Sends completed manuscripts as documents or messages to a configured chat.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"


async def send_message(
    bot_token: str,
    chat_id: str,
    text: str,
    parse_mode: str = "Markdown",
) -> Dict[str, Any]:
    """Send a text message via Telegram.

    Args:
        bot_token: Telegram bot token.
        chat_id: Target chat ID.
        message: Message text.
        parse_mode: Message parse mode.

    Returns:
        Telegram API response dict.
    """
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{TELEGRAM_API}/bot{bot_token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode,
            },
        )
        resp.raise_for_status()
        return resp.json()


async def send_document(
    bot_token: str,
    chat_id: str,
    file_path: str,
    caption: str = "",
) -> Dict[str, Any]:
    """Send a document file via Telegram.

    Args:
        bot_token: Telegram bot token.
        chat_id: Target chat ID.
        file_path: Path to the file to send.
        caption: Optional caption for the document.

    Returns:
        Telegram API response dict.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    async with httpx.AsyncClient() as client:
        with open(path, "rb") as f:
            resp = await client.post(
                f"{TELEGRAM_API}/bot{bot_token}/sendDocument",
                data={
                    "chat_id": chat_id,
                    "caption": caption,
                },
                files={"document": (path.name, f)},
            )
        resp.raise_for_status()
        return resp.json()


async def deliver_manuscript(
    bot_token: str,
    chat_id: str,
    manuscript_path: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Deliver a completed manuscript via Telegram.

    Sends the manuscript file as a document, with a summary message.

    Args:
        bot_token: Telegram bot token.
        chat_id: Target chat ID.
        manuscript_path: Path to the manuscript file.
        metadata: Optional publication metadata dict.

    Returns:
        Telegram API response dict.
    """
    title = "Manuscript"
    if metadata:
        title = metadata.get("title", title)

    caption = f"📚 *{title}*"
    if metadata:
        subtitle = metadata.get("subtitle", "")
        if subtitle:
            caption += f"\n_{subtitle}_"
        blurb = metadata.get("synopsis_short", "")
        if blurb:
            caption += f"\n\n{blurb}"

    return await send_document(
        bot_token=bot_token,
        chat_id=chat_id,
        file_path=manuscript_path,
        caption=caption,
    )
