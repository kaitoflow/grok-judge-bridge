"""Validated TypeSafe-shaped requests with truthful Grok Bot backend metadata."""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from system_one_adapter import AsyncSystemOneAdapterClient
from typesafe_sdk import TypeSafeError

from .config import BridgeError, Settings
from .provider import AsyncGrokBotProvider

Context = str | dict[str, Any] | list[Any]


class QuestionBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instructions: Context


class ChoiceQuestion(QuestionBase):
    type: Literal["choice"]
    criteria: dict[str, Context | None] = Field(min_length=1, max_length=255)


class ScoreQuestion(QuestionBase):
    type: Literal["score"]
    criteria: list[Context] = Field(min_length=2, max_length=10)


class NoulQuestion(QuestionBase):
    type: Literal["noul"]
    criteria: dict[Literal["true", "false"], Context] | None = None


Question = Annotated[ChoiceQuestion | ScoreQuestion | NoulQuestion, Field(discriminator="type")]


class Evaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: Context
    questions: dict[str, Question] = Field(min_length=1, max_length=256)
    model: str = Field(default="grok-bot", min_length=1, max_length=128)


async def evaluate(
    request: dict | Evaluation,
    *,
    settings: Settings | None = None,
    provider: AsyncGrokBotProvider | None = None,
) -> dict:
    try:
        batch = request if isinstance(request, Evaluation) else Evaluation.model_validate(request)
        encoded = json.dumps(
            batch.model_dump(exclude_none=True), ensure_ascii=False, allow_nan=False
        )
    except (ValueError, TypeError):
        raise BridgeError(
            "Invalid judgment request; check state, questions, and criteria."
        ) from None
    if len(encoded.encode("utf-8")) > 1_000_000:
        raise BridgeError("Request exceeds the 1 MB JSON limit; split this batch.")
    if batch.model != "grok-bot" and not batch.model.startswith("jev-"):
        raise BridgeError("Supported routing models are grok-bot and legacy jev-* aliases.")
    provider = provider or AsyncGrokBotProvider(settings)
    try:
        async with AsyncSystemOneAdapterClient(
            structured_outputs=False,
            llm_answer_mode="probabilities",
            normalize_probabilities=False,
            n_retry_malformed_structure=0,
            model=provider,
        ) as client:
            response = await client.system_one(
                state=batch.state,
                questions={
                    qid: q.model_dump(exclude_none=True) for qid, q in batch.questions.items()
                },
            )
        data = response.model_dump(mode="json", exclude={"debug"})
    except BridgeError:
        raise
    except (TypeSafeError, ValidationError, ValueError):
        raise BridgeError("Grok Bot returned an invalid judgment; batch is unprocessed.") from None
    data["backend"] = "grok_bot"
    data["requested_model"] = batch.model
    data["probability_semantics"] = "llm_estimate_uncalibrated"
    data["task_id"] = provider.transport.last_task_id
    data["elapsed_seconds"] = getattr(provider.transport, "last_elapsed_seconds", None)
    return data
