"""MCP tools, including existing TypeSafe/Jev compatibility names."""

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .config import BridgeError, config_path, load_settings
from .service import Context, Evaluation, Question, evaluate
from .transport import BotTransport

mcp = FastMCP(
    "typesafe",
    instructions=(
        "Use judge_evaluate (or compatibility alias jev_evaluate) for repeated review triage, "
        "claim/evidence checks, failed CI routing, classification, ranking and rubric scoring. "
        "Supply evidence and explicit criteria, batching independent questions. The actual "
        "backend is a dedicated Grok Bot. Probabilities are uncalibrated LLM estimates. "
        "Keep code navigation, calculations and execution in their usual tools. "
        "Never include credentials. Tool failures mean unprocessed; do not replay through "
        "another entry point. A classification does not authorize an action."
    ),
)
annotations = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=False, openWorldHint=True
)


@mcp.tool(annotations=annotations)
async def judge_evaluate(
    state: Context, questions: dict[str, Question], model: str = "grok-bot"
) -> dict:
    """Classify, score, or check supplied evidence through the dedicated Grok Judge Bot.

    choice: criteria maps option names to descriptions. Include insufficient evidence when
    needed. score: 2-10 ordered descriptive levels, zero-based. noul: yes/no probability,
    optionally describing true/false. Batch up to 256 questions over one state (1 MB JSON).
    Uses Grok Bot's account, not TypeSafe credits. Returned probabilities are uncalibrated
    LLM estimates. No evidence retrieval or action execution is requested. Timeout/error
    means this batch is unprocessed; reconcile pending dispatches instead of resending.
    """
    return await evaluate(Evaluation(state=state, questions=questions, model=model))


@mcp.tool(annotations=annotations)
async def jev_evaluate(
    state: Context, questions: dict[str, Question], model: str = "jev-latest"
) -> dict:
    """Compatibility alias for judge_evaluate. Backend: Grok Bot, not Jev/TypeSafe.

    Same state/questions schema: choice(criteria map), score(2-10 levels), noul(yes/no).
    Proactively batch review triage, evidence checks, classification and ranking. No
    TypeSafe API call occurs. Probabilities are uncalibrated LLM estimates; errors leave
    the batch unprocessed. Do not resend an uncertain dispatch.
    """
    return await judge_evaluate(state, questions, model)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
def judge_status() -> dict:
    """Read local bridge configuration and pending metadata without contacting Grok Bot."""
    try:
        settings = load_settings()
        pending = BotTransport(settings).pending()
        return {
            "configured": True,
            "backend": "grok_bot",
            "model": "grok-bot",
            "bot_id": settings.bot_id,
            "config_path": str(config_path()),
            "timeout_seconds": settings.timeout_seconds,
            "pending": pending,
            "authentication_checked": False,
            "uses_typesafe_api": False,
        }
    except BridgeError:
        return {
            "configured": False,
            "backend": "grok_bot",
            "authentication_checked": False,
            "config_path": str(config_path()),
            "uses_typesafe_api": False,
        }


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
def jev_status() -> dict:
    """Compatibility alias for judge_status; checks local Grok Bot bridge configuration."""
    return judge_status()
