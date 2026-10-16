"""Host-owned transport configuration. No authentication material is managed here."""

from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class BridgeError(Exception):
    """A safe error that never contains upstream messages or request contents."""

    def __init__(self, message: str, *, code: str = "judgment_failed", status_code: int = 502):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    bot_id: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_-]+$")
    mcp_command: list[str] = Field(min_length=1)
    timeout_seconds: float = Field(default=180, ge=5, le=600)
    poll_seconds: float = Field(default=1, ge=0.1, le=10)
    state_dir: Path = Field(default_factory=lambda: Path.home() / ".grok-judge-bridge/state")


def config_path() -> Path:
    return Path(
        os.environ.get("GROK_JUDGE_CONFIG", str(Path.home() / ".grok-judge-bridge/config.json"))
    ).expanduser()


def load_settings() -> Settings:
    try:
        settings = Settings.model_validate_json(config_path().read_text(encoding="utf-8-sig"))
        if any(not arg or "\0" in arg for arg in settings.mcp_command):
            raise ValueError
        return settings
    except (OSError, ValueError):
        raise BridgeError("Configure the Grok Judge Bot and MCP command first.") from None


def save_settings(settings: Settings) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8")
    return path
