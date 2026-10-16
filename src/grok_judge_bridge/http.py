"""HTTP boundary for TypeSafe-shaped evaluations and model discovery."""

from __future__ import annotations

import hmac
import os
import re
import zlib
from collections.abc import Callable
from uuid import uuid4

import zstandard
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from typesafe_sdk import SystemOneResponse

from .config import BridgeError, Settings, load_settings
from .provider import strict_json
from .service import Evaluation, evaluate, validate_request

MAX_BODY = 1_000_000


async def read_json(request: Request) -> dict:
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BODY:
            raise BridgeError("Request exceeds 1 MB.", code="request_too_large", status_code=413)
    encoding = request.headers.get("content-encoding", "identity").lower()
    try:
        if encoding in ("gzip", "x-gzip"):
            decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
            body = decoder.decompress(body, MAX_BODY + 1)
            if len(body) > MAX_BODY or decoder.unconsumed_tail:
                raise BridgeError(
                    "Decoded request exceeds 1 MB.", code="request_too_large", status_code=413
                )
            if not decoder.eof or decoder.unused_data:
                raise ValueError
        elif encoding == "zstd":
            declared_size = zstandard.frame_content_size(body)
            if declared_size > MAX_BODY:
                raise BridgeError(
                    "Decoded request exceeds 1 MB.", code="request_too_large", status_code=413
                )
            decoder = zstandard.ZstdDecompressor(max_window_size=8192)
            body = decoder.decompress(body, max_output_size=MAX_BODY + 1, allow_extra_data=False)
        elif encoding != "identity":
            raise BridgeError(
                "Unsupported Content-Encoding.", code="unsupported_encoding", status_code=415
            )
        if len(body) > MAX_BODY:
            raise BridgeError(
                "Decoded request exceeds 1 MB.", code="request_too_large", status_code=413
            )
        value = strict_json(bytes(body).decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError
        return value
    except BridgeError:
        raise
    except (ValueError, UnicodeError, RecursionError, zlib.error, zstandard.ZstdError):
        raise BridgeError(
            "Invalid UTF-8 JSON or compressed body.", code="invalid_json", status_code=400
        ) from None


def error_response(error: BridgeError, request_id: str) -> JSONResponse:
    return JSONResponse(
        {
            "error": {"code": error.code, "message": str(error)},
            "request_id": request_id,
            "batch_status": "unprocessed",
        },
        status_code=error.status_code,
        headers={"X-Request-Id": request_id},
    )


def openapi_document() -> dict:
    request_schema = Evaluation.model_json_schema()
    definitions = request_schema.pop("$defs", {})
    schemas = {**definitions, "Evaluation": request_schema}
    response_schema = SystemOneResponse.model_json_schema()
    response_definitions = response_schema.pop("$defs", {})
    answer = response_schema["properties"]["answers"]["additionalProperties"]
    answer["oneOf"] = [ref for ref in answer["oneOf"] if not ref["$ref"].endswith("/BoxAnswer")]
    answer["discriminator"]["mapping"].pop("box", None)
    for name in ("BoxAnswer", "BoxCoordinates"):
        response_definitions.pop(name, None)
    response_schema["required"] = ["model", "answers", "usage", "backend", "task_id"]
    response_schema["properties"].update(
        {
            "backend": {"const": "grok_bot"},
            "task_id": {"type": "string"},
            "request_id": {"type": "string"},
            "project_id": {"type": "string"},
            "requested_model": {"type": "string"},
            "elapsed_seconds": {"type": ["number", "null"]},
            "probability_semantics": {"const": "llm_estimate_uncalibrated"},
        }
    )
    schemas.update({**response_definitions, "JudgmentResponse": response_schema})

    def fix_refs(value):
        if isinstance(value, dict):
            return {k: fix_refs(v) for k, v in value.items()}
        if isinstance(value, list):
            return [fix_refs(v) for v in value]
        if isinstance(value, str) and value.startswith("#/$defs/"):
            return value.replace("#/$defs/", "#/components/schemas/")
        return value

    responses = {
        "200": {
            "description": "Typed choice, score, and noul judgments",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "required": ["model", "answers", "usage", "backend", "task_id"],
                        "properties": {
                            "model": {"const": "grok-bot"},
                            "backend": {"const": "grok_bot"},
                            "answers": {"type": "object"},
                            "usage": {"type": "object"},
                            "task_id": {"type": "string"},
                            "probability_semantics": {"const": "llm_estimate_uncalibrated"},
                        },
                    }
                }
            },
        }
    }
    responses.update(
        {
            str(code): {"description": description}
            for code, description in {
                400: "Invalid JSON",
                401: "Authentication required",
                409: "Pending task",
                413: "Request too large",
                415: "Unsupported encoding",
                422: "Invalid request",
                502: "Judgment failed",
                504: "Judgment timed out",
            }.items()
        }
    )
    responses["200"]["content"]["application/json"]["schema"] = {
        "$ref": "#/components/schemas/JudgmentResponse"
    }
    evaluation = {
        "operationId": "systemOne",
        "security": [{"BearerAuth": []}, {}],
        "parameters": [
            {
                "in": "header",
                "name": "X-Project-Id",
                "required": False,
                "schema": {"type": "string", "maxLength": 128},
            }
        ],
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {"schema": {"$ref": "#/components/schemas/Evaluation"}}
            },
        },
        "responses": responses,
    }
    return {
        "openapi": "3.1.0",
        "info": {"title": "Grok Judge Bridge", "version": "0.2.0"},
        "paths": {
            "/v1/systemone": {"post": evaluation},
            "/v1/models": {
                "get": {
                    "operationId": "listModels",
                    "responses": {"200": {"description": "Available routing models"}},
                }
            },
            "/health": {
                "get": {
                    "operationId": "health",
                    "responses": {"200": {"description": "Process health"}},
                }
            },
        },
        "components": {
            "schemas": fix_refs(schemas),
            "securitySchemes": {"BearerAuth": {"type": "http", "scheme": "bearer"}},
        },
    }


