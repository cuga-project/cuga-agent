from cuga.backend.cuga_graph.utils.controller import AgentRunner


def test_agent_runner_default_tool_mode_is_internal():
    runner = AgentRunner(browser_enabled=False)

    assert runner.tool_mode == "internal"
    assert runner.tooling_profile.capabilities.enable_browser_actions is True


def test_agent_runner_external_mode_disables_browser_actions():
    runner = AgentRunner(
        browser_enabled=False,
        tool_mode="external",
        external_tools=[],
    )

    assert runner.tooling_profile.capabilities.enable_browser_actions is False