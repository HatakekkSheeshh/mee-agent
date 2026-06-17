"""remember_fact / forget_fact — auto-run tools that manage durable chat knowledge
in AgentBase Memory so it survives "Xóa hội thoại" and is recalled in later turns.

Scope decides the actor namespace (read==write, the actor-granularity decision):
  scope="user"    → user_prefs/<ms_oid>        (per-user: "gọi tôi là Ronaldo")
  scope="project" → project_facts/<meeting_id> (shared across the project's users)

These run automatically in the background (no HITL): the executor validates +
resolves the namespace in-turn, then dispatches the AgentBase write fire-and-forget
so the chat reply isn't blocked. AgentBase is insert-only (DELETE is 403), so
forget_fact doesn't delete — it writes a newer `active=0` tombstone keyed by the
fact's text; recall hides it under newest-wins, and remember_fact re-activates it.

Best-effort: with MEMORY_ID unset they return {"status": "disabled"} and never
raise — capturing knowledge must never break a chat turn.
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import Callable, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from src import memory_client as mc
from src.db import repositories as repo
from src.services.tools._registry import tool

logger = logging.getLogger(__name__)


def _dispatch_write(writer: Callable[[], None]) -> None:
    """Fire the AgentBase write off the turn's critical path (fire-and-forget).

    The insert is a blocking urllib round-trip + token fetch; awaiting it would
    delay the chat reply. It uses no DB session, so it's safe to outlive the
    request. Runs in the loop's default executor; falls back to a synchronous
    call when there's no running loop (e.g. a script / sync caller).
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        writer()
        return
    loop.run_in_executor(None, writer)


async def _resolve_target(
    scope: str, args: dict, session: AsyncSession, user_id: uuid.UUID
) -> Tuple[Optional[str], str, Optional[str], Optional[dict]]:
    """Resolve (namespace, author_oid, project_title, error) for a fact op.

    Shared by remember_fact + forget_fact so read==write stays consistent. `error`
    is a non-None result dict when the target can't be resolved (return it as-is).
    """
    # ms_oid anchors per-user scope AND audits the author of a shared project fact.
    author_oid = await repo.get_user_ms_oid(session, user_id)
    if scope == "user":
        if not author_oid:
            return None, "", None, {"error": "không tìm thấy ms_oid của user — không thể thao tác fact scope='user'"}
        return mc.fact_namespace("user", author_oid), author_oid, None, None

    meeting_id = (args.get("meeting_id") or "").strip()
    if not meeting_id:
        return None, author_oid or "", None, {"error": "scope='project' cần một meeting_id (dự án)"}
    # Resolve the project's natural-language name so the stored fact reads clearly
    # ("(Dự án X) …") instead of a raw UUID. Best-effort — never a gate.
    project_title = None
    try:
        meeting = await repo.get_meeting(session, uuid.UUID(meeting_id))
        project_title = (getattr(meeting, "title", None) or "").strip() or None
    except Exception as e:  # noqa: BLE001 — enrichment must not block
        logger.info("[facts] title lookup skipped: %s", e)
    return mc.fact_namespace("project", meeting_id), author_oid or "", project_title, None


_SCOPE_PROP = {
    "type": "string",
    "enum": ["project", "user"],
    "default": "project",
    "description": (
        "'user' = riêng user này (sở thích, danh xưng); "
        "'project' = chia sẻ trong dự án/cuộc họp hiện tại."
    ),
}
_MEETING_PROP = {
    "type": "string",
    "format": "uuid",
    "description": "Dự án để gắn fact (chỉ dùng cho scope='project'). Tự inject.",
}


