import json
from pathlib import Path

import pytest

from grok_judge_bridge.config import BridgeError
from grok_judge_bridge.provider import AsyncGrokBotProvider
from grok_judge_bridge.service import evaluate

REQUEST = json.loads((Path(__file__).parents[1] / "examples/judgments.json").read_text())
RAW = {
    "answers": {
        "claim_support": {"supported": 0.02, "contradicted": 0.03, "insufficient": 0.95},
        "build_passed": 0.98,
        "severity": {"0": 0.01, "1": 0.02, "2": 0.97},
    }
}


class FixedTransport:
    last_task_id = "GJ_test"
    last_elapsed_seconds = 0.1

    def __init__(self, text):
        self.text = text
        self.calls = 0

    async def request(self, prompt):
        self.calls += 1
        self.prompt = prompt
        return self.text


async def test_three_primitives_use_upstream_conversion_and_truthful_metadata():
    transport = FixedTransport(json.dumps(RAW))
    result = await evaluate(
        {**REQUEST, "model": "jev-latest"}, provider=AsyncGrokBotProvider(transport=transport)
    )
    assert result["answers"]["claim_support"]["choice"] == "insufficient"
    assert result["answers"]["build_passed"]["noul"] == 0.98
    assert result["answers"]["severity"]["score"] == pytest.approx(1.96)
    assert result["backend"] == "grok_bot"
    assert result["model"] == "grok-bot"
    assert result["requested_model"] == "jev-latest"
    assert result["usage"]["input_tokens"] is None
    assert result["probability_semantics"] == "llm_estimate_uncalibrated"
    assert "debug" not in result
    assert transport.calls == 1


@pytest.mark.parametrize(
    "text",
    [
        "not JSON",
        '{"answers":{},"answers":{}}',
        '{"answers":{"claim_support":{"supported":NaN}}}',
        json.dumps({"answers": {"wrong_id": 1}}),
        json.dumps({"answers": {**RAW["answers"], "build_passed": 1.1}}),
        json.dumps({"answers": {**RAW["answers"], "severity": {"0": 0.1, "1": 0.1, "2": 0.1}}}),
        json.dumps({"answers": {**RAW["answers"], "severity": {"0": -0.1, "1": 0.1, "2": 1}}}),
    ],
)
async def test_invalid_replies_are_failed_batches_with_no_retry(text):
    transport = FixedTransport(text)
    with pytest.raises(BridgeError):
        await evaluate(REQUEST, provider=AsyncGrokBotProvider(transport=transport))
    assert transport.calls == 1


@pytest.mark.parametrize(
    "batch",
    [
        {"state": "x", "questions": {}},
        {
            "state": "x",
            "questions": {"x": {"type": "score", "instructions": "x", "criteria": ["one"]}},
        },
        {**REQUEST, "state": {"value": float("nan")}},
        {**REQUEST, "state": "x" * 1_000_001},
        {**REQUEST, "model": "other-provider"},
    ],
)
async def test_bad_requests_do_not_send(batch):
    transport = FixedTransport(json.dumps(RAW))
    with pytest.raises(BridgeError):
        await evaluate(batch, provider=AsyncGrokBotProvider(transport=transport))
    assert transport.calls == 0
