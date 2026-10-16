import gzip
import json

import httpx2
import pytest
import zstandard
from jsonschema import Draft202012Validator
from starlette.testclient import TestClient
from typesafe_sdk import AsyncTypeSafeClient, Choice, Noul, RetryPolicy, Score

from grok_judge_bridge import AsyncJudgeClient, JudgeClient
from grok_judge_bridge.config import BridgeError, Settings
from grok_judge_bridge.http import create_app, openapi_document
from grok_judge_bridge.provider import AsyncGrokBotProvider
from grok_judge_bridge.service import evaluate

REQUEST = {
    "state": "The build passed. Integration tests have not run.",
    "model": "jev-latest",
    "questions": {
        "claim": {
            "type": "choice",
            "instructions": "All tests passed?",
            "criteria": {"supported": "Every suite passed", "insufficient": "Missing test results"},
        },
        "passed": {"type": "noul", "instructions": "Build passed?"},
        "score": {
            "type": "score",
            "instructions": "How complete is testing?",
            "criteria": ["None", "Some", "All"],
        },
    },
}


class FixedTransport:
    last_task_id = "GJ_http_test"

    async def request(self, prompt):
        return '{"answers":{"claim":{"supported":0.02,"insufficient":0.98},"passed":0.99,"score":{"0":0,"1":0.99,"2":0.01}}}'


@pytest.fixture
def app(tmp_path):
    calls = []
    settings = Settings(bot_id="one-shared-bot", mcp_command=["unused"], state_dir=tmp_path)

    async def evaluator(batch, *, settings):
        calls.append(settings.bot_id)
        return await evaluate(batch, provider=AsyncGrokBotProvider(transport=FixedTransport()))

    instance = create_app(settings=settings, evaluator=evaluator)
    instance.state.calls = calls
    return instance


async def test_native_async_sdk_and_shared_project_routing(app):
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app)) as http:
        async with AsyncTypeSafeClient(
            base_url="http://bridge",
            api_key="local",
            compression=False,
            http_client=http,
            retry=RetryPolicy(max_retries=0),
        ) as native:
            response = await native.system_one(
                REQUEST["state"],
                {
                    "claim": Choice(
                        instructions="All tests passed?",
                        criteria={
                            "supported": "Every suite passed",
                            "insufficient": "Missing test results",
                        },
                    ),
                    "passed": Noul(instructions="Build passed?"),
                    "score": Score(
                        instructions="Testing completeness?", criteria=["None", "Some", "All"]
                    ),
                },
                extra_headers={"X-Project-Id": "project-a"},
            )
            assert response.choices["claim"].choice == "insufficient"
            assert response.nouls["passed"].noul == 0.99
            assert response.scores["score"].score == pytest.approx(1.01)
            assert response.usage.input_tokens is None
            assert response.model == "grok-bot"
            raw = response.raw_http_response.json()
            assert raw["project_id"] == "project-a"
            assert raw["request_id"] == response.raw_http_response.headers["X-Request-Id"]
            models = await native.models.list()
            assert {model.name for model in models.models} == {"grok-bot", "jev-latest"}
        async with AsyncJudgeClient(
            base_url="http://bridge",
            project_id="project-b",
            transport=httpx2.ASGITransport(app),
            compression=False,
        ) as own:
            response = await own.system_one(REQUEST["state"], REQUEST["questions"])
            assert response.raw_http_response.json()["project_id"] == "project-b"
    assert app.state.calls == ["one-shared-bot", "one-shared-bot"]


@pytest.mark.parametrize(
    "encoding,compress",
    [
        ("gzip", gzip.compress),
        ("x-gzip", gzip.compress),
        ("zstd", zstandard.ZstdCompressor().compress),
        ("zstd", zstandard.ZstdCompressor(write_content_size=False).compress),
    ],
)
def test_compressed_requests_match_plain_json(app, encoding, compress):
    with TestClient(app) as client:
        response = client.post(
            "/v1/systemone",
            content=compress(json.dumps(REQUEST).encode()),
            headers={"Content-Encoding": encoding},
        )
    assert response.status_code == 200
    assert response.json()["answers"]["claim"]["choice"] == "insufficient"


@pytest.mark.parametrize(
    "body,headers,status",
    [
        (b'{"state":1,"state":2}', {}, 400),
        (b'{"state":NaN}', {}, 400),
        (b"not-json", {}, 400),
        (b"{}", {}, 422),
        (b"{}", {"Content-Encoding": "br"}, 415),
        (b"bad-compressed", {"Content-Encoding": "gzip"}, 400),
        (b"bad-compressed", {"Content-Encoding": "zstd"}, 400),
        (b"x" * 1_000_001, {}, 413),
        (gzip.compress(b"x" * 1_000_001), {"Content-Encoding": "gzip"}, 413),
        (zstandard.ZstdCompressor().compress(b"x" * 1_000_001), {"Content-Encoding": "zstd"}, 413),
    ],
    ids=[
        "duplicate-json",
        "nan",
        "invalid-json",
        "missing-fields",
        "unsupported-encoding",
        "bad-gzip",
        "bad-zstd",
        "large-body",
        "large-gzip",
        "large-zstd",
    ],
)
def test_rejected_http_input_never_reaches_bot(app, body, headers, status):
    with TestClient(app) as client:
        response = client.post("/v1/systemone", content=body, headers=headers)
    assert response.status_code == status
    assert response.json()["batch_status"] == "unprocessed"
    assert app.state.calls == []


def test_authentication_precedes_dispatch_and_errors_keep_codes():
    calls = []

    async def unavailable(batch, *, settings):
        calls.append(batch)
        raise BridgeError("Pending task; reconcile first.", code="pending_task", status_code=409)

    with TestClient(create_app(evaluator=unavailable, api_key="test-only")) as client:
        assert client.post("/v1/systemone", json=REQUEST).status_code == 401
        assert client.get("/v1/models").status_code == 401
        response = client.post(
            "/v1/systemone", json=REQUEST, headers={"Authorization": "Bearer test-only"}
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "pending_task"
    assert len(calls) == 1


def test_openapi_refs_and_response_schema_are_resolvable(app):
    document = openapi_document()
    schema = {"$ref": "#/components/schemas/Evaluation", "components": document["components"]}
    Draft202012Validator(schema).validate(REQUEST)
    with TestClient(app) as client:
        response = client.post("/v1/systemone", json=REQUEST).json()
        assert client.get("/health").json()["shared_bot"] is True
        assert client.get("/openapi.json").json()["openapi"] == "3.1.0"
    schema["$ref"] = "#/components/schemas/JudgmentResponse"
    Draft202012Validator(schema).validate(response)


def test_client_rejects_invalid_project_id_without_network():
    with pytest.raises(ValueError):
        JudgeClient(project_id="not a header\nvalue")
