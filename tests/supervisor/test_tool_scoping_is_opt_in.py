"""Tool scoping is OPT-IN, because turning it on by default was a silent breaking change.

WHAT CHANGED, AND WHY THIS TEST EXISTS
--------------------------------------
`_create_tool_provider` used to build `CombinedToolProvider()` with no arguments — every sub-agent
received the whole registry, and the `apps:`/`mcp_servers:` keys in a supervisor YAML were
descriptive only. The old code said so out loud: *"we'll let it load all and filter at tool
retrieval time"*.

Scoping turned those keys into a filter. That is the right behaviour for the events roster, where a
sub-agent is a specialist, but as a DEFAULT it silently removes tools from any supervisor already in
the wild that names some apps and then calls a tool it did not name — and it does so through
`CugaSupervisor.from_yaml`, a documented public API.

So scoping is a keyword the caller asks for, exactly like `auto_load_policies` in the same function.
These tests pin both directions so neither can drift back:

    scope_tools=False (default)  →  app_names=None   →  the whole registry   (upstream behaviour)
    scope_tools=True             →  app_names=[...]  →  only what was named  (events roster)
"""

from __future__ import annotations

import pytest

from cuga.supervisor_utils import supervisor_config


class _SpyProvider:
    """Captures the `app_names` it was constructed with. `None` means 'load everything'."""

    last_app_names: object = "<never constructed>"

    def __init__(self, app_names=None, **_kw):
        type(self).last_app_names = app_names

    async def initialize(self):
        return None


@pytest.fixture
def spy(monkeypatch):
    monkeypatch.setattr(supervisor_config, "CombinedToolProvider", _SpyProvider)
    _SpyProvider.last_app_names = "<never constructed>"
    return _SpyProvider


APPS = [{"name": "cuga-finance"}]
MCP = [{"name": "cuga_web"}]


@pytest.mark.asyncio
async def test_default_hands_over_the_whole_registry(spy):
    """THE REGRESSION GUARD. A caller that does not ask for scoping must get what it always got."""
    await supervisor_config._create_tool_provider(apps=APPS, mcp_servers=MCP)

    assert spy.last_app_names is None, (
        "scoping leaked into the default — every existing supervisor just lost the tools it "
        "did not explicitly name"
    )


@pytest.mark.asyncio
async def test_opting_in_restricts_to_the_named_apps(spy):
    """What the events roster asks for: a specialist holds its own tools, not the registry."""
    await supervisor_config._create_tool_provider(apps=APPS, mcp_servers=MCP, scope_tools=True)

    assert spy.last_app_names == ["cuga_finance", "cuga_web"]


@pytest.mark.asyncio
async def test_hyphens_map_to_underscores_when_scoped(spy):
    """Registry keys are underscore names. A hyphen survives into a generated identifier
    ('cuga-finance_get_price') where it parses as subtraction, so the mapping is load-bearing."""
    await supervisor_config._create_tool_provider(
        apps=[{"name": "cuga-finance"}, "cuga-web"], mcp_servers=[], scope_tools=True
    )

    assert spy.last_app_names == ["cuga_finance", "cuga_web"]
    assert not any("-" in n for n in spy.last_app_names)


@pytest.mark.asyncio
async def test_naming_nothing_gets_everything_either_way(spy):
    """An agent with no apps and no servers has no provider at all — unchanged in both modes."""
    for scope in (False, True):
        assert (
            await supervisor_config._create_tool_provider(apps=[], mcp_servers=[], scope_tools=scope) is None
        )


@pytest.mark.asyncio
async def test_the_keyword_is_keyword_only(spy):
    """Positional would make `_create_tool_provider(apps, mcp, True)` mean something invisible at
    the call site, which is how the original default slipped through review."""
    with pytest.raises(TypeError):
        await supervisor_config._create_tool_provider(APPS, MCP, True)  # type: ignore[misc]
