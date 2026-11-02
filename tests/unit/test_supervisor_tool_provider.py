"""Sub-agent CombinedToolProvider must honor app names, include lists, and agent_id."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from cuga.supervisor_utils.supervisor_config import _create_tool_provider

pytestmark = pytest.mark.unit


def test_create_tool_provider_passes_app_names_agent_id_and_include():
    with patch("cuga.supervisor_utils.supervisor_config.CombinedToolProvider") as provider_cls:
        provider_cls.return_value.initialize = AsyncMock()
        result = asyncio.run(
            _create_tool_provider(
                apps=[{"name": "crm", "include": ["get_customers"]}, {"name": "email"}],
                mcp_servers=[],
                agent_id="crm-agent",
                include_by_app={"crm": ["get_customers"]},
            )
        )

    assert result is provider_cls.return_value
    kwargs = provider_cls.call_args.kwargs
    assert kwargs["app_names"] == ["crm", "email"]
    assert kwargs["agent_id"] == "crm-agent"
    include, version = kwargs["get_include_by_app"]()
    assert include == {"crm": ["get_customers"]}
    assert version == 0
    provider_cls.return_value.initialize.assert_awaited()


def test_create_tool_provider_returns_none_without_apps():
    assert asyncio.run(_create_tool_provider(apps=[], mcp_servers=[])) is None


@pytest.mark.asyncio
async def test_stored_member_with_no_tools_keeps_explicit_empty_scope():
    with patch("cuga.supervisor_utils.supervisor_config.CombinedToolProvider") as provider_cls:
        provider_cls.return_value.initialize = AsyncMock()
        result = await _create_tool_provider(apps=[], mcp_servers=[], agent_id="empty-member")

    assert result is provider_cls.return_value
    assert provider_cls.call_args.kwargs["app_names"] == []
    assert provider_cls.call_args.kwargs["agent_id"] == "empty-member"
    provider_cls.return_value.initialize.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "use_draft,has_draft,namespace",
    [(False, True, "weatherbot"), (True, True, "weatherbot--draft"), (True, False, "weatherbot")],
)
async def test_stored_member_tool_namespace_matches_resolved_config(
    monkeypatch, use_draft, has_draft, namespace
):
    from cuga.supervisor_utils import supervisor_config

    published = {"tools": [{"name": "published-tools"}]}
    draft = {"tools": [{"name": "draft-tools"}]}
    monkeypatch.setattr(
        "cuga.backend.server.config_store.load_config", AsyncMock(return_value=(published, 1))
    )
    monkeypatch.setattr(
        "cuga.backend.server.config_store.load_draft", AsyncMock(return_value=draft if has_draft else None)
    )
    policy = AsyncMock()
    monkeypatch.setattr("cuga.backend.cuga_graph.policy.configurable.create_agent_policy_system", policy)
    builder = AsyncMock(return_value={})
    monkeypatch.setattr(supervisor_config, "build_agents_from_list", builder)

    await supervisor_config.build_agents_from_stored_subagents(
        [{"kind": "internal", "ref": "weatherbot"}], use_draft=use_draft
    )

    member = builder.call_args.args[0][0]
    assert member["name"] == "weatherbot"
    assert member["agent_id"] == namespace
    assert member["apps"] == (["draft-tools"] if use_draft and has_draft else ["published-tools"])
    policy.assert_awaited_once_with(agent_id="weatherbot", draft=use_draft and has_draft)
