"""
WhatsApp Channel — outbound document delivery via Twilio WhatsApp API.

Sends completed manuscripts as documents or messages to a configured number.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

TWILIO_API = "https://api.twilio.com/2010-04-01"


async def send_message(
    account_sid: str,
    auth_token: str,
    from_number: str,
    to_number: str,
    body: str,
) -> Dict[str, Any]:
    """Send a text message via WhatsApp (Twilio).

    Args:
        account_sid: Twilio account SID.
        auth_token: Twilio auth token.
        from_number: Sender WhatsApp number (whatsapp:+1234567890).
        to_number: Recipient WhatsApp number.
        body: Message body.

    Returns:
        Twilio API response dict.
    """
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{TWILIO_API}/Accounts/{account_sid}/Messages.json",
            auth=(account_sid, auth_token),
            data={
                "From": from_number,
                "To": to_number,
                "Body": body,
            },
        )
        resp.raise_for_status()
        return resp.json()


async def send_document(
    account_sid: str,
    auth_token: str,
    from_number: str,
    to_number: str,
    file_path: str,
    caption: str = "",
) -> Dict[str, Any]:
    """Send a document via WhatsApp (Twilio).

    Note: Twilio WhatsApp requires the media URL to be publicly accessible.
    For local files, you'd need to upload to a public URL first.
    This function sends the file path as a media URL if it's a URL,
    or falls back to sending the caption as a text message.

    Args:
        account_sid: Twilio account SID.
        auth_token: Twilio auth token.
        from_number: Sender WhatsApp number.
        to_number: Recipient WhatsApp number.
        file_path: Path or URL to the file.
        caption: Optional caption.

    Returns:
        Twilio API response dict.
    """
    data = {
        "From": from_number,
        "To": to_number,
    }

    if file_path.startswith("http"):
        data["MediaUrl"] = file_path
        if caption:
            data["Body"] = caption
    else:
        # For local files, send path info as text
        path = Path(file_path)
        if path.exists():
            size_kb = path.stat().st_size / 1024
            data["Body"] = (
                f"📚 Manuscript ready!\n\n"
                f"File: {path.name}\n"
                f"Size: {size_kb:.1f} KB\n"
                f"{caption}"
            )
        else:
            data["Body"] = f"Manuscript file not found: {file_path}"

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{TWILIO_API}/Accounts/{account_sid}/Messages.json",
            auth=(account_sid, auth_token),
            data=data,
        )
        resp.raise_for_status()
        return resp.json()


async def deliver_manuscript(
    account_sid: str,
    auth_token: str,
    from_number: str,
    to_number: str,
    manuscript_path: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Deliver a completed manuscript via WhatsApp.

    Args:
        account_sid: Twilio account SID.
        auth_token: Twilio auth token.
        from_number: Sender WhatsApp number.
        to_number: Recipient WhatsApp number.
        manuscript_path: Path to the manuscript file.
        metadata: Optional publication metadata.

    Returns:
        Twilio API response dict.
    """
    title = "Manuscript"
    if metadata:
        title = metadata.get("title", title)

    caption = f"📚 {title}"
    if metadata:
        blurb = metadata.get("synopsis_short", "")
        if blurb:
            caption += f"\n\n{blurb}"

    return await send_document(
        account_sid=account_sid,
        auth_token=auth_token,
        from_number=from_number,
        to_number=to_number,
        file_path=manuscript_path,
        caption=caption,
    )
