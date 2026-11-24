"""Variables survive a tool-call budget error, end to end through ``CugaAgent.invoke``.

Accepted CodeAct change from #777 (#976): when a tool call is refused because a
budget is spent (the per-block cap, default 100; the turn cap; the conversation
cap), the variables the block computed before the refused call are kept, as for a
timeout or ``exit()``. Before, the block's variables were dropped with the error.

Each test runs the real SDK graph with a scripted model and checks what the next
block, the final no-tools pass and the next turn on the same thread see. In
function-calling mode the same budgets refuse calls inside one batch: every call
id is still answered and the results of the calls that ran are kept.
"""

from __future__ import annotations

import re
import sys

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import StructuredTool

from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.shared_nodes import TOOL_BUDGET_EXHAUSTED_INSTRUCTION
from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.graph_adapter import FC_BUDGET_CALL_REPLY
from cuga.backend.cuga_graph.nodes.cuga_lite.tracking import tracker as tracker_module

pytestmark = pytest.mark.unit

CALLS: list = []


class _Bound:
    def __init__(self, model):
        self._model = model

    async def ainvoke(self, messages, config=None, **kwargs):
        return self._model.answer(messages, bound=True)


class _ScriptedModel:
    """Queued replies; ``seen`` records (tools bound?, outbound messages) per call."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.seen: list = []

    def bind_tools(self, tools, **kwargs):
        return _Bound(self)

    async def ainvoke(self, messages, config=None, **kwargs):
        return self.answer(messages, bound=False)

    def answer(self, messages, *, bound):
        self.seen.append((bound, list(messages)))
        if not self._responses:
            raise AssertionError(f"model call #{len(self.seen)} was not scripted")
        reply = self._responses.pop(0)
        return reply if isinstance(reply, AIMessage) else AIMessage(content=reply)


class _Overlay:
    """Read-through view of a settings object with a few attributes replaced."""

    def __init__(self, base, **overrides):
        object.__setattr__(self, "_base", base)
        object.__setattr__(self, "_overrides", overrides)

    def __getattr__(self, name):
        overrides = object.__getattribute__(self, "_overrides")
        if name in overrides:
            return overrides[name]
        return getattr(object.__getattribute__(self, "_base"), name)

    def __setattr__(self, name, value):
        setattr(object.__getattribute__(self, "_base"), name, value)


def _caps(monkeypatch, *, block=0, run=0, thread=0):
    """Pin the three tool-call caps for this test only: 0 disables one, ``block=None``
    keeps the configured block cap (the two default-cap tests assert it is the shipped
    100, so they fail by design when the environment changes it).

    The tracker reads every cap from ``cuga.config.settings`` at call time, so an
    overlay there sets the caps whatever earlier tests in the process or a
    ``DYNACONF_`` variable did to them. ``_isolated`` takes the overlay back out of
    every module that imported it while it was installed.
    """
    from cuga.config import settings

    caps = {"max_tool_calls_per_run": run, "max_tool_calls_per_thread": thread}
    if block is not None:
        caps["max_tool_calls_per_block"] = block
    monkeypatch.setattr(
        "cuga.config.settings",
        _Overlay(settings, advanced_features=_Overlay(settings.advanced_features, **caps)),
    )


def _echo():
    async def echo(value: int) -> int:
        """Echo a value."""
        CALLS.append(value)
        return value

    return StructuredTool.from_function(coroutine=echo, name="echo", description="Echo a value.")


def _tc(value, call_id):
    return {"name": "echo", "args": {"value": value}, "id": call_id, "type": "tool_call"}


def _text(message) -> str:
    content = message["content"] if isinstance(message, dict) else message.content
    return content if isinstance(content, str) else str(content)


def _call_text(model, index) -> str:
    """Everything the model was sent on call ``index``."""
    return "\n".join(_text(m) for m in model.seen[index][1])


def _listed_variables(text) -> set:
    """Variable names in the variables summaries the model was shown."""
    return set(re.findall(r"^## (\w+)$", text, flags=re.MULTILINE))


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    from cuga.config import settings

    monkeypatch.chdir(tmp_path)  # SDK policy sync writes ./.cuga
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-used")  # graph build constructs a default client
    monkeypatch.setattr(settings.policy, "enabled", False, raising=False)
    monkeypatch.setattr(settings.evolve, "enabled", False, raising=False)
    CALLS.clear()
    yield
    CALLS.clear()
    # A module first imported during the test bound the overlay as its own ``settings``,
    # and monkeypatch restores only ``cuga.config``. Rebind those modules to the real object.
    for module in list(sys.modules.values()):
        if isinstance(getattr(module, "__dict__", {}).get("settings"), _Overlay):
            module.settings = settings
    tracker_module._tool_call_budget_context.set(None)
    tracker_module._thread_tool_call_budget_context.set(None)
    tracker_module._block_tool_call_budget_context.set(None)


def _agent(model, execution_mode="codeact"):
    """Mode and step discipline are pinned: a ``DYNACONF_`` override of either changes what runs."""
    from cuga.sdk import CugaAgent

    return CugaAgent(tools=[_echo()], model=model, execution_mode=execution_mode, step_discipline="off")


FOUR_CALLS = (
    "```python\n"
    "a = await echo(value=1)\n"
    "b = await echo(value=2)\n"
    "c = await echo(value=3)\n"
    "d = await echo(value=4)\n"
    "```"
)


# -- CodeAct ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_default_block_cap_keeps_the_variables_computed_before_the_refused_call(monkeypatch):
    from cuga.config import settings

    assert settings.advanced_features.max_tool_calls_per_block == 100, "runs at the shipped default"
    _caps(monkeypatch, block=None)
    model = _ScriptedModel(
        [
            "```python\nfirst = await echo(value=1)\nfor i in range(150):\n    last = await echo(value=i)\n"
            "after = 'never'\n```",
            "```python\nprint('kept:', first, last, i)\n```",
            "Done.",
        ]
    )

    result = await _agent(model).invoke("echo a lot", thread_id="block-cap")

    assert len(CALLS) == 100, "the 101st call of the block was refused"
    breach = _text(model.seen[1][1][-1])
    assert "Tool call limit reached for this code block: it made 100 tool calls" in breach
    assert {"first", "last", "i"} <= _listed_variables(breach), "the refusal names what was kept"
    assert "kept: 1 98 99" in _text(model.seen[2][1][-1]), "the next block read the kept values"
    assert {k: result.variables.get(k) for k in ("first", "last", "i")} == {"first": 1, "last": 98, "i": 99}
    assert "after" not in result.variables, "nothing after the refused call ran"
    assert result.answer == "Done."


@pytest.mark.asyncio
async def test_a_variable_rebound_before_the_refused_call_keeps_its_new_value(monkeypatch):
    """``total`` exists from block 1; block 2 rebinds it, then hits the cap. The kept
    value is the new one, not block 1's. (The rebinding does not read ``total``: a
    block runs inside a function, so ``total = total + 10`` raises UnboundLocalError.)"""
    _caps(monkeypatch, block=2)
    model = _ScriptedModel(
        [
            "```python\ntotal = await echo(value=5)\n```",
            "```python\ntotal = 15\nx = await echo(value=1)\ny = await echo(value=2)\nz = await echo(value=3)\n```",
            "```python\nprint('kept:', total, x, y)\n```",
            "Done.",
        ]
    )

    result = await _agent(model).invoke("add up", thread_id="block-cap-rebound")

    assert CALLS == [5, 1, 2]
    assert "kept: 15 1 2" in _text(model.seen[3][1][-1]), "block 3 read the kept values"
    assert result.variables.get("total") == 15, "total kept its new value, not block 1's 5"
    assert "z" not in result.variables


@pytest.mark.asyncio
async def test_run_cap_keeps_the_variables_for_the_final_pass_and_the_next_turn(monkeypatch):
    _caps(monkeypatch, run=3)
    model = _ScriptedModel(
        [
            FOUR_CALLS,
            "Got 1, 2 and 3 before the limit.",
            "```python\ntotal = await echo(value=a + b + c)\nprint('sum:', total)\n```",
            "The sum is 6.",
        ]
    )
    agent = _agent(model)

    turn1 = await agent.invoke("echo four values", thread_id="run-cap")

    assert CALLS == [1, 2, 3]
    *_, refusal, instruction = model.seen[1][1]
    assert "this run (one user turn) has already made 3 tool calls" in _text(refusal)
    assert TOOL_BUDGET_EXHAUSTED_INSTRUCTION in _text(instruction), "the final pass has no tools"
    kept = _listed_variables(_text(refusal))
    assert {"a", "b", "c"} <= kept and "d" not in kept, "the final pass is told what was kept"
    assert turn1.answer == "Got 1, 2 and 3 before the limit."
    assert {k: turn1.variables.get(k) for k in "abc"} == {"a": 1, "b": 2, "c": 3}
    assert "d" not in turn1.variables

    turn2 = await agent.invoke("add them up", thread_id="run-cap")

    assert CALLS == [1, 2, 3, 6], "turn 2 read the kept values, and its run budget was fresh"
    assert "sum: 6" in _text(model.seen[3][1][-1]), "the kept variables survived the checkpoint"
    assert turn2.answer == "The sum is 6."


@pytest.mark.asyncio
async def test_thread_cap_keeps_the_variables_for_a_turn_that_cannot_call_tools(monkeypatch):
    _caps(monkeypatch, thread=3)
    model = _ScriptedModel([FOUR_CALLS, "Got 1, 2 and 3.", "From what I kept: a=1, b=2, c=3."])
    agent = _agent(model)

    await agent.invoke("echo four values", thread_id="thread-cap")
    assert CALLS == [1, 2, 3]
    assert "this conversation has already made 3 tool calls" in _call_text(model, 1)

    turn2 = await agent.invoke("what did you get?", thread_id="thread-cap")

    assert len(model.seen) == 3, "turn 2 opens exhausted: one final pass, no code block"
    assert CALLS == [1, 2, 3]
    *_, question, instruction = model.seen[2][1]
    assert TOOL_BUDGET_EXHAUSTED_INSTRUCTION in _text(instruction)
    assert _text(question).startswith("what did you get?")
    assert {"a", "b", "c"} <= _listed_variables(_text(question)), (
        "turn 2's own message lists the kept variables"
    )
    assert {k: turn2.variables.get(k) for k in "abc"} == {"a": 1, "b": 2, "c": 3}
    assert turn2.answer == "From what I kept: a=1, b=2, c=3."


# -- Function calling -----------------------------------------------------------


@pytest.mark.asyncio
async def test_fc_run_cap_mid_batch_answers_every_call_and_keeps_the_results_that_ran(monkeypatch):
    _caps(monkeypatch, run=2)
    model = _ScriptedModel(
        [
            AIMessage(content="", tool_calls=[_tc(1, "c1"), _tc(2, "c2"), _tc(3, "c3")]),
            AIMessage(content="I got 1 and 2; the third call was refused."),
        ]
    )

    result = await _agent(model, execution_mode="function_calling").invoke(
        "echo 1, 2 and 3", thread_id="fc-run-cap"
    )

    assert CALLS == [1, 2]
    bound, final_pass = model.seen[1]
    assert not bound, "the final pass is sent without tools"
    replies = {m.tool_call_id: m.content for m in final_pass if isinstance(m, ToolMessage)}
    assert set(replies) == {"c1", "c2", "c3"}, "every call id is answered"
    assert replies["c1"] == "1" and replies["c2"] == "2"
    assert replies["c3"].startswith("Tool call limit reached: this run (one user turn) has already made 2")
    assert result.variables.get("tool_result_c1") == 1 and result.variables.get("tool_result_c2") == 2
    assert "tool_result_c3" not in result.variables
    assert result.answer == "I got 1 and 2; the third call was refused."


@pytest.mark.asyncio
async def test_fc_calls_made_in_the_final_pass_are_answered_and_the_next_turn_replays(monkeypatch):
    """The model ignores the no-tools instruction: its calls are answered, never run,
    so the checkpointed transcript has no dangling call id for the next turn."""
    _caps(monkeypatch, run=1)
    model = _ScriptedModel(
        [
            AIMessage(content="", tool_calls=[_tc(1, "c1"), _tc(2, "c2")]),
            AIMessage(content="Only 1 so far.", tool_calls=[_tc(3, "c3")]),
            AIMessage(content="", tool_calls=[_tc(4, "c4")]),
            AIMessage(content="Now 4."),
        ]
    )
    agent = _agent(model, execution_mode="function_calling")

    await agent.invoke("echo 1 and 2", thread_id="fc-final-pass")
    assert CALLS == [1], "the final pass ran nothing"

    turn2 = await agent.invoke("echo 4", thread_id="fc-final-pass")

    assert CALLS == [1, 4] and turn2.answer == "Now 4."
    replay = model.seen[2][1]
    call_ids = [c["id"] for m in replay if isinstance(m, AIMessage) for c in m.tool_calls]
    replies = {m.tool_call_id: m.content for m in replay if isinstance(m, ToolMessage)}
    assert call_ids == ["c1", "c2", "c3"] and set(replies) == set(call_ids), "no dangling call id"
    assert replies["c3"] == FC_BUDGET_CALL_REPLY


@pytest.mark.asyncio
async def test_fc_default_block_cap_refuses_the_101st_call_of_one_turn_and_stays_recoverable(monkeypatch):
    from cuga.config import settings

    assert settings.advanced_features.max_tool_calls_per_block == 100, "runs at the shipped default"
    _caps(monkeypatch, block=None)
    batch = [_tc(i, f"c{i}") for i in range(101)]
    model = _ScriptedModel([AIMessage(content="", tool_calls=batch), AIMessage(content="Echoed 100 of 101.")])

    result = await _agent(model, execution_mode="function_calling").invoke(
        "echo 0..100", thread_id="fc-block"
    )

    assert CALLS == list(range(100))
    bound, after = model.seen[1]
    assert bound, "a block breach is recoverable: tools stay bound"
    replies = {m.tool_call_id: m.content for m in after if isinstance(m, ToolMessage)}
    assert len(replies) == 101
    assert replies["c100"].startswith("Tool call limit reached for this code block: it made 100 tool calls")
    assert result.variables.get("tool_result_c99") == 99 and "tool_result_c100" not in result.variables
    assert result.answer == "Echoed 100 of 101."
