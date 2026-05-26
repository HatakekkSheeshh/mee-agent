"""
Memory Service — semantic search over past meeting events.

Sprint A (current): SQL-backed real impl using memory_events table.
    - save() INSERTs into memory_events
    - retrieve() SELECTs by user/topic/query

Phase F (future): Wire to AgentBase Memory Service (vector store) for semantic search.

Pattern:
    LangGraph node `read_memory` calls retrieve() before generating MoM
    LangGraph node `save_results` calls save() after generating MoM
    → Cross-meeting context: "Tuần trước Tuấn đã commit deploy v1"
"""
from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from meeting.db import repositories as repo

logger = logging.getLogger(__name__)


VALID_EVENT_TYPES = {
    "action_item", "decision", "commitment", "blocker", "update", "summary"
}


@dataclass
class MemoryEvent:
    """A single semantic event saved to memory."""
    meeting_id: str
    topic: str
    text: str
    event_type: str = "update"          # action_item / decision / commitment / blocker / update / summary
    speaker: Optional[str] = None
    deadline: Optional[str] = None
    metadata: dict = field(default_factory=dict)


class MemoryService:
    """
    SQL-backed memory service (Sprint A).
    Reads/writes `memory_events` table for cross-meeting context.
    """

    def __init__(self, user_id: str = "dev-local"):
        self.user_id = user_id  # legacy compat — actual user_id resolved at call time

    async def retrieve(
        self,
        query: str = "",
        top_k: int = 5,
        *,
        db_session: Optional[AsyncSession] = None,
        user_id: Optional[uuid.UUID] = None,
        topic: Optional[str] = None,
        exclude_meeting_id: Optional[uuid.UUID] = None,
        event_types: Optional[list[str]] = None,
        **kwargs: Any,
    ) -> list[MemoryEvent]:
        """
        Retrieve memory events. Returns [] if db_session/user_id missing.

        Args:
            query: free-text search via Postgres full-text
            top_k: max results
            db_session: required for DB access
            user_id: required to scope to user's events
            topic: optional topic filter (ILIKE match)
            exclude_meeting_id: skip events from this meeting (avoid retrieving from "self")
            event_types: filter by type (vd ['action_item', 'commitment'])
        """
        if db_session is None or user_id is None:
            logger.info(
                f"[MemoryService] retrieve(query={query[:60]!r}) → [] "
                f"(no db_session/user_id provided — stub mode)"
            )
            return []

        rows = await repo.retrieve_memory_events(
            db_session,
            user_id=user_id,
            query=query,
            topic=topic,
            exclude_meeting_id=exclude_meeting_id,
            event_types=event_types,
            limit=top_k,
        )
        events = [
            MemoryEvent(
                meeting_id=str(r.meeting_id),
                topic=r.topic or "",
                text=r.text,
                event_type=r.event_type,
                speaker=r.speaker,
                deadline=r.deadline,
                metadata=r.event_metadata or {},
            )
            for r in rows
        ]
        logger.info(
            f"[MemoryService] retrieved {len(events)} events "
            f"for query={query[:60]!r} topic={topic!r}"
        )
        return events

    async def save(
        self,
        events: list[MemoryEvent],
        *,
        db_session: Optional[AsyncSession] = None,
        user_id: Optional[uuid.UUID] = None,
        meeting_id: Optional[uuid.UUID] = None,
        **kwargs: Any,
    ) -> bool:
        """
        Save events to memory_events table.
        Without db_session → log only (mock mode for backward compat).
        """
        if db_session is None or user_id is None or meeting_id is None:
            logger.info(
                f"[MemoryService] save({len(events)} events) → noop "
                f"(no db_session/user_id/meeting_id — stub mode)"
            )
            for e in events:
                logger.debug(f"  would save: type={e.event_type} text={e.text[:80]!r}")
            return False

        payload = []
        for e in events:
            event_type = e.event_type if e.event_type in VALID_EVENT_TYPES else "update"
            payload.append({
                "event_type": event_type,
                "text": e.text,
                "topic": e.topic,
                "speaker": e.speaker,
                "deadline": e.deadline,
                "metadata": e.metadata,
            })

        count = await repo.save_memory_events_bulk(
            db_session, payload, user_id=user_id, meeting_id=meeting_id
        )
        logger.info(
            f"[MemoryService] saved {count}/{len(events)} events to DB "
            f"for user={user_id}"
        )
        return True


_default_service: Optional[MemoryService] = None


def get_memory_service() -> MemoryService:
    """Singleton accessor. Service is stateless — same instance reused."""
    global _default_service
    if _default_service is None:
        _default_service = MemoryService(user_id=os.getenv("MEE_USER_ID", "dev-local"))
    return _default_service
