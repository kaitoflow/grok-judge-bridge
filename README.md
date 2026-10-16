# Grok Judge Bridge

Run structured judgments through a dedicated Grok Bot. Accepts TypeSafe-style `state` and `questions`, and returns `choice`, `score`, and `noul` answers through MCP, Python, or the command line.

Uses the MIT-licensed [System One Adapter](https://github.com/typesafe-ai/system-one-adapter-python) for question schemas and response conversion, and an existing authenticated Grok Bot MCP connection for transport.

## Install

```sh
python -m pip install "git+https://github.com/kaitoflow/grok-judge-bridge.git"
```

Requires Python 3.11+ and an authenticated Grok Bot MCP server exposing `gbot_send` and `gbot_thread`.

## Configure

Create a dedicated judgment Bot in Grok Bot, then save its ID and your existing Grok Bot MCP launch command:

```sh
grok-judge configure --bot-id YOUR_BOT_ID --mcp-command-json '["node", "/absolute/path/to/grok-bot-mcp.mjs"]'
grok-judge status
```

`--mcp-command-json` contains the executable and arguments from your working Grok Bot MCP configuration. Authentication stays with that server. The bridge does not read or export login tokens.

Default configuration: `~/.grok-judge-bridge/config.json`. Set `GROK_JUDGE_CONFIG` to use another file. Bot IDs, launch commands, state, and task receipts stay outside this repository.

## MCP

```toml
[mcp_servers.typesafe]
command = "grok-judge"
args = ["mcp"]
startup_timeout_sec = 30
tool_timeout_sec = 240
```

Tools: `judge_evaluate`, `judge_status`, plus compatibility aliases `jev_evaluate` and `jev_status`. Legacy `model="jev-latest"` is accepted as a routing alias; returned metadata identifies `grok-bot` as the actual backend.

## CLI

```sh
grok-judge evaluate --input examples/judgments.json
```

## Python

```python
import asyncio
from grok_judge_bridge import evaluate

result = asyncio.run(evaluate({
    "state": "The build passed. Integration tests have not run.",
    "questions": {
        "claim": {
            "type": "choice",
            "instructions": "Does the evidence establish that all tests passed?",
            "criteria": {
                "supported": "All tests are confirmed passing",
                "contradicted": "A test is confirmed failing",
                "insufficient": "Some test outcomes are missing"
            }
        }
    }
}))
print(result["answers"]["claim"]["choice"])
```

The package also exports `GrokBotProvider` and `AsyncGrokBotProvider` for use as a custom `model` in System One Adapter clients.

## Execution

Each batch has a unique task ID and JSON schema. Requests for the same Bot share a cross-process lock; only complete assistant replies with matching task markers are accepted. Requests are sent once. An uncertain send or reply timeout leaves a pending receipt, which must be reconciled before the Bot accepts another batch.

`grok-judge pending` inspects pending metadata without returning request contents. `grok-judge reconcile` reads the matching reply and clears the pending receipt only when a complete reply is observed. A validation error returns a failed batch rather than a fabricated judgment.

The default total budget is 180 seconds, including lock waiting, server startup, sending, and reading. Configure `timeout_seconds` between 5 and 600. Token usage is `null` when Grok Bot does not report it. Probability values are LLM estimates; response metadata records `probability_semantics="llm_estimate_uncalibrated"`.

## Development

```sh
python -m pip install -e ".[dev]"
pytest
ruff check .
```

## License

MIT. See [LICENSE](LICENSE).
