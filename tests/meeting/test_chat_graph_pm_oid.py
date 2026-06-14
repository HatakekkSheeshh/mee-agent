"""pm_call forwards the logged-in user's token as the per-request bearer.

This is the per-user authorization seam: the chat turn carries pm_user_token
(the signed-in user's Microsoft Graph access token), and pm_call hands it to the
A2A client so pm-agent's JWT path validates it as that user — not a static env
token.
"""
from __future__ import annotations

from meeting.graphs.chat_graph.pm import make_pm_call
from meeting.services.pm_agent_client import PmAgentResult


class _RecordingClient:
    def __init__(self):
        self.kwargs = None

    async def send_message(self, text, *, task_id=None, context_id=None, data_part=None, bearer=None):
        self.kwargs = {
            "text": text,
            "task_id": task_id,
            "context_id": context_id,
            "data_part": data_part,
            "bearer": bearer,
        }
        return PmAgentResult(
            task_id="task-1", state="completed", text="ok",
            need_approval=False, issues=None, context_id="ctx-1",
        )


async def test_pm_call_forwards_user_oid_as_bearer():
    client = _RecordingClient()
    pm_call = make_pm_call(client)
    oid = "9c1f8e7a-1111-2222-3333-444455556666"

    state = {
        "user_message": "liệt kê issue overdue",
        "pm_user_token": oid,
        "pm_rounds": 0,
    }
    await pm_call(state)

    assert client.kwargs["bearer"] == oid
    assert client.kwargs["text"] == "liệt kê issue overdue"


async def test_pm_call_bearer_none_when_no_oid():
    client = _RecordingClient()
    pm_call = make_pm_call(client)

    await pm_call({"user_message": "x", "pm_rounds": 0})

    assert client.kwargs["bearer"] is None
