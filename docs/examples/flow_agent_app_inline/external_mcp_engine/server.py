"""In-memory stub workflow MCP server for CUGA FLO remote-engine demos."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from fastmcp import FastMCP
from loguru import logger

from cuga.backend.cuga_graph.nodes.cuga_flow.remote import (
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


@dataclass
class WorkflowStubStore:
    """Small in-memory store backing the stub lifecycle tools."""

    workflows: dict[str, WorkflowSummary] = field(default_factory=dict)
    sessions: dict[str, SingleRunStatus] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.workflows:
            return
        self.workflows.update(
            {
                "loan_approval_stub": WorkflowSummary(
                    workflow_id="loan_approval_stub",
                    name="Loan Approval Stub",
                    description="Pauses for a credit checker agent goal.",
                ),
                "receive_order_stub": WorkflowSummary(
                    workflow_id="receive_order_stub",
                    name="Receive Order Stub",
                    description="Completes immediately with a deterministic response.",
                ),
                "trip_planner_stub": WorkflowSummary(
                    workflow_id="trip_planner_stub",
                    name="Trip Planner Stub",
                    description="Pauses for a travel planner agent goal.",
                ),
            }
        )

    def get_workflows(self) -> WorkflowList:
        return WorkflowList(workflows=list(self.workflows.values()))

    def register_workflow(self, workflow_json: dict[str, Any] | str) -> UploadResult:
        payload = self._parse_workflow_json(workflow_json)
        workflow_id = str(payload.get("workflow_id") or payload.get("id") or f"workflow_{uuid4().hex[:8]}")
        name = str(payload.get("name") or workflow_id)
        description = str(payload.get("description") or "Registered stub workflow")

        self.workflows[workflow_id] = WorkflowSummary(
            workflow_id=workflow_id,
            name=name,
            description=description,
        )
        return UploadResult(workflow_id=workflow_id, name=name, description=description, registered=True)

    def run_workflow(self, workflow_id: str) -> RunResult:
        if workflow_id not in self.workflows:
            raise ValueError(f"Unknown workflow_id: {workflow_id}")

        session_id = f"session_{uuid4().hex}"
        if workflow_id == "receive_order_stub":
            final_response = "Receive order stub completed successfully."
            self.sessions[session_id] = SingleRunStatus(
                session_id=session_id,
                workflow_id=workflow_id,
                status="completed",
                final_response=final_response,
            )
            return RunResult(final_response=final_response)

        agent_name = "credit_checker" if workflow_id == "loan_approval_stub" else "travel_planner"
        goal = AgentGoal(
            agent_name=agent_name,
            workflow_session_id=session_id,
            context=WorkflowContext(
                vars={"workflow_id": workflow_id},
                memory={},
                state={"status": "paused", "step": "agent_goal"},
            ),
        )
        self.sessions[session_id] = SingleRunStatus(
            session_id=session_id,
            workflow_id=workflow_id,
            status="paused",
            agent_goal=goal,
        )
        return RunResult(agent_goal=goal)

    def resume_workflow(self, session_id: str, agent_response: str | dict[str, Any]) -> RunResult:
        session = self._get_session(session_id)
        if session.status != "paused":
            raise ValueError(f"Session {session_id} is not paused; current status is {session.status}")

        response_text = (
            agent_response if isinstance(agent_response, str) else json.dumps(agent_response, sort_keys=True)
        )
        final_response = f"Workflow {session.workflow_id} completed after agent response: {response_text}"
        updated = session.model_copy(
            update={"status": "completed", "final_response": final_response, "agent_goal": None}
        )
        self.sessions[session_id] = updated
        return RunResult(final_response=final_response)

    def stop_workflow(self, session_id: str, force: bool = False) -> StopResult:
        session = self._get_session(session_id)
        if session.status in {"completed", "stopped"}:
            return StopResult(
                session_id=session_id,
                stopped=session.status == "stopped",
                forced=force,
                status=session.status,
                message=f"Session is already {session.status}.",
            )

        self.sessions[session_id] = session.model_copy(update={"status": "stopped", "agent_goal": None})
        mode = "forced" if force else "graceful"
        return StopResult(
            session_id=session_id,
            stopped=True,
            forced=force,
            status="stopped",
            message=f"Session stopped with {mode} stop.",
        )

    def get_run_status(self, session_id: str | None = None) -> RunStatus:
        if session_id is None:
            return RunStatus(runs=list(self.sessions.values()))
        return RunStatus(runs=[self._get_session(session_id)])

    def _get_session(self, session_id: str) -> SingleRunStatus:
        try:
            return self.sessions[session_id]
        except KeyError as exc:
            raise ValueError(f"Unknown session_id: {session_id}") from exc

    @staticmethod
    def _parse_workflow_json(workflow_json: dict[str, Any] | str) -> dict[str, Any]:
        if isinstance(workflow_json, str):
            parsed = json.loads(workflow_json)
        else:
            parsed = workflow_json
        if not isinstance(parsed, dict):
            raise ValueError("workflow_json must be a JSON object or dict")
        return parsed


def create_mcp_server(store: WorkflowStubStore | None = None) -> FastMCP:
    """Create a FastMCP server exposing the stub workflow lifecycle tools."""

    store = store or WorkflowStubStore()
    mcp = FastMCP("workflow-mcp-stub")

    @mcp.tool(name="get_workflows")
    def get_workflows() -> dict[str, Any]:
        """Return all registered stub workflows."""

        return store.get_workflows().model_dump(mode="json")

    @mcp.tool(name="register_workflow")
    def register_workflow(workflow_json: dict[str, Any] | str) -> dict[str, Any]:
        """Register a workflow definition with the in-memory stub server."""

        return store.register_workflow(workflow_json).model_dump(mode="json")

    @mcp.tool(name="run_workflow")
    def run_workflow(workflow_id: str) -> dict[str, Any]:
        """Create a run session and return a deterministic result or pause."""

        return store.run_workflow(workflow_id).model_dump(mode="json")

    @mcp.tool(name="resume_workflow")
    def resume_workflow(session_id: str, agent_response: str | dict[str, Any]) -> dict[str, Any]:
        """Resume a paused stub session using a supplied agent response."""

        return store.resume_workflow(session_id, agent_response).model_dump(mode="json")

    @mcp.tool(name="stop_workflow")
    def stop_workflow(session_id: str, force: bool = False) -> dict[str, Any]:
        """Stop a running or paused stub session."""

        return store.stop_workflow(session_id, force).model_dump(mode="json")

    @mcp.tool(name="get_run_status")
    def get_run_status(session_id: str | None = None) -> dict[str, Any]:
        """Return one session status or all known session statuses."""

        return store.get_run_status(session_id).model_dump(mode="json")

    return mcp


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the CUGA FLO workflow MCP stub server.")
    parser.add_argument(
        "--transport", default="streamable-http", choices=["streamable-http", "http", "sse", "stdio"]
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    store = WorkflowStubStore()
    mcp = create_mcp_server(store)
    tool_names = [
        "get_workflows",
        "register_workflow",
        "run_workflow",
        "resume_workflow",
        "stop_workflow",
        "get_run_status",
    ]
    logger.info(f"Starting workflow MCP stub: transport={args.transport}")
    if args.transport != "stdio":
        logger.info(f"Listening on {args.host}:{args.port}")
    logger.info(f"Available workflows: {list(store.workflows)}")
    logger.info(f"Tools: {tool_names}")

    if args.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(transport=args.transport, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
