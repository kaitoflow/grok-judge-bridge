"""CLI and stdio MCP entry points."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from .config import BridgeError, Settings, load_settings, save_settings
from .provider import strict_json
from .service import evaluate


def main() -> int:
    parser = argparse.ArgumentParser(description="Structured judgments through Grok Bot")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("mcp")
    sub.add_parser("status")
    sub.add_parser("pending")
    sub.add_parser("reconcile")
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8018)
    configure = sub.add_parser("configure")
    configure.add_argument("--bot-id", required=True)
    configure.add_argument("--mcp-command-json", required=True)
    configure.add_argument("--timeout-seconds", type=float, default=180)
    configure.add_argument("--state-dir", type=Path)
    run = sub.add_parser("evaluate")
    run.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        if args.command == "mcp":
            from .server import mcp

            mcp.run(transport="stdio")
            return 0
        if args.command == "serve":
            from .http import serve

            serve(host=args.host, port=args.port)
            return 0
        if args.command == "configure":
            values = {
                "bot_id": args.bot_id,
                "mcp_command": strict_json(args.mcp_command_json),
                "timeout_seconds": args.timeout_seconds,
            }
            if args.state_dir:
                values["state_dir"] = args.state_dir
            output = {"config_path": str(save_settings(Settings(**values))), "configured": True}
        elif args.command == "status":
            from .server import judge_status

            output = judge_status()
        elif args.command == "evaluate":
            output = asyncio.run(evaluate(strict_json(args.input.read_text(encoding="utf-8-sig"))))
        else:
            from .transport import BotTransport

            transport = BotTransport(load_settings())
            output = (
                transport.pending()
                if args.command == "pending"
                else asyncio.run(transport.reconcile())
            )
        print(json.dumps(output, ensure_ascii=False, allow_nan=False))
        return 0
    except BridgeError as error:
        print(json.dumps({"error": str(error), "batch_status": "unprocessed"}))
    except (ValueError, TypeError, OSError):
        print(
            json.dumps(
                {
                    "error": "Invalid local configuration or input JSON; contents suppressed.",
                    "batch_status": "unprocessed",
                }
            )
        )
    return 1
