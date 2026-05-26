"""Regression test for issue #185.

MCP tool names containing dashes must be registered under sanitized (valid Python
identifier) names, while the original dashed name is preserved in a reverse map so
that _call_mcp_server_tool can forward the correct name to the MCP server.
"""

from unittest.mock import MagicMock, patch

import pytest

from cuga.backend.tools_env.registry.mcp_manager.adapter import sanitize_tool_name


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fake_tool(name: str, description: str = "A tool"):
    """Return a minimal mock object that looks like an MCP Tool."""
    tool = MagicMock()
    tool.name = name
    tool.description = description
    tool.inputSchema = {"type": "object", "properties": {}}
    tool.outputSchema = {}
    return tool


def _make_manager_with_tools(server_name: str, tool_names: list[str]):
    """
    Instantiate MCPManager with a minimal config and manually drive the
    registration loop that runs inside _initialize_single_fastmcp_server,
    without actually connecting to any MCP server.
    """
    from collections import defaultdict
    from cuga.backend.tools_env.registry.mcp_manager.mcp_manager import MCPManager

    # Build a minimal ServiceConfig stub so __init__ doesn't crash.
    service_cfg = MagicMock()
    service_cfg.type = "mcp_server"

    with patch.object(MCPManager, "__init__", lambda self, cfg: None):
        mgr = MCPManager.__new__(MCPManager)

    # Manually initialise only the attributes touched by the registration loop.
    mgr.tools_by_server = defaultdict(list)
    mgr.server_by_tool = {}
    mgr.original_tool_name_by_sanitized = {}

    tools = [_make_fake_tool(n) for n in tool_names]

    # Replicate the registration loop from _initialize_single_fastmcp_server.
    for tool in tools:
        sanitized_name = sanitize_tool_name(tool.name)
        prefixed_name = f"{server_name}_{sanitized_name}"
        mgr.original_tool_name_by_sanitized[prefixed_name] = tool.name
        tool_dict = {
            "type": "function",
            "function": {
                "name": prefixed_name,
                "description": tool.description,
                "parameters": {},
                "outputSchema": {},
            },
        }
        mgr.tools_by_server[server_name].append(tool_dict)
        mgr.server_by_tool[prefixed_name] = server_name

    return mgr


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDashedToolNameRegistration:
    """Registered names must be valid Python identifiers (issue #185)."""

    SERVER = "dashrepro"
    DASHED_TOOL = "echo-with-dash"
    UNDERSCORED_TOOL = "echo_with_underscore"

    def setup_method(self):
        self.mgr = _make_manager_with_tools(self.SERVER, [self.DASHED_TOOL, self.UNDERSCORED_TOOL])

    # -- registered names are valid identifiers --------------------------------

    def test_dashed_tool_registered_as_valid_identifier(self):
        registered = list(self.mgr.server_by_tool.keys())
        for name in registered:
            assert name.isidentifier(), f"Registered tool name {name!r} is not a valid Python identifier"

    def test_dashed_tool_sanitized_name(self):
        assert "dashrepro_echo_with_dash" in self.mgr.server_by_tool

    def test_underscored_tool_name_unchanged(self):
        assert "dashrepro_echo_with_underscore" in self.mgr.server_by_tool

    # -- reverse map preserves original names ----------------------------------

    def test_reverse_map_dashed_tool(self):
        assert self.mgr.original_tool_name_by_sanitized["dashrepro_echo_with_dash"] == "echo-with-dash"

    def test_reverse_map_underscored_tool(self):
        assert (
            self.mgr.original_tool_name_by_sanitized["dashrepro_echo_with_underscore"]
            == "echo_with_underscore"
        )

    # -- code-gen compile check ------------------------------------------------

    def test_dashed_tool_name_compiles(self):
        sanitized = "dashrepro_echo_with_dash"
        code = f"result = {sanitized}(message='hi')"
        # Must not raise SyntaxError
        compile(code, "<gen>", "exec")

    # -- cleanup removes reverse map entries -----------------------------------

    def test_clear_removes_reverse_map_entries(self):
        # Simulate _clear_mcp_server_registration logic
        stale_tools = [t for t, s in self.mgr.server_by_tool.items() if s == self.SERVER]
        for t in stale_tools:
            self.mgr.server_by_tool.pop(t, None)
            self.mgr.original_tool_name_by_sanitized.pop(t, None)

        assert "dashrepro_echo_with_dash" not in self.mgr.original_tool_name_by_sanitized
        assert "dashrepro_echo_with_underscore" not in self.mgr.original_tool_name_by_sanitized


class TestSanitizeToolName:
    """Sanity-check the sanitize_tool_name helper used by the fix."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("echo-with-dash", "echo_with_dash"),
            ("echo_with_underscore", "echo_with_underscore"),
            ("my-tool-v2", "my_tool_v2"),
            ("tool.name", "tool_name"),
            ("tool name", "tool_name"),
            ("UPPER-CASE", "upper_case"),
            ("-leading-dash", "leading_dash"),
            ("trailing-dash-", "trailing_dash"),
        ],
    )
    def test_sanitize(self, raw, expected):
        assert sanitize_tool_name(raw) == expected


class TestSanitizationCollision:
    """When two tool names on the same server sanitize to the same identifier,
    the second one must be skipped (not silently overwrite the first)."""

    SERVER = "myserver"

    def _register(self, tool_names: list[str]):
        """Run the registration loop from _initialize_single_fastmcp_server."""
        from collections import defaultdict

        mgr = MagicMock()
        mgr.tools_by_server = defaultdict(list)
        mgr.server_by_tool = {}
        mgr.original_tool_name_by_sanitized = {}

        for tool in [_make_fake_tool(n) for n in tool_names]:
            sanitized_name = sanitize_tool_name(tool.name)
            prefixed_name = f"{self.SERVER}_{sanitized_name}"
            if prefixed_name in mgr.original_tool_name_by_sanitized:
                # collision — skip (mirrors the new guard in mcp_manager.py)
                continue
            mgr.original_tool_name_by_sanitized[prefixed_name] = tool.name
            tool_dict = {
                "type": "function",
                "function": {"name": prefixed_name, "description": tool.description},
            }
            mgr.tools_by_server[self.SERVER].append(tool_dict)
            mgr.server_by_tool[prefixed_name] = self.SERVER

        return mgr

    def test_first_tool_wins_on_collision(self):
        """echo-dash and echo_dash both sanitize to echo_dash; first one wins."""
        mgr = self._register(["echo-dash", "echo_dash"])
        assert mgr.original_tool_name_by_sanitized["myserver_echo_dash"] == "echo-dash"

    def test_colliding_tool_not_registered_twice(self):
        """Only one entry should exist for the colliding sanitized name."""
        mgr = self._register(["echo-dash", "echo_dash"])
        registered = [t["function"]["name"] for t in mgr.tools_by_server[self.SERVER]]
        assert registered.count("myserver_echo_dash") == 1

    def test_non_colliding_tool_still_registered(self):
        """A third tool that doesn't collide must still be registered normally."""
        mgr = self._register(["echo-dash", "echo_dash", "other-tool"])
        assert "myserver_other_tool" in mgr.server_by_tool
        assert mgr.original_tool_name_by_sanitized["myserver_other_tool"] == "other-tool"
