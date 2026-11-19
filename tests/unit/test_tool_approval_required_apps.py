"""ToolApproval ``required_apps`` matches a tool by the app it belongs to.

A tool's app is known from the provider (for SDK tools, ``metadata["app_name"]``),
so its name does not have to start with ``<app>_``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import HumanMessage

from cuga.backend.cuga_graph.nodes.cuga_agent_core.policy.tool_approval_handler import ToolApprovalHandler
from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.graph_adapter import AgentGraphAdapter
from cuga.backend.cuga_graph.policy.agent import PolicyAgent
from cuga.backend.cuga_graph.policy.configurable import PolicyConfigurable
from cuga.backend.cuga_graph.policy.models import ToolApproval

pytestmark = pytest.mark.unit

_CODE = 'print(await send_email(to="a@example.com", body="hi"))'


def _policy_agent(policy) -> PolicyAgent:
    agent = PolicyAgent.__new__(PolicyAgent)
    agent.storage = SimpleNamespace(list_policies=AsyncMock(return_value=[policy]))
    return agent


def _approve_mail_app() -> ToolApproval:
    return ToolApproval(
        id="approve-mail",
        name="Approve mail",
        description="Approval gate.",
        required_tools=[],
        required_apps=["mail"],
        approval_message="Sending mail needs approval.",
    )


def _adapter(tool_apps: dict) -> AgentGraphAdapter:
    adapter = AgentGraphAdapter(
        tracker=MagicMock(),
        base_callbacks=[],
        task_todos_ref=[],
        tools_context_ref={},
        base_tool_provider=None,
        tools_context={},
    )
    adapter._tool_apps = tool_apps
    return adapter


def test_required_apps_matches_by_tool_app_and_keeps_name_prefix_matching():
    check = PolicyAgent.__new__(PolicyAgent)._check_code_uses_tools

    assert check(_CODE, [], ["mail"], tool_apps={"send_email": "mail"}) == (True, ["send_email"])
    assert check(_CODE, [], ["mail"], tool_apps={"send_email": "chat"}) == (False, [])
    assert check(_CODE, [], ["mail"]) == (False, [])
    assert check("await mail_send(to='a')", [], ["mail"]) == (True, ["mail_send"])
    # Named in the code but not called: no approval.
    assert check('print("send_email")', [], ["mail"], tool_apps={"send_email": "mail"}) == (False, [])


@pytest.mark.asyncio
async def test_approval_pauses_for_a_tool_of_a_required_app_without_the_app_prefix(monkeypatch):
    adapter = _adapter({"send_email": "mail"})
    state = SimpleNamespace(
        chat_messages=[HumanMessage(content="Send it.")], step_count=0, cuga_lite_metadata={}
    )
    policy_system = SimpleNamespace(agent=_policy_agent(_approve_mail_app()), ensure_initialized=AsyncMock())
    monkeypatch.setattr(PolicyConfigurable, "from_config", lambda _config: policy_system)
    monkeypatch.setattr(
        PolicyConfigurable, "create_context_from_state", lambda *_: SimpleNamespace(user_input="")
    )

    command = await ToolApprovalHandler.check_and_create_approval_interrupt(adapter, state, _CODE, _CODE, {})

    assert command is not None
    metadata = command.update[adapter.metadata_key]
    assert metadata["policy_id"] == "approve-mail"
    assert metadata["matched_tools"] == ["send_email"]
