"""Feature-off golden: one CodeAct run, pinned, with function-calling code present.

``snapshots/codeact_feature_off.json`` was recorded on the tree *before* the
function-calling execution path was routed through the sandbox (#777): every
field of the final graph state, every message (type, content, tool calls, call
id), the role and content of every outbound turn the model saw (the system prompt
as a marker), and the sandbox's variable bookkeeping. Timestamps are dropped;
other non-JSON values are compared by ``repr``.

``snapshots/codeact_feature_off_system_prompt.txt`` pins the whole system prompt
those turns carry, apart from whitespace at line ends and blank lines at either
end. It was recorded after #777 merged (for #976), so it guards later changes; it
cannot show that #777 left the prompt unchanged. The model must also see no bound
tools and no extra request arguments.

Scope: this one scenario (one tool; no apps, ``find_tools``, todos, policies,
few-shots, memory, agent spawning, forced autonomous mode or tool binding). Other
CodeAct paths are not pinned here.

Re-record (only for an intentional CodeAct change, never to make this pass):
``CUGA_SNAPSHOT_WRITE=1 pytest <this file>``.
"""

from __future__ import annotations

import difflib
import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import MemorySaver

from cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_graph import CugaLiteState, create_cuga_lite_graph
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.code_executor import CodeExecutor
from cuga.backend.cuga_graph.nodes.cuga_lite.tracking import tracker as tracker_module

pytestmark = pytest.mark.unit

SNAPSHOT = Path(__file__).parent / "snapshots" / "codeact_feature_off.json"
SYSTEM_PROMPT = Path(__file__).parent / "snapshots" / "codeact_feature_off_system_prompt.txt"
_VOLATILE_KEYS = {"created_at"}
CALLS: list = []


class _Bound:
    def __init__(self, model):
        self._model = model

    async def ainvoke(self, messages, config=None, **kwargs):
        return await self._model.ainvoke(messages, config=config, **kwargs)


class _ScriptedModel:
    def __init__(self, responses):
        self._responses = list(responses)
        self.seen: list = []
        self.bound: list = []  # tool names of every bind_tools call
        self.request_kwargs: list = []  # anything sent besides the messages and config

    def bind_tools(self, tools, **kwargs):
        self.bound.append([getattr(t, "name", t) for t in tools])
        return _Bound(self)

    async def ainvoke(self, messages, config=None, **kwargs):
        self.seen.append(list(messages))
        self.request_kwargs.append(kwargs)
        return self._responses.pop(0)


def _provider(*tools):
    provider = MagicMock()
    provider.get_all_tools = AsyncMock(return_value=list(tools))
    provider.get_apps = AsyncMock(return_value=[])
    provider.get_tools = AsyncMock(return_value=[])
    provider.app_name = "test_app"
    return provider


def _echo_tool():
    async def echo(value: int) -> int:
        """Echo a value."""
        CALLS.append(("echo", value))
        return value

    return StructuredTool.from_function(coroutine=echo, name="echo", description="Echo a value.")


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    from cuga.config import settings

    monkeypatch.setattr(settings.policy, "enabled", False, raising=False)
    # Settings that change the prompt or the bound tools, pinned off so a local override
    # cannot: the memory section (it reads the evolve setting, a preferences row in the
    # local database and, with memory on, a running Evolve server; pinning it also skips
    # that I/O), agent spawning, forced autonomous mode and tool binding.
    monkeypatch.setattr("cuga.backend.evolve.memory.memory_enabled", AsyncMock(return_value=False))
    monkeypatch.setattr(settings.agent_spawn, "enabled", False, raising=False)
    monkeypatch.setattr(settings.advanced_features, "force_autonomous_mode", False, raising=False)
    monkeypatch.setattr(settings.advanced_features, "cuga_lite_bind_tools_mode", "none", raising=False)
    CALLS.clear()
    yield
    CALLS.clear()
    tracker_module._tool_call_budget_context.set(None)
    tracker_module._thread_tool_call_budget_context.set(None)
    tracker_module._block_tool_call_budget_context.set(None)
    tracker_module._block_tool_call_cap_override_context.set(None)


