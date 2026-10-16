"""Sync and async HTTP clients built on TypeSafe's MIT-licensed SDK."""

from __future__ import annotations

import os
import re
from typing import Any

from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy, TypeSafeClient


def client_options(base_url, api_key, project_id, options):
    headers = dict(options.pop("headers", {}) or {})
    if project_id is not None:
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", project_id):
            raise ValueError("project_id must be 1-128 ASCII letters, digits, or _.:-")
        headers["X-Project-Id"] = project_id
    return {
        "base_url": base_url,
        "api_key": api_key or os.environ.get("GROK_JUDGE_HTTP_API_KEY") or "local",
        "model": "grok-bot",
        "timeout": 240,
        "retry": RetryPolicy(max_retries=0, timeout=None),
        "headers": headers,
        **options,
    }


class JudgeClient(TypeSafeClient):
    """TypeSafe-compatible HTTP client with Grok defaults and no automatic replay."""

    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:8018",
        api_key: str | None = None,
        project_id: str | None = None,
        **options: Any,
    ):
        super().__init__(**client_options(base_url, api_key, project_id, options))


class AsyncJudgeClient(AsyncTypeSafeClient):
    """Asynchronous JudgeClient with the same typed answers and model resource."""

    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:8018",
        api_key: str | None = None,
        project_id: str | None = None,
        **options: Any,
    ):
        super().__init__(**client_options(base_url, api_key, project_id, options))
