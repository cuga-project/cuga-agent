#!/usr/bin/env python3
"""
Connect to the real ro MCP server, register the workflow configured in
config/ordo_config.yaml, run it until the first external GOAL, and complete
that GOAL with a sample result.

The .ro source path and optional register_workflow input_args come from:

  flow.ro_source_file
  flow.input_args

Usage:
    python docs/examples/flow_agent_app_inline/ordo/run_with_mcp.py
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import yaml

# ── Allow running from the repo root without installing ──────────────────────
_repo_root = Path(__file__).resolve().parents[4]  # cuga-agent-external/
sys.path.insert(0, str(_repo_root / "src"))

# Load .env from the project root (OPENAI_API_KEY, MODEL_NAME, …)
_env_file = _repo_root / ".env"
if _env_file.is_file():
    os.environ.setdefault("ENV_FILE", str(_env_file))


def _resolve_ro() -> str:
    """Resolve ro, preferring the freshly rebuilt cargo binary."""
    for candidate in (
        Path("/Users/offerakrabi/.cargo/bin/ro"),
        Path.home() / ".cargo" / "bin" / "ro",
        Path.home() / ".local" / "bin" / "ro",
    ):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return shutil.which("ro") or "ro"


_MCP_CONFIG = {"mcpServers": {"ro": {"command": _resolve_ro(), "args": ["mcp"]}}}


def _extract(result: Any) -> object:
    """Parse a fastmcp call_tool() result into a plain Python object."""
    if hasattr(result, "content"):
        items = result.content
        text = items[0].text if (items and hasattr(items[0], "text")) else str(result)
    else:
        try:
            item = result[0]
            text = item.text if hasattr(item, "text") else str(result)
        except (TypeError, IndexError):
            text = str(result)

    if isinstance(text, (dict, list)):
        return text
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return text


def _print_step(step: str, data: object) -> None:
    print(f"\n--- {step} ---")
    print(json.dumps(data, indent=2, default=str))


def _load_flow_config() -> tuple[str, str, dict[str, Any]]:
    config_file = Path(__file__).parent / "config" / "ordo_config.yaml"
    config = yaml.safe_load(config_file.read_text()) or {}
    flow = config.get("flow") or {}

    workflow_id = flow["id"]
    source_path = flow.get("ro_source_file")
    if not source_path:
        raise ValueError("flow.ro_source_file is required for this smoke test")

    resolved_source = Path(source_path)
    if not resolved_source.is_absolute():
        resolved_source = config_file.parent / resolved_source

    input_args = flow.get("input_args") or {}
    if not isinstance(input_args, dict):
        raise ValueError("flow.input_args must be a mapping/dict if provided")

    return workflow_id, resolved_source.read_text(), input_args


async def main() -> None:
    from fastmcp import Client

    workflow_id, source, input_args = _load_flow_config()

    register_json: dict[str, Any] = {"source": source, "force": True}
    if input_args:
        register_json["input_args"] = input_args

    async with Client(_MCP_CONFIG) as client:
        _print_step(
            "register_workflow",
            _extract(
                await client.call_tool(
                    "register_workflow",
                    {
                        "workflow_id": workflow_id,
                        "json": register_json,
                    },
                )
            ),
        )

        _print_step("get_workflows", _extract(await client.call_tool("get_workflows", {})))

        run_payload = _extract(
            await client.call_tool(
                "run_workflow",
                {"workflow_id": workflow_id, "dispatch": "mcp"},
            )
        )
        _print_step("run_workflow_initial", run_payload)

        if isinstance(run_payload, dict) and run_payload.get("type") == "goal":
            session_id = run_payload["session_id"]
            goal_id = run_payload["goal_id"]

            _print_step(
                "complete_goal",
                _extract(
                    await client.call_tool(
                        "complete_goal",
                        {
                            "workflow_id": workflow_id,
                            "session_id": session_id,
                            "goal_id": goal_id,
                            "result": "Hello, world!",
                        },
                    )
                ),
            )

            try:
                _print_step(
                    "run_workflow_after_complete",
                    _extract(
                        await client.call_tool(
                            "run_workflow",
                            {
                                "workflow_id": workflow_id,
                                "session_id": session_id,
                                "dispatch": "mcp",
                            },
                        )
                    ),
                )
            except Exception as exc:
                _print_step("run_workflow_after_complete_error", f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    asyncio.run(main())