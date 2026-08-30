"""
Checks for the remote-agent binding. No network: `fetch_agent_card` and
`delegate_task_via_a2a_sdk` are patched, since everything here is about *our* wiring
— key names, card caching, role metadata, failure routing.

Run: uv run pytest src/cuga/backend/cuga_graph/nodes/cuga_flow/test_remote_agent.py
"""

import asyncio

import pytest

from cuga.backend.cuga_graph.nodes.cuga_flow import remote_agent as ra


class _FakeA2A:
    """Stands in for the two a2a_protocol functions, recording every call."""

    def __init__(self):
        self.card_calls = 0
        self.sends = []

    async def fetch_agent_card(self, url, auth=None, timeout=30.0):
        self.card_calls += 1
        return {"name": "agent0", "url": url}

    async def delegate_task_via_a2a_sdk(self, card, task, auth=None, timeout=30.0, variables=None):
        self.sends.append({"task": task, "timeout": timeout, "variables": variables})
        return {"result": f"answer to: {task}", "variables": {}, "status": "success"}


@pytest.fixture
def fake(monkeypatch):
    f = _FakeA2A()
    mod = "cuga.backend.cuga_graph.nodes.cuga_supervisor.a2a_protocol"
    import importlib

    proto = importlib.import_module(mod)
    monkeypatch.setattr(proto, "fetch_agent_card", f.fetch_agent_card)
    monkeypatch.setattr(proto, "delegate_task_via_a2a_sdk", f.delegate_task_via_a2a_sdk)
    monkeypatch.setattr(proto, "HAS_A2A_SDK", True)
    return f


def _registry():
    return ra.RemoteAgentRegistry({"agent0": {"url": "http://x", "timeout": 90}})


def test_card_fetched_once_and_cached(fake):
    reg = _registry()

    async def go():
        for _ in range(3):
            await reg.send("agent0", "q", ra.ROLE_CONSULT)

    asyncio.run(go())
    assert fake.card_calls == 1, "card must be fetched once per process, not per call"
    assert len(fake.sends) == 3


def test_task_executor_returns_output_key(fake):
    """`result` must be renamed: TaskAgent._process_output reads `output`/`content`,
    else str(result) — an unmapped dict would be stringified whole into the task output."""
    out = asyncio.run(ra.RemoteTaskExecutor("agent0", _registry()).invoke("do the thing"))
    assert out == {"output": "answer to: do the thing"}
    assert "result" not in out


def test_both_bindings_send_their_role(fake):
    reg = _registry()

    async def go():
        await ra.RemoteTaskExecutor("agent0", reg).invoke("work")
        await ra.make_consultation_tool("agent0", reg, "gateway 'G'").ainvoke({"question": "which?"})

    asyncio.run(go())
    roles = [s["variables"]["role"] for s in fake.sends]
    assert roles == [ra.ROLE_FULFILL, ra.ROLE_CONSULT]


def test_gateway_skips_condition_eval_when_consulting(fake):
    """A consulting gateway has no expression to evaluate, so node 1 is left out of the
    graph entirely and the decide agent asks the user instead."""
    from cuga.backend.cuga_graph.nodes.cuga_flow.decision_agent import DecisionAgent

    tool = ra.make_consultation_tool("agent0", _registry(), "gateway 'G'")
    consulting = DecisionAgent(gateway_id="G", policy="", condition="prose", consultation_tool=tool)
    plain = DecisionAgent(gateway_id="G", policy="", condition="${x} > 1")

    nodes = lambda a: set(a._compiled_graph.get_graph().nodes)  # noqa: E731
    assert "eval_condition" not in nodes(consulting)
    assert "eval_condition" in nodes(plain)


def test_plain_gateway_still_evaluates_its_condition(fake):
    """The no-consultation path is unchanged from before remote agents existed."""
    from cuga.backend.cuga_graph.nodes.cuga_flow.decision_agent import DecisionAgent

    d = DecisionAgent(gateway_id="G", policy="", condition="${score} > 0.6")
    assert d._eval_condition_node({"process_variables": {"score": 0.75}})["condition_result"] == "TRUE"
    assert d._eval_condition_node({"process_variables": {"score": 0.2}})["condition_result"] == "FALSE"


def test_timeout_is_passed_not_defaulted(fake):
    """The a2a-sdk default is 30s, below the 120s Kogito control-point ceiling."""
    asyncio.run(_registry().send("agent0", "q", ra.ROLE_CONSULT))
    assert fake.sends[0]["timeout"] == 90


def test_unset_timeout_stays_under_the_kogito_ceiling(fake):
    reg = ra.RemoteAgentRegistry({"agent0": {"url": "http://x"}})
    asyncio.run(reg.send("agent0", "q", ra.ROLE_FULFILL))
    assert 30 < fake.sends[0]["timeout"] < 120


def test_failure_split_hard_for_tasks_soft_for_consultation():
    """A task that cannot execute is a broken app; a consultation that cannot be reached
    is a less-informed but still sound decision. No fake — a closed port exercises the
    real connection-error path."""
    reg = ra.RemoteAgentRegistry({"agent0": {"url": "http://127.0.0.1:9", "timeout": 2}})

    with pytest.raises(Exception):
        asyncio.run(ra.RemoteTaskExecutor("agent0", reg).invoke("work"))

    out = asyncio.run(ra.make_consultation_tool("agent0", reg, "gateway 'G'").ainvoke({"question": "which?"}))
    assert "unavailable" in out.lower()
    assert "policy" in out.lower(), "the agent must be told to decide without it"


def test_multiple_remote_agents_are_independent(fake):
    reg = ra.RemoteAgentRegistry(
        {"agent0": {"url": "http://a", "timeout": 90}, "report_bot": {"url": "http://b", "timeout": 45}}
    )

    async def go():
        await ra.RemoteTaskExecutor("agent0", reg).invoke("work")
        await ra.make_consultation_tool("report_bot", reg, "gateway 'G'").ainvoke({"question": "q"})

    asyncio.run(go())
    assert fake.card_calls == 2, "each agent resolves its own card"
    assert [s["timeout"] for s in fake.sends] == [90, 45], "per-agent timeouts, not shared"


def test_unknown_name_raises_with_context(fake):
    reg = _registry()
    with pytest.raises(ValueError, match="typo_bot"):
        reg.require("typo_bot", "gateway 'G' human_consultation")
    reg.require("agent0", "gateway 'G' human_consultation")  # declared → no raise


def test_missing_sdk_fails_at_load_not_mid_process(monkeypatch):
    import importlib

    proto = importlib.import_module("cuga.backend.cuga_graph.nodes.cuga_supervisor.a2a_protocol")
    monkeypatch.setattr(proto, "HAS_A2A_SDK", False)
    with pytest.raises(ImportError, match="a2a-sdk"):
        _registry().require("agent0", "task 'T' agent_type")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
