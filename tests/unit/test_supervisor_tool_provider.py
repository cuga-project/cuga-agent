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
