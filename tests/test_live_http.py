"""Opt-in real HTTP/SDK test against a running configured service."""

import json
import os

import pytest
from typesafe_sdk import AsyncTypeSafeClient, Choice, Noul, RetryPolicy, Score

from grok_judge_bridge import JudgeClient

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("GROK_JUDGE_LIVE_TEST") != "1"
        or not os.environ.get("GROK_JUDGE_LIVE_HTTP_BASE_URL"),
        reason="A live service URL and explicit Bot usage opt-in are required",
    ),
]


def questions():
    return {
        "production": Noul(
            instructions="Has this project completed its production deployment, according to current state only?"
        ),
        "status": Choice(
            instructions="Select this project's production deployment status from the supplied evidence only.",
            criteria={
                "not_deployed": "Production deployment has not begun",
                "deployed": "Production deployment completed",
            },
        ),
        "completion": Score(
            instructions="Rate production deployment completion from current state only.",
            criteria=["Not started", "Partly deployed", "Fully deployed"],
        ),
    }


async def test_live_http_projects_share_one_bot_with_distinct_evidence():
    url = os.environ["GROK_JUDGE_LIVE_HTTP_BASE_URL"]
    with JudgeClient(base_url=url, project_id="project-a") as client:
        a = client.system_one(
            state={
                "project": "project-a",
                "evidence": "Only a staging build exists. Production deployment has not started.",
            },
            questions=questions(),
        )
        assert a.nouls["production"].noul < 0.15
        assert a.choices["status"].choice == "not_deployed"
        assert a.scores["completion"].score < 0.5
    async with AsyncTypeSafeClient(
        base_url=url,
        api_key="local",
        model="jev-latest",
        timeout=240,
        retry=RetryPolicy(max_retries=0, timeout=None),
    ) as native:
        b = await native.system_one(
            state={
                "project": "project-b",
                "evidence": "Production deployment completed successfully. All production instances run the new release.",
            },
            questions=questions(),
            extra_headers={"X-Project-Id": "project-b"},
        )
        assert b.nouls["production"].noul > 0.85
        assert b.choices["status"].choice == "deployed"
        assert b.scores["completion"].score > 1.5
        assert {m.name for m in (await native.models.list()).models} == {"grok-bot", "jev-latest"}
    raw_a, raw_b = a.raw_http_response.json(), b.raw_http_response.json()
    assert raw_a["backend"] == raw_b["backend"] == "grok_bot"
    assert raw_a["project_id"] == "project-a"
    assert raw_b["project_id"] == "project-b"
    assert raw_a["task_id"] != raw_b["task_id"]
    print(
        json.dumps(
            {
                "http_live_tasks": [raw_a["task_id"], raw_b["task_id"]],
                "seconds": [raw_a["elapsed_seconds"], raw_b["elapsed_seconds"]],
                "judgments": 6,
                "projects": 2,
                "status": "passed",
            }
        )
    )
