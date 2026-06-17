"""send_email — side-effect tool (MOCK; Phase E wires MS Graph)."""
from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.services.tools._registry import tool

logger = logging.getLogger(__name__)


@tool(
    name="send_email",
    description=(
        "Send an email to one or more recipients. "
        "Use when user explicitly asks to email someone the MoM, summary, action items, etc. "
        "REQUIRES user approval before execution (side-effect)."
    ),
    side_effect=True,
    schema={
        "type": "object",
        "required": ["to", "subject", "body"],
        "properties": {
            "to": {"type": "string", "description": "Comma-separated recipients"},
            "subject": {"type": "string"},
            "body": {"type": "string"},
            "attachments": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of file paths to attach",
            },
        },
    },
)
async def send_email(args: dict, *, session: AsyncSession, user_id: uuid.UUID) -> dict:
    """MOCK: simulate sending email. Phase E will wire MS Graph."""
    to = args.get("to", "")
    subject = args.get("subject", "")
    body = args.get("body", "")
    logger.info(f"[MOCK send_email] to={to!r} subject={subject!r} body_len={len(body)}")
    return {
        "status": "sent_mock",
        "to": to,
        "subject": subject,
        "message_id": f"mock-{uuid.uuid4().hex[:8]}",
        "note": "Mock execution — Phase E wires MS Graph for real send.",
    }
