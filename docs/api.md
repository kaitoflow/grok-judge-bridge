# Grok Judge API

## Run

```sh
grok-judge serve --host 127.0.0.1 --port 8018
```

Base URL: `http://127.0.0.1:8018`. OpenAPI 3.1: `GET /openapi.json`.

| Method | Path | Result |
| --- | --- | --- |
| POST | `/v1/systemone` | Evaluate typed questions |
| POST | `/v1/decisions` | Alias for `/v1/systemone` |
| GET | `/v1/models` | List `grok-bot` and `jev-latest` |
| GET | `/health` | Process health and shared-Bot mode |
| GET | `/openapi.json` | Request and typed response schemas |

## One Bot across projects

Every project uses the **same Bot configured in `GROK_JUDGE_CONFIG`**. MCP, HTTP, CLI and direct Python requests share a cross-process lock when they use the same `state_dir`. Every batch gets a new task ID; the bridge accepts only the complete matching reply. Judgment results are not cached.

Each request supplies its own evidence, applicable project rules, revision and context in `state`. The Bot is instructed to judge only this request. `X-Project-Id` labels the response for the caller; it does not select a different Bot, load project files or add previous project history. This is request matching and a current-evidence instruction, not a separate model context per project.

## Request

```sh
curl http://127.0.0.1:8018/v1/systemone \
  -H 'Content-Type: application/json' \
  -H 'X-Project-Id: checkout-service' \
  --data-binary @examples/judgments.json
```

```json
{
  "model": "grok-bot",
  "state": {"log": "Unit tests passed. Integration tests were not run."},
  "questions": {
    "all_tests": {
      "type": "choice",
      "instructions": "Does log establish that every test suite passed?",
      "criteria": {
        "supported": "Every suite has a passing result",
        "contradicted": "A suite has a failing result",
        "insufficient": "A required result is missing"
      }
    }
  }
}
```

`state` and `instructions` accept a string, object or array. `model` defaults to `grok-bot`; legacy `jev-*` identifiers route to the same Bot. The actual response model is always identified as `grok-bot`.

| Type | Criteria | Answer |
| --- | --- | --- |
| `choice` | Object mapping 1-255 option names to descriptions or null | `choice`, `probabilities`, `confidence` |
| `score` | Array of 2-10 ordered descriptions | Zero-based weighted `score`, `legend`, `probabilities`, `confidence` |
| `noul` | Optional `true` and `false` descriptions | `noul`, the estimated probability of yes |

Up to 256 questions and 1 MB of UTF-8 JSON per request. Plain JSON, gzip and zstd bodies are supported; compressed and decoded sizes are both limited. Probability values must be finite and within `[0,1]`; distributions must sum to one.

## Response

The response keeps TypeSafe's `model`, `answers` and `usage` fields. Unreported token counts are `null`.

| Additional field | Meaning |
| --- | --- |
| `backend` | `grok_bot` |
| `requested_model` | Original routing model identifier |
| `task_id` | Unique Bot task ID |
| `request_id` | HTTP ID, also returned as `X-Request-Id` |
| `project_id` | Supplied `X-Project-Id`, when present |
| `elapsed_seconds` | Bot operation time, including queue waiting |
| `probability_semantics` | `llm_estimate_uncalibrated` |

## Python clients

The package clients extend TypeSafe's official MIT-licensed SDK, retaining its typed questions, answers, model resource, context managers, compression and per-call options.

```python
from grok_judge_bridge import JudgeClient
from typesafe_sdk import Choice, Noul, Score

with JudgeClient(project_id="checkout-service") as client:
    response = client.system_one(
        state={"log": "Build passed; integration tests have not run."},
        questions={
            "passed": Noul(instructions="Did the build pass?"),
            "coverage": Choice(
                instructions="What does log establish about testing?",
                criteria={"complete": "Every suite passed", "incomplete": "Results are missing"},
            ),
            "completion": Score(
                instructions="How complete is testing according to log?",
                criteria=["Not started", "Partly run", "All suites run"],
            ),
        },
    )
    print(response.nouls["passed"].noul)
    print(response.choices["coverage"].choice)
    print(response.scores["completion"].score)
    print(response.raw_http_response.json()["task_id"])
    print(client.models.list())
```

Defaults: base URL `http://127.0.0.1:8018`, model `grok-bot`, timeout 240 seconds and zero automatic retries. Pass `base_url` and `api_key` for another service. `GROK_JUDGE_HTTP_API_KEY` supplies the bridge key when `api_key` is omitted.

```python
from grok_judge_bridge import AsyncJudgeClient
from typesafe_sdk import Noul

async def check():
    async with AsyncJudgeClient(project_id="checkout-service") as client:
        return await client.system_one(
            state="Unit tests passed.",
            questions={"passed": Noul(instructions="Did unit tests pass?")},
        )
```

## Existing TypeSafe applications

Keep the question and answer code; configure the official client to reach the bridge:

```python
from typesafe_sdk import TypeSafeClient, RetryPolicy, Noul

with TypeSafeClient(
    base_url="http://127.0.0.1:8018",
    api_key="local",
    model="jev-latest",
    timeout=240,
    retry=RetryPolicy(max_retries=0, timeout=None),
) as client:
    response = client.system_one(
        state="The build passed.",
        questions={"passed": Noul(instructions="Did the build pass?")},
    )
```

`AsyncTypeSafeClient` accepts the same configuration. For a protected service, use its bridge key instead of `local`. Raw backend and project metadata remains available through `response.raw_http_response.json()`.

For direct Bot calls without an HTTP service, use `await evaluate(request)` or the exported `GrokBotProvider` / `AsyncGrokBotProvider` with System One Adapter. Set `structured_outputs=False`, `llm_answer_mode="probabilities"`, `normalize_probabilities=False` and `n_retry_malformed_structure=0`.

## Authentication and errors

Loopback starts without an API key. To require bearer authentication, set `GROK_JUDGE_HTTP_API_KEY` in the service environment. Listening beyond loopback requires it. This key protects the bridge and is independent of Grok Bot's native login.

```http
Authorization: Bearer <bridge-key>
```

Errors return `error.code`, `error.message`, `request_id` and `batch_status="unprocessed"`.

| HTTP | Meaning |
| --- | --- |
| 400 | Invalid JSON, compression, UTF-8 or project header |
| 401 | Missing or incorrect bearer token |
| 409 | An earlier Bot dispatch remains pending |
| 413 | Body limit exceeded |
| 415 | Unsupported Content-Encoding |
| 422 | Invalid questions or unsupported routing model |
| 502 | Bot transport or judgment validation failed |
| 504 | Total deadline elapsed |

Requests are sent once. For an uncertain dispatch, use `grok-judge pending` and `grok-judge reconcile`; reconciliation reads the existing task and sends nothing new. Do not automatically replay failed requests.

Supported evaluation primitives are text `choice`, `score` and `noul`. Media assets, Box questions, TypeSafe account management and TypeSafe-hosted models are outside this API.

## Upstream interfaces

- [System One Adapter source](https://github.com/typesafe-ai/system-one-adapter-python): schema construction, response conversion and custom provider protocol.
- [TypeSafe Python SDK](https://docs.typesafe.ai/sdk/python): sync/async HTTP clients and typed answer views.
- [TypeSafe HTTP API](https://docs.typesafe.ai/api): state/questions contract and primitive semantics.
