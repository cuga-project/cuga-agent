"""
MCPOrdo - FastMCP server exposing the remote workflow lifecycle API.

Implements the same contract as external_mcp_engine/server.py but as a
first-class production class for embedding in the CUGA FLO server.

WorkflowStubStore is the in-memory workflow engine served by MCPOrdo.
MCP2MCPMediator registers additional callback proxy tools here so that
a real external engine can call FlowAgent control-point handlers directly
via MCP rather than using the pause-resume round-trip.

Tools exposed:
  get_workflows()
  register_workflow(workflow_json)         → UploadResult
  run_workflow(workflow_id)               → RunResult (final_response | agent_goal)
  resume_workflow(session_id, response)   → RunResult
  stop_workflow(session_id, force)        → StopResult
  get_run_status(session_id?)             → RunStatus
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from fastmcp import Client, FastMCP
from fastmcp.client.transports import FastMCPTransport
from cuga.backend.server.cuga_flo_mcp.mcp_logger import mcp_in, mcp_out
from loguru import logger

from cuga.backend.cuga_graph.nodes.cuga_flow.remote.schemas import (
    AgentGoal,
    RunResult,
    RunStatus,
    SingleRunStatus,
    StopResult,
    UploadResult,
    WorkflowContext,
    WorkflowList,
    WorkflowSummary,
)


# ── WorkflowStubStore — in-memory engine served by MCPOrdo ───────────────────


@dataclass
class WorkflowStubStore:
    """
    In-memory stub workflow engine served by MCPOrdo.

    When a workflow reaches a control point requiring agent reasoning it pauses
    and returns an AgentGoal.  MCP2MCPMediator receives that goal, forwards it
    to FlowAgent via MCPFlowBridge, and calls resume_workflow() with the result.
    """

    workflows: dict[str, WorkflowSummary] = field(default_factory=dict)
    sessions: dict[str, SingleRunStatus] = field(default_factory=dict)

    def get_workflows(self) -> WorkflowList:
        return WorkflowList(workflows=list(self.workflows.values()))

    def register_workflow(self, workflow_json: dict[str, Any] | str) -> UploadResult:
        payload = self._parse(workflow_json)
        wid = str(payload.get("workflow_id") or payload.get("id") or f"wf_{uuid4().hex[:8]}")
        name = str(payload.get("name") or wid)
        description = str(payload.get("description") or "")
        self.workflows[wid] = WorkflowSummary(workflow_id=wid, name=name, description=description)
        logger.debug(f"MCPOrdo: registered workflow '{wid}'")
        return UploadResult(workflow_id=wid, name=name, description=description)

    def run_workflow(self, workflow_id: str) -> RunResult:
        if workflow_id not in self.workflows:
            raise ValueError(f"MCPOrdo: unknown workflow_id '{workflow_id}'")

        session_id = f"sess_{uuid4().hex}"

        if workflow_id == "receive_order_stub":
            response = f"Workflow '{workflow_id}' completed successfully."
            self.sessions[session_id] = SingleRunStatus(
                session_id=session_id, workflow_id=workflow_id,
                status="completed", final_response=response,
            )
            return RunResult(final_response=response)

        agent_name_map = {
            "loan_approval_stub": "credit_checker",
            "trip_planner_stub":  "travel_planner",
        }
        agent_name = agent_name_map.get(workflow_id, "task_agent")

        goal = AgentGoal(
            agent_name=agent_name,
            workflow_session_id=session_id,
            context=WorkflowContext(
                vars={"workflow_id": workflow_id},
                memory={},
                state={"status": "paused", "waiting_for": agent_name},
            ),
        )
        self.sessions[session_id] = SingleRunStatus(
            session_id=session_id, workflow_id=workflow_id,
            status="paused", agent_goal=goal,
        )
        logger.debug(f"MCPOrdo: workflow '{workflow_id}' paused — agent_goal: {agent_name}")
        return RunResult(agent_goal=goal)

    def resume_workflow(
        self, session_id: str, agent_response: str | dict[str, Any]
    ) -> RunResult:
        session = self._get_session(session_id)
        if session.status != "paused":
            raise ValueError(
                f"MCPOrdo: session '{session_id}' is not paused (status={session.status})"
            )
        response_text = (
            agent_response if isinstance(agent_response, str)
            else json.dumps(agent_response, sort_keys=True)
        )
        final_response = (
            f"Workflow '{session.workflow_id}' completed. Agent result: {response_text}"
        )
        self.sessions[session_id] = session.model_copy(
            update={"status": "completed", "final_response": final_response, "agent_goal": None}
        )
        logger.debug(f"MCPOrdo: session '{session_id}' completed")
        return RunResult(final_response=final_response)

    def stop_workflow(self, session_id: str, force: bool = False) -> StopResult:
        session = self._get_session(session_id)
        if session.status in {"completed", "stopped"}:
            return StopResult(
                session_id=session_id, stopped=session.status == "stopped",
                forced=force, status=session.status,
                message=f"Session already {session.status}.",
            )
        self.sessions[session_id] = session.model_copy(
            update={"status": "stopped", "agent_goal": None}
        )
        return StopResult(
            session_id=session_id, stopped=True, forced=force, status="stopped",
            message=f"Session stopped ({'forced' if force else 'graceful'}).",
        )

    def get_run_status(self, session_id: str | None = None) -> RunStatus:
        if session_id is None:
            return RunStatus(runs=list(self.sessions.values()))
        return RunStatus(runs=[self._get_session(session_id)])

    def _get_session(self, session_id: str) -> SingleRunStatus:
        try:
            return self.sessions[session_id]
        except KeyError as exc:
            raise ValueError(f"MCPOrdo: unknown session_id '{session_id}'") from exc

    @staticmethod
    def _parse(workflow_json: dict[str, Any] | str) -> dict[str, Any]:
        parsed = json.loads(workflow_json) if isinstance(workflow_json, str) else workflow_json
        if not isinstance(parsed, dict):
            raise ValueError("workflow_json must be a JSON object or dict")
        return parsed


# ── _NoOpWorkflowStore — stub store for external MCP servers ─────────────────


class _NoOpWorkflowStore:
    """
    No-op store used by MCPOrdoExternal.

    The real workflow engine manages its own registry; we do not need to
    register workflows dynamically.  Any call to register_workflow() is
    silently ignored so MCP2MCPMediator._register_workflow_on_ordo() can
    continue to use the same code path without modification.
    """

    def register_workflow(self, workflow_json: "dict[str, Any] | str") -> None:  # noqa: F821
        logger.debug(
            "MCPOrdoExternal: skipping register_workflow "
            "(real engine manages its own registry)"
        )


# ── MCPOrdoExternal — client wrapper for a real external MCP server ───────────


class MCPOrdoExternal:
    """
    Drop-in replacement for MCPOrdo that connects to a *real* external
    workflow engine running as an MCP server.

    Instead of spinning up an in-process FastMCP server backed by
    WorkflowStubStore, this class wraps a fastmcp ``Client`` that speaks
    to the external process via stdio (or any other transport fastmcp
    supports).

    Typical usage::

        # Python (standalone script)
        from cuga.backend.server.cuga_flo_mcp.ordo import MCPOrdoExternal

        ordo = MCPOrdoExternal(command="ro", args=["mcp"])
        flow_agent = flow_config.to_ordo_flow_agent(process_key="...", ordo=ordo)

        # YAML (supervisor_ordo.yaml)
        agents:
          - name: ordo_flow_agent
            type: flow_agent_ordo
            flow_config: "ordo_config.yaml"
            process_key: "loan_approval_stub"
            mcp_server:
              command: "ro"
              args: ["mcp"]

    The ``store`` property exposes a :class:`_NoOpWorkflowStore` so that
    ``MCP2MCPMediator._register_workflow_on_ordo`` can call
    ``store.register_workflow()`` without modification — the call is
    simply ignored because the real engine already knows its workflows.
    """

    # Common user-local binary directories that are often missing from the
    # minimal PATH a uvicorn/gunicorn subprocess inherits.
    _EXTRA_SEARCH_DIRS: "list[str]" = [
        "~/.cargo/bin",
        "~/.local/bin",
        "~/.npm/bin",
        "/opt/homebrew/bin",
        "/opt/homebrew/sbin",
        "/usr/local/bin",
    ]

    def __init__(
        self,
        command: str,
        args: "list[str] | None" = None,
        env: "dict[str, str] | None" = None,
    ) -> None:
        self._command = self._resolve_command(command)
        self._args = args or []
        self._env = env
        self._store = _NoOpWorkflowStore()
        logger.info(
            f"MCPOrdoExternal created: command={self._command!r} args={self._args}"
            + (f" (resolved from {command!r})" if self._command != command else "")
        )

    @classmethod
    def _resolve_command(cls, command: str) -> str:
        """
        Resolve a command name to its absolute path.

        The demo/registry server is a subprocess that inherits a minimal PATH
        (typically ``/usr/bin:/bin``).  Tools installed via pip/cargo/npm into
        ``~/.local/bin`` or ``/opt/homebrew/bin`` are often missing.

        Resolution order:
          1. Absolute path already → return as-is.
          2. ``shutil.which(command)`` on the current process PATH.
          3. Walk :attr:`_EXTRA_SEARCH_DIRS` (expanded, glob-safe).
          4. Fall back to the bare name and let the subprocess raise a clear error.
        """
        import glob
        import os
        import shutil

        if os.path.isabs(command):
            return command

        # Prefer explicit known install locations first.  This avoids using an
        # older cached/user-local binary (for example ~/.local/bin/ro) when a
        # freshly rebuilt binary exists in ~/.cargo/bin.
        for raw_dir in cls._EXTRA_SEARCH_DIRS:
            for expanded in glob.glob(os.path.expanduser(raw_dir)):
                candidate = os.path.join(expanded, command)
                if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                    logger.debug(
                        f"MCPOrdoExternal: resolved {command!r} → {candidate!r} "
                        f"(extra search dir)"
                    )
                    return candidate

        logger.warning(
            f"MCPOrdoExternal: could not resolve {command!r} — "
            "will pass the bare name to fastmcp and hope it is on PATH"
        )
        return command

    def get_client(self) -> "Client":
        """
        Return a fastmcp Client connected to the external MCP server via
        stdio transport.

        fastmcp >= 3.2 expects the Claude-Desktop / MCP config format when a
        dict is passed to ``Client()``.  The dict must use the top-level
        ``mcpServers`` key; a bare ``{"command": ..., "args": [...]}`` dict
        causes a "No MCP servers defined in the config" error::

            {
                "mcpServers": {
                    "ro": {"command": "ro", "args": ["mcp"]}
                }
            }

        An optional ``env`` dict is forwarded to the child process.
        """
        server_entry: dict[str, Any] = {
            "command": self._command,
            "args": self._args,
        }
        if self._env:
            server_entry["env"] = self._env
        config: dict[str, Any] = {
            "mcpServers": {
                self._command: server_entry,
            }
        }
        logger.debug(f"MCPOrdoExternal: creating stdio client {config}")
        return Client(config)

    @property
    def store(self) -> _NoOpWorkflowStore:
        return self._store


# ── MCPOrdo — FastMCP server serving WorkflowStubStore ───────────────────────


class MCPOrdo:
    """
    FastMCP server that serves WorkflowStubStore.

    WorkflowStubStore handles all workflow state; MCPOrdo exposes its lifecycle
    operations as MCP tools.  MCP2MCPMediator additionally registers
    execute_task_proxy, route_gateway_proxy, and evaluate_hook_proxy here so
    FlowAgent control-point handlers are reachable from MCPOrdo's tool namespace.
    """

    def __init__(self, name: str = "cuga-ordo-mcp") -> None:
        self._store = WorkflowStubStore()
        self._mcp = FastMCP(name)
        self._register_tools()
        logger.info(f"MCPOrdo created: {name!r}")

    def _register_tools(self) -> None:
        store = self._store

        @self._mcp.tool(name="get_workflows")
        def get_workflows() -> dict[str, Any]:
            """Return all registered workflows."""
            mcp_in("MCPOrdo", "get_workflows")
            result = store.get_workflows().model_dump(mode="json")
            mcp_out("MCPOrdo", "get_workflows", count=len(result.get("workflows", [])))
            return result

        @self._mcp.tool(name="register_workflow")
        def register_workflow(workflow_json: dict[str, Any] | str) -> dict[str, Any]:
            """Register a workflow definition."""
            payload = json.loads(workflow_json) if isinstance(workflow_json, str) else workflow_json
            mcp_in("MCPOrdo", "register_workflow",
                   workflow_id=payload.get("workflow_id"),
                   name=payload.get("name"))
            result = store.register_workflow(workflow_json).model_dump(mode="json")
            mcp_out("MCPOrdo", "register_workflow",
                    workflow_id=result.get("workflow_id"),
                    registered=result.get("registered"))
            return result

        @self._mcp.tool(name="run_workflow")
        def run_workflow(workflow_id: str) -> dict[str, Any]:
            """Start a workflow; returns a final_response or an agent_goal pause."""
            mcp_in("MCPOrdo", "run_workflow", workflow_id=workflow_id)
            result = store.run_workflow(workflow_id).model_dump(mode="json")
            mcp_out("MCPOrdo", "run_workflow",
                    workflow_id=workflow_id,
                    has_agent_goal=result.get("agent_goal") is not None,
                    agent_name=result.get("agent_goal", {}).get("agent_name") if result.get("agent_goal") else None,
                    final_response=result.get("final_response"))
            return result

        @self._mcp.tool(name="resume_workflow")
        def resume_workflow(
            session_id: str, agent_response: str | dict[str, Any]
        ) -> dict[str, Any]:
            """Resume a paused session with an agent result."""
            mcp_in("MCPOrdo", "resume_workflow",
                   session_id=session_id,
                   response_type=type(agent_response).__name__)
            result = store.resume_workflow(session_id, agent_response).model_dump(mode="json")
            mcp_out("MCPOrdo", "resume_workflow",
                    session_id=session_id,
                    final_response=result.get("final_response"))
            return result

        @self._mcp.tool(name="stop_workflow")
        def stop_workflow(session_id: str, force: bool = False) -> dict[str, Any]:
            """Stop a running or paused session."""
            mcp_in("MCPOrdo", "stop_workflow", session_id=session_id, force=force)
            result = store.stop_workflow(session_id, force).model_dump(mode="json")
            mcp_out("MCPOrdo", "stop_workflow", session_id=session_id, stopped=result.get("stopped"))
            return result

        @self._mcp.tool(name="get_run_status")
        def get_run_status(session_id: str | None = None) -> dict[str, Any]:
            """Return status for one session or all sessions."""
            mcp_in("MCPOrdo", "get_run_status", session_id=session_id)
            result = store.get_run_status(session_id).model_dump(mode="json")
            mcp_out("MCPOrdo", "get_run_status", run_count=len(result.get("runs", [])))
            return result

    def get_client(self) -> Client:
        """Return an in-process MCP client backed by FastMCPTransport."""
        return Client(FastMCPTransport(self._mcp))

    @property
    def mcp(self) -> FastMCP:
        return self._mcp

    @property
    def store(self) -> WorkflowStubStore:
        return self._store
