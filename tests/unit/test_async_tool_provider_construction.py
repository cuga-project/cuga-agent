from __future__ import annotations

import inspect
from typing import Any

import pytest
from langchain_core.tools import StructuredTool

from cuga.backend.cuga_graph.nodes.cuga_lite.providers import combined as combined_provider
from cuga.backend.cuga_graph.nodes.cuga_lite.providers import registry as registry_provider
from cuga.backend.cuga_graph.nodes.cuga_lite.providers.toolguard import ToolGuardingToolProvider
from tests.unit.test_toolguard_provider import DummyProvider


def _tool_def() -> dict[str, Any]:
    return {
        "description": "Fetch a CRM account.",
        "operation_id": "fetchAccount",
        "parameters": {
            "properties": {
                "account_id": {
                    "type": "string",
                    "description": "Account ID",
                }
            },
            "required": ["account_id"],
        },
    }


@pytest.mark.asyncio
async def test_registry_tool_async_invoke_returns_data_not_coroutine(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_call_api(
        app_name: str,
        api_name: str,
        args: dict[str, Any],
        operation_id: str | None = None,
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        return {
            "app_name": app_name,
            "api_name": api_name,
            "args": args,
            "operation_id": operation_id,
            "agent_id": agent_id,
        }

    monkeypatch.setattr(registry_provider, "call_api", fake_call_api)

    tool = registry_provider.create_tool_from_api_dict(
        "crm_get_account",
        _tool_def(),
        "crm",
        agent_id="agent_1",
    )

    result = await tool.ainvoke({"account_id": "acct_1"})

    assert not inspect.isawaitable(result)
    assert result == {
        "app_name": "crm",
        "api_name": "crm_get_account",
        "args": {"account_id": "acct_1"},
        "operation_id": "fetchAccount",
        "agent_id": "agent_1",
    }


@pytest.mark.asyncio
async def test_combined_tracker_tool_async_invoke_returns_data_not_coroutine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_invoke_tool(app_name: str, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        return {
            "app_name": app_name,
            "tool_name": tool_name,
            "args": args,
        }

    monkeypatch.setattr(combined_provider.tracker, "invoke_tool", fake_invoke_tool)

    tool = combined_provider.create_tool_from_tracker(
        "crm_get_account",
        _tool_def(),
        "crm",
    )

    result = await tool.ainvoke({"account_id": "acct_1"})

    assert not inspect.isawaitable(result)
    assert result == {
        "app_name": "crm",
        "tool_name": "crm_get_account",
        "args": {"account_id": "acct_1"},
    }


@pytest.mark.asyncio
async def test_toolguard_wrapper_unwraps_nested_awaitable_results() -> None:
    async def crm_get_account(account_id: str) -> Any:
        """Fetch a CRM account."""

        async def payload() -> dict[str, Any]:
            return {"account_id": account_id}

        return payload()

    # Simulates legacy/misconstructed tools where an async function was
    # registered as the sync func, causing ainvoke() to yield a coroutine result.
    raw_tool = StructuredTool.from_function(
        func=crm_get_account,
        name="crm_get_account",
        description="Fetch a CRM account.",
    )
    provider = ToolGuardingToolProvider(DummyProvider([raw_tool]), policy_storage=None)

    guarded_tool = (await provider.get_tools("runtime_tools"))[0]
    result = await guarded_tool.ainvoke({"account_id": "acct_1"})

    assert not inspect.isawaitable(result)
    assert result == {"account_id": "acct_1"}
