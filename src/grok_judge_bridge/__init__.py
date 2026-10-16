"""Structured judgments through a dedicated Grok Bot."""

__version__ = "0.1.0"

from .provider import AsyncGrokBotProvider, GrokBotProvider
from .service import evaluate

__all__ = ["AsyncGrokBotProvider", "GrokBotProvider", "evaluate", "__version__"]
