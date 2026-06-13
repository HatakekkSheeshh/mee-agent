"""Live-verify the multi-turn merge fix (PRIORITY-1 bug).

Drives the REAL chat graph turn-by-turn via `run_chat_turn`, sharing ONE
session_id so `recent_messages` + the Postgres checkpointer carry conversation
history exactly as in production. `FakeLLM` can't exercise this — the bug only
reproduces against the real chat model — so this needs the full stack (the same
DATABASE_URL / LLM / embedding env that `run_meeting.py` uses).

Run from the repo root:

    venv/bin/python scripts/verify_multiturn.py                  # repro #1 (email)
    venv/bin/python scripts/verify_multiturn.py --meeting <id>   # + repro #2 (create_task)
        [--label "Meeting 1"]                                    # session label for repro #2

PASS criteria
  #1 email merge (2 turns, no meeting needed):
       t1 "email đến andvd6"                      → agent asks for subject/body
       t2 "tiêu đề: …, nội dung: …"               → MUST fire send_email (interrupt),
                                                     not re-ask.
  #2 create_task accumulation (3 turns, needs a meeting with an agenda-only
     recording): by the final turn the agent MUST interrupt with create_task,
     not re-ask or re-dump the agenda.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

# Make `import meeting` work when run as `python scripts/verify_multiturn.py`
# (sys.path[0] is scripts/, not the repo root).
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from dotenv import load_dotenv  # noqa: E402

# Same loader contract as the app: keep '$' in passwords intact.
load_dotenv(dotenv_path=os.path.join(_ROOT, ".env"), override=True, interpolate=False)

from meeting.db import repositories as repo  # noqa: E402
from meeting.db.base import AsyncSessionLocal  # noqa: E402
from meeting.graphs import (  # noqa: E402
    close_checkpointer,
    get_checkpointer,
    init_checkpointer,
    run_chat_turn,
)


def _summarize(res: dict) -> str:
    if res.get("status") == "interrupted":
        pa = res.get("pending_action") or {}
        return f"INTERRUPTED  tool={pa.get('tool')!r} kind={pa.get('kind')!r}"
    reply = (res.get("reply") or "").replace("\n", " ")
    return f"COMPLETE     reply={reply[:140]!r}"


async def _turn(session, cp, *, sid, uid, mid, text) -> dict:
    print(f"  > user: {text}")
    res = await run_chat_turn(
        session_id=sid, user_id=uid, user_message=text,
        meeting_id=mid, session=session, checkpointer=cp,
    )
    await session.commit()  # persist messages so the next turn recalls them
    print(f"    {_summarize(res)}")
    return res


def _is_tool_interrupt(res: dict, tool: str) -> bool:
    return res.get("status") == "interrupted" and (
        (res.get("pending_action") or {}).get("tool") == tool
    )


async def repro_email(session, cp, *, uid) -> bool:
    print("\n=== Repro #1: email merge across 2 turns ===")
    chat = await repo.create_chat_session(session, user_id=__import__("uuid").UUID(uid),
                                           meeting_id=None, title="verify-email")
    await session.commit()
    sid = str(chat.id)
    await _turn(session, cp, sid=sid, uid=uid, mid=None, text="email đến andvd6")
    r2 = await _turn(session, cp, sid=sid, uid=uid, mid=None,
                     text="tiêu đề: Họp chiều nay, nội dung: Họp gấp lúc 3h")
    ok = _is_tool_interrupt(r2, "send_email")
    print(f"  RESULT: {'PASS ✅ merged → send_email' if ok else 'FAIL ❌ re-asked / no send_email'}")
    return ok


async def repro_create_task(session, cp, *, uid, meeting_id, label) -> bool:
    print(f"\n=== Repro #2: create_task accumulation (meeting={meeting_id}, label={label!r}) ===")
    chat = await repo.create_chat_session(
        session, user_id=__import__("uuid").UUID(uid),
        meeting_id=__import__("uuid").UUID(meeting_id), title="verify-create-task",
    )
    await session.commit()
    sid = str(chat.id)
    await _turn(session, cp, sid=sid, uid=uid, mid=meeting_id,
                text=f"tạo task cho phiên {label}")
    await _turn(session, cp, sid=sid, uid=uid, mid=meeting_id,
                text="gán cho hieunq3 và anhvd6")
    r3 = await _turn(session, cp, sid=sid, uid=uid, mid=meeting_id,
                     text="hạn đến 20/06/2026")
    ok = _is_tool_interrupt(r3, "create_task")
    print(f"  RESULT: {'PASS ✅ accumulated → create_task' if ok else 'FAIL ❌ re-asked / re-dumped agenda'}")
    return ok


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--meeting", default=None, help="meeting_id with an agenda-only recording (repro #2)")
    ap.add_argument("--label", default="Meeting 1", help="session label referenced in repro #2 turns")
    args = ap.parse_args()

    print(f"LLM_MODEL={os.getenv('LLM_MODEL')!r}  (bug only reproduces vs the real chat model)")
    await init_checkpointer()
    cp = get_checkpointer()
    results: list[tuple[str, bool]] = []
    try:
        async with AsyncSessionLocal() as session:
            user = await repo.get_or_create_dev_user(session)
            await session.commit()
            uid = str(user.id)
            results.append(("email-merge", await repro_email(session, cp, uid=uid)))
            if args.meeting:
                results.append(
                    ("create_task-accumulation",
                     await repro_create_task(session, cp, uid=uid,
                                             meeting_id=args.meeting, label=args.label))
                )
    finally:
        await close_checkpointer()

    print("\n=== SUMMARY ===")
    for name, ok in results:
        print(f"  {name:28s} {'PASS' if ok else 'FAIL'}")
    return 0 if all(ok for _, ok in results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
