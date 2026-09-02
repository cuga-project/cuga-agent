#!/usr/bin/env python
"""
Serve a CUGA FLO process headlessly, so a remote agent can start instances over MCP.

No UI and no supervisor: this replaces the *caller*, not the harness. The bridge already
registers its tools on a FastMCP server and exposes them over HTTP — all that was missing
was starting that listener before the first call, and staying alive afterwards.

    uv run python scripts/serve_flow.py excel_flows_kogito

Then a remote agent calls the MCP endpoint printed at startup:

    start_process(message="update the Q3 adjustments")   → runs to completion, returns state
    run_process(process_key=..., initial_inputs={})      → fire-and-forget (bridge built-in)

Prefer `start_process`: it goes through FlowAgent.invoke, which registers the completion
future that `complete_process` resolves, so the call returns the finished state. Calling
`run_process` directly executes correctly but completes silently — nothing is awaiting it.

Execution progress is printed as the ActivityTracker collects it.
"""

import argparse
import asyncio
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
APPS = REPO / "docs" / "examples" / "flow_agent_app_inline"


def _echo_tracker_steps() -> None:
    """Print each step as it is collected. The tracker has no subscriber hook, so wrap it."""
    from cuga.backend.activity_tracker.tracker import ActivityTracker

    original = ActivityTracker.collect_step

    def collect_step(self, step):
        data = str(getattr(step, "data", "") or "")
        if len(data) > 300:
            data = data[:300] + " …"
        print(f"  · {getattr(step, 'name', step)}" + (f"\n      {data}" if data else ""), flush=True)
        return original(self, step)

    ActivityTracker.collect_step = collect_step


def _find_config(app: str) -> Path:
    base = Path(app) if Path(app).is_dir() else APPS / app
    configs = sorted((base / "config").glob("*_config.yaml"))
    if not configs:
        sys.exit(f"No *_config.yaml under {base / 'config'}")
    return configs[0]


async def serve(app: str, port: int | None) -> None:
    # Without this the banner and trace vanish when stdout is redirected to a log,
    # which is exactly how a service gets run.
    sys.stdout.reconfigure(line_buffering=True)
    _echo_tracker_steps()

    from cuga.backend.cuga_graph.nodes.cuga_flow.flow_config import FlowConfig

    config_path = _find_config(app)
    print(f"config   {config_path}")

    flow_config = FlowConfig.from_yaml(str(config_path))
    engine_cfg = flow_config.config.get("workflow_engine", {}) or {}
    port = port or int(engine_cfg.get("callback_port", 8090))

    agent = flow_config.to_flow_agent()
    bridge = agent.bridge

    @bridge._mcp.tool(name="start_process")
    async def start_process(message: str) -> dict:
        """Start one process instance and return its final state when it completes."""
        print(f"\n▶ start_process: {message}", flush=True)
        state = await agent.invoke(message)
        print("■ complete\n", flush=True)
        return {"process_variables": getattr(state, "process_variables", {})}

    # Idempotent, and normally called from inside run_process — which is too late for an
    # external caller, since they need the listener up in order to make that call at all.
    await bridge._ensure_http_server(port)

    print(f"engine   {engine_cfg.get('type', 'langgraph')} ({engine_cfg.get('url', 'in-process')})")
    print(f"process  {flow_config.flow_config.get('id')} → {engine_cfg.get('process_id', '(bpmn id)')}")
    print(f"serving  http://0.0.0.0:{port}/mcp   tools: start_process, run_process")
    print("Ctrl-C to stop.\n")

    await asyncio.Event().wait()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("app", help="app dir name under flow_agent_app_inline, or a path")
    ap.add_argument("--port", type=int, default=None, help="override callback_port from the YAML")
    args = ap.parse_args()
    try:
        asyncio.run(serve(args.app, args.port))
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