@tool(
    name="remember_fact",
    description=(
        "Lưu một SỰ THẬT lâu dài để nhớ cho các lượt/cuộc trò chuyện sau (vượt qua "
        "'Xóa hội thoại'). Dùng khi user khẳng định điều cần nhớ ('gọi tôi là Ronaldo', "
        "'deadline dời sang 30/06', 'X phụ trách module Y') HOẶC khi bạn suy luận ra "
        "một sự thật bền vững đáng nhớ. scope='user' cho sở thích/danh xưng của CHÍNH "
        "user (vd cách xưng hô); scope='project' cho sự thật về dự án/cuộc họp mà cả "
        "nhóm nên nhớ. Cũng dùng để BẬT LẠI một fact đã tắt. Tự động lưu CHẠY NGẦM, "
        "KHÔNG cần user duyệt."
    ),
    side_effect=False,
    schema={
        "type": "object",
        "required": ["text"],
        "properties": {
            "text": {"type": "string", "description": "Câu sự thật cần nhớ, viết gọn và tự đủ nghĩa."},
            "scope": _SCOPE_PROP,
            "meeting_id": _MEETING_PROP,
        },
    },
)
async def remember_fact(args: dict, *, session: AsyncSession, user_id: uuid.UUID) -> dict:
    """Persist (or re-activate) one durable fact in the scope-appropriate namespace."""
    text = (args.get("text") or "").strip()
    if not text:
        return {"error": "remember_fact cần `text` không rỗng"}

    scope = (args.get("scope") or "project").strip().lower()
    if scope not in ("project", "user"):
        return {"error": f"scope không hợp lệ: {scope!r} (chỉ 'project' hoặc 'user')"}

    # Best-effort: never touch the DB/network when AgentBase memory is unconfigured.
    if not os.getenv("MEMORY_ID"):
        logger.info("[remember_fact] MEMORY_ID unset → disabled (no write)")
        return {"status": "disabled", "note": "AgentBase memory chưa được cấu hình"}

    namespace, author_oid, project_title, err = await _resolve_target(scope, args, session, user_id)
    if err:
        return err

    # key identifies the logical fact by its RAW text (so a decorated project body
    # and a later forget_fact on the same raw text resolve to the same fact).
    key = mc.fact_key(text)
    body = f"(Dự án {project_title}) {text}" if (scope == "project" and project_title) else text
    session_id = (args.get("session_id") or "").strip()

    def _write() -> None:
        # Background, best-effort: a failure is logged, never surfaced.
        try:
            # Pollution guard: skip if this exact fact is already active (no approval
            # gate now). A forgotten fact isn't in the active list → re-insert revives it.
            if body in mc.list_fact_records(namespace):
                logger.info("[remember_fact] duplicate skipped ns=%s", namespace)
                return
            mc.insert_fact_record(
                body, namespace=namespace, scope=scope, active=True, key=key,
                author_oid=author_oid or "", session_id=session_id,
            )
        except Exception as e:  # noqa: BLE001 — capture must never break anything
            logger.warning("[remember_fact] background write failed: %s", e)

    _dispatch_write(_write)
    logger.info("[remember_fact] scheduled scope=%s ns=%s len=%d", scope, namespace, len(body))
    out = {"status": "remembered", "scope": scope, "namespace": namespace, "text": body}
    if project_title:
        out["project_title"] = project_title
    return out


@tool(
    name="forget_fact",
    description=(
        "TẮT (ngừng dùng) một sự thật đã nhớ trước đó, mà KHÔNG xóa hẳn. Dùng khi user "
        "bảo đừng dùng nữa ('đừng gọi tôi là Ronaldo nữa', 'bỏ ghi nhớ deadline 30/06'). "
        "Truyền `text` đúng/sát nội dung đã nhớ và `scope` tương ứng. Sau này muốn dùng "
        "lại thì gọi `remember_fact`. Tự động CHẠY NGẦM, KHÔNG cần user duyệt."
    ),
    side_effect=False,
    schema={
        "type": "object",
        "required": ["text"],
        "properties": {
            "text": {"type": "string", "description": "Nội dung fact cần tắt (đúng/sát bản đã nhớ)."},
            "scope": _SCOPE_PROP,
            "meeting_id": _MEETING_PROP,
        },
    },
)
async def forget_fact(args: dict, *, session: AsyncSession, user_id: uuid.UUID) -> dict:
    """Hide a fact by writing a newer `active=0` tombstone keyed by its text."""
    text = (args.get("text") or "").strip()
    if not text:
        return {"error": "forget_fact cần `text` không rỗng"}

    scope = (args.get("scope") or "project").strip().lower()
    if scope not in ("project", "user"):
        return {"error": f"scope không hợp lệ: {scope!r} (chỉ 'project' hoặc 'user')"}

    if not os.getenv("MEMORY_ID"):
        logger.info("[forget_fact] MEMORY_ID unset → disabled (no write)")
        return {"status": "disabled", "note": "AgentBase memory chưa được cấu hình"}

    namespace, author_oid, _title, err = await _resolve_target(scope, args, session, user_id)
    if err:
        return err

    key = mc.fact_key(text)
    session_id = (args.get("session_id") or "").strip()

    def _write() -> None:
        try:
            mc.insert_fact_record(
                text, namespace=namespace, scope=scope, active=False, key=key,
                author_oid=author_oid or "", session_id=session_id,
            )
        except Exception as e:  # noqa: BLE001 — best-effort
            logger.warning("[forget_fact] background write failed: %s", e)

    _dispatch_write(_write)
    logger.info("[forget_fact] scheduled scope=%s ns=%s key=%s", scope, namespace, key)
    return {"status": "forgotten", "scope": scope, "namespace": namespace, "text": text}