def create_app(
    *, settings: Settings | None = None, evaluator: Callable = evaluate, api_key: str | None = None
) -> Starlette:
    key = api_key if api_key is not None else os.environ.get("GROK_JUDGE_HTTP_API_KEY", "")

    def authorized(request: Request) -> bool:
        return not key or hmac.compare_digest(
            request.headers.get("authorization", ""), f"Bearer {key}"
        )

    async def run_evaluation(request: Request):
        request_id = f"req_{uuid4().hex}"
        try:
            if not authorized(request):
                raise BridgeError(
                    "A valid bearer token is required.", code="unauthorized", status_code=401
                )
            project = request.headers.get("x-project-id")
            if project is not None and not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", project):
                raise BridgeError(
                    "Invalid X-Project-Id.", code="invalid_project_id", status_code=400
                )
            batch = validate_request(await read_json(request))
            result = await evaluator(batch, settings=settings)
            result = {**result, "request_id": request_id}
            if project:
                result["project_id"] = project
            return JSONResponse(result, headers={"X-Request-Id": request_id})
        except BridgeError as error:
            return error_response(error, request_id)
        except Exception:
            return error_response(
                BridgeError(
                    "Internal bridge error; batch is unprocessed.",
                    code="internal_error",
                    status_code=500,
                ),
                request_id,
            )

    async def models(request: Request):
        if not authorized(request):
            return error_response(
                BridgeError(
                    "A valid bearer token is required.", code="unauthorized", status_code=401
                ),
                f"req_{uuid4().hex}",
            )
        return JSONResponse(
            {
                "models": [
                    {
                        "name": "grok-bot",
                        "description": "Dedicated Grok Judge Bot; shared across projects",
                        "release_date": "2026-10-16",
                    },
                    {
                        "name": "jev-latest",
                        "description": "Compatibility alias for grok-bot",
                        "release_date": "2026-10-16",
                    },
                ]
            }
        )

    async def health(request: Request):
        return JSONResponse({"status": "ok", "backend": "grok_bot", "shared_bot": True})

    async def schema(request: Request):
        return JSONResponse(openapi_document())

    return Starlette(
        routes=[
            Route("/v1/systemone", run_evaluation, methods=["POST"]),
            Route("/v1/decisions", run_evaluation, methods=["POST"]),
            Route("/v1/models", models),
            Route("/health", health),
            Route("/openapi.json", schema),
        ]
    )


def serve(*, host: str = "127.0.0.1", port: int = 8018):
    import uvicorn

    if host not in ("127.0.0.1", "::1", "localhost") and not os.environ.get(
        "GROK_JUDGE_HTTP_API_KEY"
    ):
        raise BridgeError(
            "Set GROK_JUDGE_HTTP_API_KEY before listening beyond loopback.",
            code="configuration_error",
            status_code=503,
        )
    settings = load_settings()
    uvicorn.run(create_app(settings=settings), host=host, port=port, access_log=False)