def _normalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _normalize(v) for k, v in value.items() if k not in _VOLATILE_KEYS}
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    if hasattr(value, "type") and hasattr(value, "content"):  # a message object
        out = {"type": type(value).__name__, "content": value.content}
        if getattr(value, "tool_calls", None):
            out["tool_calls"] = value.tool_calls
        if getattr(value, "tool_call_id", None):
            out["tool_call_id"] = value.tool_call_id
        return out
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def _outbound(messages: list) -> list:
    """The dict-serialised CodeAct turns, system prompt replaced by a marker."""
    out = []
    for m in messages:
        assert isinstance(m, dict), "CodeAct sends dict turns"
        out.append(
            {"role": m["role"], "content": "<system prompt>" if m["role"] == "system" else m["content"]}
        )
    return out


def _canonical(prompt: str) -> str:
    """The prompt as the golden stores it: no whitespace at line ends and no blank lines at
    either end, so an editor that trims on save cannot break the comparison."""
    return "\n".join(line.rstrip() for line in prompt.splitlines()).strip("\n") + "\n"


@pytest.mark.asyncio
async def test_codeact_run_matches_the_recorded_snapshot(request):
    model = _ScriptedModel(
        [
            AIMessage(content="```python\nr = await echo(value=3)\nprint(r)\n```"),
            AIMessage(content="It printed 3."),
        ]
    )
    graph = create_cuga_lite_graph(
        model=model, tool_provider=_provider(_echo_tool()), apps_list=[], thread_id="t"
    ).compile(checkpointer=MemorySaver())
    result = await graph.ainvoke(
        CugaLiteState(chat_messages=[HumanMessage(content="use the tools")]),
        config={"configurable": {"thread_id": "codeact-snapshot", "enable_todos": False}},
    )

    prompt = result["prepared_prompt"]
    assert "```python" in prompt
    assert all(turn[0]["content"] == prompt for turn in model.seen), "system prompt is the prepared prompt"
    assert model.bound == [], f"CodeAct bound tools to the model: {model.bound}"
    assert model.request_kwargs == [{}] * len(model.seen), f"extra request arguments: {model.request_kwargs}"

    state = _normalize({k: v for k, v in result.items() if k != "prepared_prompt"})
    snapshot = {
        "calls": [list(c) for c in CALLS],
        "state": state,
        "outbound": [_outbound(turn) for turn in model.seen],
    }

    current_prompt = _canonical(prompt)
    if os.environ.get("CUGA_SNAPSHOT_WRITE"):
        SNAPSHOT.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
        SYSTEM_PROMPT.write_text(current_prompt, encoding="utf-8", newline="\n")
        pytest.skip(f"snapshot written to {SNAPSHOT} and {SYSTEM_PROMPT}")

    recorded = json.loads(SNAPSHOT.read_text())
    assert snapshot == recorded, "CodeAct behaviour changed with the feature off"

    recorded_prompt = _canonical(SYSTEM_PROMPT.read_text(encoding="utf-8"))
    if current_prompt != recorded_prompt:
        diff = "".join(
            difflib.unified_diff(
                recorded_prompt.splitlines(keepends=True),
                current_prompt.splitlines(keepends=True),
                fromfile=f"recorded {SYSTEM_PROMPT.name}",
                tofile="current CodeAct system prompt",
            )
        )
        test_file = os.path.relpath(__file__, request.config.rootpath)
        pytest.fail(
            "CodeAct system prompt changed with the feature off. If the change is intended, "
            f"re-record with CUGA_SNAPSHOT_WRITE=1 pytest {test_file}\n{diff}"
        )


@pytest.mark.asyncio
async def test_find_tools_listing_is_still_stripped_from_codeact_variables():
    """The executor drops find_tools listing markdown from a block's variables by default."""
    state = CugaLiteState(chat_messages=[])
    listing = "# Found 1 Matching Tool(s)\n**Query:** q\n## 1. `x`"

    async def lookup():
        return listing

    code = "tools = await lookup()\nkept = 1\n"
    _, new_vars = await CodeExecutor.eval_with_tools_async(code=code, _locals={"lookup": lookup}, state=state)

    assert new_vars == {"kept": 1}
