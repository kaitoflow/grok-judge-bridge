import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from grok_judge_bridge.config import BridgeError, Settings
from grok_judge_bridge.transport import BotTransport, matching_reply


def result(data):
    return SimpleNamespace(isError=False, structuredContent=data)


def settings(tmp_path, timeout=5):
    return Settings.model_construct(
        bot_id="test-bot",
        mcp_command=["unused"],
        timeout_seconds=timeout,
        poll_seconds=0.001,
        state_dir=tmp_path,
    )


@pytest.mark.parametrize(
    "entry",
    [
        {"role": "user"},
        {"role": "tool"},
        {"role": "assistant", "truncated": True},
        {"role": "assistant", "isStreaming": True},
    ],
)
def test_echo_tool_and_incomplete_entries_are_not_results(entry):
    assert (
        matching_reply({"entries": [{**entry, "text": "GJ_1 BEGIN\n{}\nGJ_1 END"}]}, "GJ_1") is None
    )


def test_wrong_task_and_unwrapped_text_are_not_results():
    rows = [
        {"role": "assistant", "text": "GJ_other BEGIN\n{}\nGJ_other END"},
        {"role": "assistant", "text": "The answer is yes."},
    ]
    assert matching_reply({"entries": rows}, "GJ_1") is None


def test_complete_matching_markdown_json_is_accepted():
    data = {
        "entries": [
            {"role": "assistant", "text": 'GJ_1 BEGIN\n```json\n{"answers":{}}\n```\nGJ_1 END'}
        ]
    }
    assert json.loads(matching_reply(data, "GJ_1")) == {"answers": {}}


async def test_timeout_is_not_resent_and_blocks_following_dispatch(tmp_path):
    class Silent:
        sends = 0

        async def call_tool(self, name, args):
            if name == "gbot_send":
                self.sends += 1
                return result({"status": "sent"})
            return result({"cursor": "before", "entries": []})

    session = Silent()

    @asynccontextmanager
    async def connect(_):
        yield session

    transport = BotTransport(settings(tmp_path, 0.03), connect=connect)
    with pytest.raises(BridgeError, match="timed out"):
        await transport.request("test")
    assert session.sends == 1
    assert transport.pending()["task_id"] == transport.last_task_id
    with pytest.raises(BridgeError, match="pending task"):
        await transport.request("another batch")
    assert session.sends == 1


async def test_two_instances_for_one_bot_serialize_and_do_not_cross_results(tmp_path):
    class Session:
        active = 0
        max_active = 0
        completed = []

        async def call_tool(self, name, args):
            if name == "gbot_send":
                self.active += 1
                self.max_active = max(self.max_active, self.active)
                self.task_id = args["requestId"]
                await asyncio.sleep(0.02)
                return result({"status": "sent"})
            if not args["full"]:
                return result({"cursor": "before", "entries": []})
            self.active -= 1
            self.completed.append(self.task_id)
            return result(
                {
                    "entries": [
                        {
                            "role": "assistant",
                            "text": f'{self.task_id} BEGIN\n{{"task":"{self.task_id}"}}\n{self.task_id} END',
                        }
                    ]
                }
            )

    session = Session()

    @asynccontextmanager
    async def connect(_):
        yield session

    transports = [BotTransport(settings(tmp_path), connect=connect) for _ in range(2)]
    payloads = await asyncio.gather(*(t.request("test") for t in transports))
    assert session.max_active == 1
    assert len(set(session.completed)) == 2
    assert [json.loads(p)["task"] for p in payloads] == [t.last_task_id for t in transports]
    assert all(t.pending() is None for t in transports)


async def test_reconcile_reads_without_resending(tmp_path):
    task = "GJ_pending"

    class Finished:
        async def call_tool(self, name, args):
            assert name == "gbot_thread"
            return result(
                {"entries": [{"role": "assistant", "text": f"{task} BEGIN\n{{}}\n{task} END"}]}
            )

    @asynccontextmanager
    async def connect(_):
        yield Finished()

    transport = BotTransport(settings(tmp_path), connect=connect)
    transport.pending_path.write_text(json.dumps({"task_id": task, "cursor": None}))
    status = await transport.reconcile()
    assert status["completed_task_id"] == task
    assert transport.pending() is None
