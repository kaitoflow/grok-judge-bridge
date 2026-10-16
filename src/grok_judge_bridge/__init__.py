"""Structured judgments through a dedicated Grok Bot."""

__version__ = "0.2.0"

from .client import AsyncJudgeClient, JudgeClient
from .provider import AsyncGrokBotProvider, GrokBotProvider
from .service import evaluate

__all__ = [
    "AsyncGrokBotProvider",
    "AsyncJudgeClient",
    "GrokBotProvider",
    "JudgeClient",
    "evaluate",
    "__version__",
]
