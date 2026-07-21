"""Browser planner must always supply a valid ``img`` when vision is effective,
even with no screenshot available — otherwise the prompt render raises
``missing variables {'img'}`` (issue: action agent img crash)."""

from langchain_core.messages import AIMessage

from cuga.backend.cuga_graph.nodes.browser.browser_planner_agent import (
    browser_planner_agent as bpa,
)
from cuga.backend.cuga_graph.nodes.browser.browser_planner_agent.browser_planner_agent import (
    BrowserPlannerAgent,
    _BLANK_IMAGE,
)
from cuga.backend.cuga_graph.state.agent_state import AgentState


class _CapturingChain:
    """Stand-in for the real chain; records the data dict it was invoked with."""

    def __init__(self):
        self.captured = None

    async def ainvoke(self, data):
        self.captured = data
        return AIMessage(content="ok", name="BrowserPlannerAgent")


def _make_agent(chain, use_vision_effective, template_requires_img=None):
    # Bypass __init__ (which builds a real LLM chain); run() only needs these.
    # template_requires_img is fixed at build time; defaults to the build-time
    # vision value, matching __init__.
    agent = object.__new__(BrowserPlannerAgent)
    agent.name = "BrowserPlannerAgent"
    agent.chain = chain
    agent.use_vision_effective = use_vision_effective
    agent._template_requires_img = (
        use_vision_effective if template_requires_img is None else template_requires_img
    )
    return agent


async def test_vision_effective_no_screenshot_supplies_blank_img(monkeypatch):
    monkeypatch.setattr(bpa.tracker, "images", [])  # no screenshot captured yet
    chain = _CapturingChain()
    agent = _make_agent(chain, use_vision_effective=True)

    await agent.run(AgentState(input="do a task", url=""))  # must not raise

    assert chain.captured is not None
    assert chain.captured["img"] == _BLANK_IMAGE  # valid img always supplied


async def test_vision_effective_uses_real_screenshot_when_present(monkeypatch):
    real_img = "data:image/png;base64,REALIMAGE"
    monkeypatch.setattr(bpa.tracker, "images", [real_img])
    chain = _CapturingChain()
    agent = _make_agent(chain, use_vision_effective=True)

    await agent.run(AgentState(input="do a task", url=""))

    assert chain.captured["img"] == real_img  # real screenshot preferred


async def test_vision_disabled_at_build_does_not_attach_img(monkeypatch):
    monkeypatch.setattr(bpa.tracker, "images", [])
    chain = _CapturingChain()
    agent = _make_agent(chain, use_vision_effective=False, template_requires_img=False)

    await agent.run(AgentState(input="do a task", url=""))

    assert "img" not in chain.captured  # no image slot when template has no img


async def test_img_supplied_after_vision_rejection_disables_flag(monkeypatch):
    # Singleton graph node: a prior vision rejection left use_vision_effective
    # False, but the build-time template still requires img. Must not crash.
    monkeypatch.setattr(bpa.tracker, "images", [])
    chain = _CapturingChain()
    agent = _make_agent(chain, use_vision_effective=False, template_requires_img=True)

    await agent.run(AgentState(input="do a task", url=""))

    assert chain.captured["img"] == _BLANK_IMAGE


async def test_vision_rejection_retries_with_blank_img_then_stays_safe(monkeypatch):
    # End-to-end retry path: real screenshot -> endpoint rejects vision -> retry
    # text-only with blank img -> a later run (vision now off) still supplies img.
    monkeypatch.setattr(bpa.tracker, "images", ["data:image/png;base64,REAL"])

    class _RejectingChain:
        def __init__(self):
            self.calls = []

        async def ainvoke(self, data):
            self.calls.append(data.copy())
            if len(self.calls) == 1:
                raise ValueError("model does not support vision")  # real marker
            return AIMessage(content="ok", name="BrowserPlannerAgent")

    chain = _RejectingChain()
    agent = _make_agent(chain, use_vision_effective=True)

    await agent.run(AgentState(input="do a task", url=""))
    assert len(chain.calls) == 2
    assert chain.calls[0]["img"] == "data:image/png;base64,REAL"  # first sends real image
    assert chain.calls[1]["img"] == _BLANK_IMAGE  # retry falls back to blank
    assert chain.calls[1]["use_vision"] is False
    assert agent.use_vision_effective is False  # flag flipped off for the instance

    await agent.run(AgentState(input="another task", url=""))
    assert len(chain.calls) == 3
    assert chain.calls[2]["img"] == _BLANK_IMAGE  # later run still safe
