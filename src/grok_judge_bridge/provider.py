"""Custom provider for the upstream System One Adapter protocol."""

from __future__ import annotations

import asyncio
import json
import math

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as SchemaError
from system_one_adapter.providers.base import Message, ProviderResult
from typesafe_sdk import TypeSafeError

from .config import BridgeError, Settings, load_settings
from .transport import BotTransport


def strict_json(text: str):
    def pairs(items):
        obj = {}
        for key, value in items:
            if key in obj:
                raise ValueError("Duplicate key")
            obj[key] = value
        return obj

    def reject_constant(value):
        raise ValueError("Non-finite JSON constant")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=reject_constant)


def check_probabilities(data: dict) -> None:
    """The upstream generated schema checks shape but omits probability bounds."""
    for value in data["answers"].values():
        probabilities = list(value.values()) if isinstance(value, dict) else [value]
        if not all(
            type(p) in (int, float) and math.isfinite(p) and 0 <= p <= 1 for p in probabilities
        ):
            raise ValueError("Invalid probability")
        if isinstance(value, dict) and not math.isclose(sum(probabilities), 1, abs_tol=1e-6):
            raise ValueError("Probability distribution must sum to one")


class AsyncGrokBotProvider:
    model_name = "grok-bot"

    def __init__(self, settings: Settings | None = None, *, transport: BotTransport | None = None):
        self.transport = transport or BotTransport(settings or load_settings())

    async def request(
        self, messages: list[Message], *, schema: dict, structured: bool
    ) -> ProviderResult:
        # Grok Bot has prompted JSON, not a native constrained-output transport.
        if structured:
            raise BridgeError("Use structured_outputs=False for Grok Bot.")
        prompt = json.dumps(
            {
                "messages": [{"role": m.role, "content": m.content} for m in messages],
                "output_schema": schema,
            },
            ensure_ascii=False,
            allow_nan=False,
        )
        payload = await self.transport.request(prompt)
        try:
            data = strict_json(payload)
            Draft202012Validator(schema).validate(data)
            check_probabilities(data)
        except (ValueError, SchemaError):
            raise BridgeError(
                "Grok Bot reply failed JSON schema validation; batch is unprocessed."
            ) from None
        return ProviderResult(
            text=json.dumps(data, ensure_ascii=False), input_tokens=None, output_tokens=None
        )

    def translate_error(self, error: Exception) -> TypeSafeError:
        return TypeSafeError("Grok Bot judgment failed; batch is unprocessed.")


class GrokBotProvider:
    model_name = "grok-bot"

    def __init__(self, settings: Settings | None = None, *, transport: BotTransport | None = None):
        self.async_provider = AsyncGrokBotProvider(settings, transport=transport)

    def request(self, messages: list[Message], *, schema: dict, structured: bool) -> ProviderResult:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(
                self.async_provider.request(messages, schema=schema, structured=structured)
            )
        raise BridgeError("Use AsyncGrokBotProvider inside an active event loop.")

    def translate_error(self, error: Exception) -> TypeSafeError:
        return self.async_provider.translate_error(error)
