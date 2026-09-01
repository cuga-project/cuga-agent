"""What a supervisor sub-agent is allowed to see, and why `mcp_servers:` has to count.

HISTORY, BECAUSE THE ANSWER CHANGED UNDER US
--------------------------------------------
`_create_tool_provider` used to build `CombinedToolProvider()` with no arguments — every sub-agent
got the whole registry and the `apps:` key was descriptive only. This branch briefly made scoping an
opt-in flag (`scope_tools=`) so that turning it on could not silently strip tools from supervisors
already in the wild.

#433 (multi-agent registry / supervisor runtime) then made scoping the default upstream:
`app_names=app_names or None`. That settles the question — scoping is the product's behaviour now,
decided in main, not something this branch imposes — so the flag is gone.

WHAT STILL NEEDS GUARDING
-------------------------
Two things main's version does not do, both of which the events roster depends on:

  1. `mcp_servers:` entries must count as named apps. Every agent in the events roster declares ONLY
     `mcp_servers:` — no `apps:` key at all. Without this, app_names comes out empty, `or None`
     hands back the entire registry, and the "specialist" is a specialist in name only.

  2. Hyphen → underscore. Rosters write `cuga-finance`; registry keys are `cuga_finance`. Scoping on
     the hyphenated spelling matches nothing, and a hyphen surviving into a generated identifier
     (`cuga-finance_get_price`) parses as subtraction.

Neither is visible from the outside: both fail by handing over too many tools or too few, with no
error. Hence these tests.
"""

from __future__ import annotations

import pytest

from cuga.supervisor_utils import supervisor_config

pytestmark = pytest.mark.unit


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


@pytest.mark.asyncio
async def test_mcp_servers_count_as_named_apps(spy):
    """THE EVENTS ROSTER'S CASE. Its agents declare mcp_servers and nothing else."""
    await supervisor_config._create_tool_provider(apps=[], mcp_servers=[{"name": "cuga_finance"}])

    assert spy.last_app_names == ["cuga_finance"], (
        "an mcp_servers-only agent was handed the whole registry — the events roster declares "
        "mcp_servers exclusively, so every specialist would see every tool"
    )


@pytest.mark.asyncio
async def test_apps_and_mcp_servers_combine(spy):
    await supervisor_config._create_tool_provider(apps=[{"name": "crm"}], mcp_servers=[{"name": "cuga_web"}])

    assert spy.last_app_names == ["crm", "cuga_web"]


@pytest.mark.asyncio
async def test_hyphens_are_mapped_to_registry_keys(spy):
    """Rosters say `cuga-finance`; the registry key is `cuga_finance`."""
    await supervisor_config._create_tool_provider(
        apps=[{"name": "cuga-finance"}, "cuga-web"], mcp_servers=[{"name": "cuga-geo"}]
    )

    assert spy.last_app_names == ["cuga_finance", "cuga_web", "cuga_geo"]
    assert not any("-" in n for n in spy.last_app_names)


@pytest.mark.asyncio
async def test_naming_nothing_still_gets_everything(spy):
    """Unchanged in every version of this function: no apps and no servers → no provider, so the
    agent falls back to the full registry rather than being scoped to an empty list."""
    assert await supervisor_config._create_tool_provider(apps=[], mcp_servers=[]) is None


@pytest.mark.asyncio
async def test_string_entries_are_accepted_too(spy):
    """`apps: [crm]` is as valid as `apps: [{name: crm}]`."""
    await supervisor_config._create_tool_provider(apps=["crm"], mcp_servers=["cuga-web"])

    assert spy.last_app_names == ["crm", "cuga_web"]
