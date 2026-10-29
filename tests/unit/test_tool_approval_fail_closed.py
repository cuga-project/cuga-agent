"""Tool approval fails closed when the check cannot run (#880).

If the approval check cannot run, the code must not run unapproved. That covers
a policy store that errors or cannot connect, an unreadable stored policy, a
policy system that cannot be set up, and a check that raises. The run pauses for
approval as it does for a matched policy: approving runs the code, denying runs
nothing. A working store with no matching policy still runs the code, and the
pause for a matched policy is unchanged.

Most tests go through the real SDK in both execution modes. The handler-level
tests cover states a full run does not reach on its own, and the supervisor.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.graph import END

from cuga.backend.cuga_graph.nodes.cuga_agent_core.policy.tool_approval_handler import ToolApprovalHandler
from cuga.backend.cuga_graph.nodes.human_in_the_loop.followup_model import ActionResponse, ActionType

pytestmark = pytest.mark.unit

MODES = ["codeact", "function_calling"]
FAILED_CHECK_TEXT = "policies could not be checked"
_DONE = "Deleted record 1."


class _Bound:
    """What ``bind_tools`` returns: a runnable wrapping the model."""

    def __init__(self, model):
        self._model = model

    async def ainvoke(self, messages, config=None, **kwargs):
        return await self._model.ainvoke(messages, config=config, **kwargs)


class _ScriptedModel:
    def __init__(self, responses):
        self._responses = list(responses)
        self.invocations = 0
        self.seen: list = []

    def bind_tools(self, tools, **kwargs):
        return _Bound(self)  # a real bind returns a new runnable, never the model itself

    async def ainvoke(self, messages, config=None, **kwargs):
        self.invocations += 1
        self.seen.append(list(messages))
        if not self._responses:
            raise AssertionError("model asked for more responses than scripted")
        return self._responses.pop(0)


RAN: list = []


def _call(record_id: int, call_id: str) -> dict:
    return {"name": "delete_record", "args": {"record_id": record_id}, "id": call_id, "type": "tool_call"}


def _turn(mode: str, record_id: int = 1, call_id: str = "c1") -> AIMessage:
    """A model reply that tries to run the guarded tool."""
    if mode == "function_calling":
        return AIMessage(content="", tool_calls=[_call(record_id, call_id)])
    return AIMessage(content=f"```python\nprint(await delete_record(record_id={record_id}))\n```")


def _model(mode: str) -> _ScriptedModel:
    # The second reply is what the model says after the tool ran. A test that
    # expects a pause asserts it was never used.
    return _ScriptedModel([_turn(mode), AIMessage(content=_DONE)])


def _delete_record():
    async def delete_record(record_id: int) -> str:
        """Delete a record."""
        RAN.append(record_id)
        return "deleted"

    return StructuredTool.from_function(
        coroutine=delete_record, name="delete_record", description="Delete a record."
    )


def _response(confirmed: bool) -> ActionResponse:
    return ActionResponse(
        action_id="tool_approval",
        response_type=ActionType.CONFIRMATION,
        confirmed=confirmed,
        timestamp=datetime.now().isoformat(),
    )


def _agent(model, mode: str):
    from cuga.sdk import CugaAgent

    return CugaAgent(tools=[_delete_record()], model=model, execution_mode=mode, reset_policy_storage=True)


async def _guarded_agent(model, mode: str):
    agent = _agent(model, mode)
    await agent.policies.add_tool_approval(
        name="Approve deletions",
        required_tools=["delete_record"],
        approval_message="Deleting needs approval.",
    )
    return agent


def _toggleable_outage(monkeypatch, agent) -> dict:
    """Make the policy store's listing fail while ``outage["on"]`` is true."""
    backend = agent._policy_system.storage._backend
    real_list = backend.list_policies
    outage = {"on": True}

    async def maybe_unavailable(*args, **kwargs):
        if outage["on"]:
            raise RuntimeError("policy store unavailable")
        return await real_list(*args, **kwargs)

    monkeypatch.setattr(backend, "list_policies", maybe_unavailable)
    return outage


def _break_policy_store(monkeypatch, agent) -> None:
    _toggleable_outage(monkeypatch, agent)


def _break_approval_check(monkeypatch, agent) -> None:
    async def crashed(*args, **kwargs):
        raise RuntimeError("approval check crashed")

    monkeypatch.setattr(agent._policy_system.agent, "check_tool_approval_for_code", crashed)


_BREAKAGES = {"policy_store": _break_policy_store, "approval_check": _break_approval_check}


def _failed_check():
    """The id and message of the failed-check pause, imported where used."""
    from cuga.backend.cuga_graph.nodes.cuga_agent_core.policy import tool_approval_handler as handler

    return handler.APPROVAL_CHECK_FAILED_POLICY_ID, handler.APPROVAL_CHECK_FAILED_MESSAGE


def _decisions(result) -> list:
    pairs = []
    for decision in result.policy_decisions or []:
        data = decision if isinstance(decision, dict) else decision.model_dump(mode="json")
        pairs.append((data["policy_id"], data["outcome"]))
    return pairs


def _set_everywhere(monkeypatch, section: str, key: str, value) -> None:
    """Set ``settings.<section>.<key>`` on every settings object a cuga module holds.

    A test that reloads ``cuga.config`` leaves modules imported before it (the
    graph builders among them) holding the previous settings object, so setting
    it on the current one alone does not reach the code under test.
    """
    import sys

    held = {}
    for name, module in list(sys.modules.items()):
        if name == "cuga" or name.startswith("cuga."):
            candidate = getattr(module, "settings", None)
            if candidate is not None and getattr(candidate, section, None) is not None:
                held[id(candidate)] = candidate
    for candidate in held.values():
        monkeypatch.setattr(getattr(candidate, section), key, value, raising=False)


@pytest.fixture(autouse=True)
def _settings(monkeypatch, tmp_path):
    import cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_graph  # noqa: F401  (load the graph's settings)

    _set_everywhere(monkeypatch, "policy", "enabled", True)
    _set_everywhere(monkeypatch, "evolve", "enabled", False)
    # Policy filesystem sync writes each added policy under ./.cuga and loads it
    # back into later agents; keep that folder per test.
    monkeypatch.chdir(tmp_path)
    # Building the SDK graph constructs a default OpenAI client, which needs some
    # key; the scripted model answers every call, so it is never used.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-used")
    RAN.clear()
    yield
    RAN.clear()


# ── A failed check pauses; approving runs the code, denying runs nothing ──────


@pytest.mark.asyncio
@pytest.mark.parametrize("breakage", sorted(_BREAKAGES))
@pytest.mark.parametrize("mode", MODES)
async def test_a_failed_approval_check_pauses_instead_of_running_the_tool(monkeypatch, mode, breakage):
    model = _model(mode)
    agent = await _guarded_agent(model, mode)
    _BREAKAGES[breakage](monkeypatch, agent)

    paused = await agent.invoke("delete record 1", thread_id=f"fail-closed-{mode}-{breakage}")

    assert RAN == [], "the tool ran although its approval could not be checked"
    assert model.invocations == 1, "the run continued past the failed check"
    assert FAILED_CHECK_TEXT in paused.answer, paused.answer
    assert "unavailable" not in paused.answer and "crashed" not in paused.answer, (
        "error detail reached the user"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", MODES)
async def test_approving_after_a_failed_check_runs_the_tool(monkeypatch, mode):
    model = _model(mode)
    agent = await _guarded_agent(model, mode)
    _break_policy_store(monkeypatch, agent)

    await agent.invoke("delete record 1", thread_id=f"fail-closed-approve-{mode}")
    assert RAN == [], "paused before the tool ran"
    resumed = await agent.invoke(
        None, thread_id=f"fail-closed-approve-{mode}", action_response=_response(True)
    )

    assert RAN == [1] and resumed.answer == _DONE, resumed.answer


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", MODES)
async def test_denying_after_a_failed_check_runs_nothing(monkeypatch, mode):
    model = _model(mode)
    agent = await _guarded_agent(model, mode)
    _break_policy_store(monkeypatch, agent)

    await agent.invoke("delete record 1", thread_id=f"fail-closed-deny-{mode}")
    denied = await agent.invoke(None, thread_id=f"fail-closed-deny-{mode}", action_response=_response(False))

    assert RAN == [] and model.invocations == 1
    assert "cancelled" in denied.answer.lower(), denied.answer


# ── What one approval covers ──────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", MODES)
async def test_an_approval_after_a_failed_check_covers_only_that_block(monkeypatch, mode):
    """The next block in the same turn is checked again, and pauses again while the store is down."""
    model = _ScriptedModel([_turn(mode, 1, "c1"), _turn(mode, 2, "c2"), AIMessage(content=_DONE)])
    agent = await _guarded_agent(model, mode)
    _break_policy_store(monkeypatch, agent)
    thread = f"covers-one-block-{mode}"

    await agent.invoke("delete records 1 and 2", thread_id=thread)
    second_pause = await agent.invoke(None, thread_id=thread, action_response=_response(True))

    assert RAN == [1], "only the approved block ran"
    assert model.invocations == 2 and FAILED_CHECK_TEXT in second_pause.answer, second_pause.answer

    await agent.invoke(None, thread_id=thread, action_response=_response(False))
    assert RAN == [1], "denying the second pause ran nothing more"


@pytest.mark.asyncio
async def test_several_native_calls_pause_once_and_all_run_on_approval(monkeypatch):
    model = _ScriptedModel(
        [
            AIMessage(content="", tool_calls=[_call(1, "c1"), _call(2, "c2")]),
            AIMessage(content="Deleted records 1 and 2."),
        ]
    )
    agent = await _guarded_agent(model, "function_calling")
    _break_policy_store(monkeypatch, agent)

    paused = await agent.invoke("delete records 1 and 2", thread_id="fc-two-calls")
    assert RAN == [] and FAILED_CHECK_TEXT in paused.answer, paused.answer
    resumed = await agent.invoke(None, thread_id="fc-two-calls", action_response=_response(True))

    assert RAN == [1, 2] and resumed.answer == "Deleted records 1 and 2.", resumed.answer
    replies = {m.tool_call_id: m.content for m in model.seen[1] if isinstance(m, ToolMessage)}
    assert replies == {"c1": "deleted", "c2": "deleted"}, "each call id got its own reply"


# ── The cost of failing closed: a check that cannot run pauses every block ────


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", MODES)
async def test_a_store_outage_pauses_even_when_no_approval_policy_exists(monkeypatch, mode):
    """During an outage the check cannot tell "no approval policies" from "unreadable"."""
    model = _model(mode)
    agent = _agent(model, mode)
    assert await agent.policies.list() == []
    _break_policy_store(monkeypatch, agent)

    paused = await agent.invoke("delete record 1", thread_id=f"outage-no-policies-{mode}")

    assert RAN == [] and FAILED_CHECK_TEXT in paused.answer, paused.answer


@pytest.mark.asyncio
async def test_a_store_outage_pauses_code_that_calls_no_tool(monkeypatch):
    model = _ScriptedModel([AIMessage(content="```python\nprint(1 + 1)\n```"), AIMessage(content="2")])
    agent = await _guarded_agent(model, "codeact")
    _break_policy_store(monkeypatch, agent)

    paused = await agent.invoke("what is 1 + 1", thread_id="outage-no-tool")

    assert model.invocations == 1 and FAILED_CHECK_TEXT in paused.answer, paused.answer


@pytest.mark.asyncio
async def test_an_unreadable_approval_policy_pauses_instead_of_being_skipped(monkeypatch):
    model = _model("codeact")
    agent = await _guarded_agent(model, "codeact")

    async def corrupted(*args, **kwargs):
        return [{"policy_json": "{not valid json"}]

    monkeypatch.setattr(agent._policy_system.storage._backend, "list_policies", corrupted)

    paused = await agent.invoke("delete record 1", thread_id="unreadable-policy")

    assert RAN == [] and FAILED_CHECK_TEXT in paused.answer, paused.answer


@pytest.mark.asyncio
async def test_a_store_that_cannot_connect_pauses(monkeypatch):
    model = _model("codeact")
    agent = await _guarded_agent(model, "codeact")
    storage = agent._policy_system.storage

    async def cannot_connect(*args, **kwargs):
        raise RuntimeError("policy store cannot connect")

    monkeypatch.setattr(storage, "_connected", False)
    monkeypatch.setattr(storage, "initialize_async", cannot_connect)

    paused = await agent.invoke("delete record 1", thread_id="store-cannot-connect")

    assert RAN == [] and FAILED_CHECK_TEXT in paused.answer, paused.answer


# ── What still runs without a pause ───────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", MODES)
async def test_with_no_stored_policies_the_tool_runs_without_approval(mode):
    """A working store with no tool-approval policy is not an error: nothing to approve."""
    model = _model(mode)
    agent = _agent(model, mode)

    result = await agent.invoke("delete record 1", thread_id=f"no-stored-policies-{mode}")

    assert RAN == [1] and result.answer == _DONE, result.answer


@pytest.mark.asyncio
async def test_a_disabled_approval_policy_does_not_pause():
    model = _model("codeact")
    agent = _agent(model, "codeact")
    await agent.policies.add_tool_approval(
        name="Approve deletions", required_tools=["delete_record"], enabled=False
    )

    result = await agent.invoke("delete record 1", thread_id="disabled-policy")

    assert RAN == [1] and result.answer == _DONE, result.answer


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", MODES)
async def test_with_policies_turned_off_a_broken_store_is_never_consulted(monkeypatch, mode):
    model = _model(mode)
    agent = await _guarded_agent(model, mode)
    _break_policy_store(monkeypatch, agent)
    _set_everywhere(monkeypatch, "policy", "enabled", False)

    result = await agent.invoke("delete record 1", thread_id=f"policies-off-{mode}")

    assert RAN == [1] and result.answer == _DONE, result.answer


def _found(states, key: str) -> list:
    """Every non-empty ``key`` value anywhere in streamed state updates."""
    found = []

    def walk(item):
        if isinstance(item, dict):
            if item.get(key):
                found.append(item[key])
            for value in item.values():
                walk(value)
        elif isinstance(item, (list, tuple)):  # stream yields (namespace, {node: update})
            for value in item:
                walk(value)

    for state in states:
        walk(state)
    return found


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", MODES)
async def test_a_streamed_run_surfaces_the_failed_check_as_an_approval_request(monkeypatch, mode):
    """The server and UI consume ``stream``: the pause must arrive as an approval request, not an error."""
    model = _model(mode)
    agent = await _guarded_agent(model, mode)
    _break_policy_store(monkeypatch, agent)
    thread = f"stream-{mode}"

    states = [state async for state in agent.stream("delete record 1", thread_id=thread)]

    assert RAN == [], "the tool ran during the stream"
    actions = _found(states, "hitl_action")
    action_ids = [
        a.get("action_id") if isinstance(a, dict) else getattr(a, "action_id", None) for a in actions
    ]
    assert action_ids and set(action_ids) == {"tool_approval"}, action_ids
    assert any(FAILED_CHECK_TEXT in str(answer) for answer in _found(states, "final_answer"))

    [state async for state in agent.stream(None, thread_id=thread, action_response=_response(True))]

    assert RAN == [1], "approving through the stream ran the tool"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", MODES)
async def test_once_the_store_is_back_the_real_policy_applies_again(monkeypatch, mode):
    model = _ScriptedModel([_turn(mode, 1, "c1"), _turn(mode, 2, "c2")])
    agent = await _guarded_agent(model, mode)
    outage = _toggleable_outage(monkeypatch, agent)
    thread = f"store-recovers-{mode}"

    first = await agent.invoke("delete record 1", thread_id=thread)
    assert FAILED_CHECK_TEXT in first.answer, first.answer
    await agent.invoke(None, thread_id=thread, action_response=_response(False))
    outage["on"] = False
    second = await agent.invoke("delete record 2", thread_id=thread)

    assert RAN == []
    assert "Deleting needs approval." in second.answer, second.answer
    assert FAILED_CHECK_TEXT not in second.answer, "the outage pause outlived the outage"


# ── Observability and the last resort ─────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("approve", [True, False])
async def test_the_failed_check_is_recorded_as_its_own_policy_decision(monkeypatch, approve):
    model = _model("codeact")
    agent = await _guarded_agent(model, "codeact")
    _break_policy_store(monkeypatch, agent)
    thread = f"decision-{'approved' if approve else 'denied'}"

    paused = await agent.invoke("delete record 1", thread_id=thread)
    resumed = await agent.invoke(None, thread_id=thread, action_response=_response(approve))

    failed_id, _ = _failed_check()
    assert (failed_id, "approval_required") in _decisions(paused), _decisions(paused)
    outcome = "approved" if approve else "denied"
    assert (failed_id, outcome) in _decisions(resumed), _decisions(resumed)


@pytest.mark.asyncio
async def test_if_even_the_pause_cannot_be_built_the_run_errors_and_the_tool_does_not_run(monkeypatch):
    model = _model("codeact")
    agent = await _guarded_agent(model, "codeact")
    _break_policy_store(monkeypatch, agent)

    def cannot_build(*args, **kwargs):
        raise RuntimeError("cannot build the approval pause")

    monkeypatch.setattr(ToolApprovalHandler, "_create_approval_interrupt", staticmethod(cannot_build))

    with pytest.raises(RuntimeError, match="cannot build the approval pause"):
        await agent.invoke("delete record 1", thread_id="pause-cannot-be-built")

    assert RAN == [] and model.invocations == 1


# ── Handler level: states a full run does not reach, and the supervisor ───────

_CODE = "print(await delete_record(record_id=1))"


def _lite_adapter():
    from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.graph_adapter import AgentGraphAdapter

    return AgentGraphAdapter(
        tracker=MagicMock(),
        base_callbacks=[],
        task_todos_ref=[],
        tools_context_ref={},
        base_tool_provider=None,
        tools_context={},
    )


def _lite_state():
    return SimpleNamespace(
        chat_messages=[HumanMessage(content="delete record 1")], step_count=0, cuga_lite_metadata={}
    )


async def _check(policy_system, adapter=None, state=None):
    return await ToolApprovalHandler.check_and_create_approval_interrupt(
        adapter or _lite_adapter(),
        state or _lite_state(),
        _CODE,
        _CODE,
        {"configurable": {"policy_system": policy_system}},
    )


async def _store(*policies):
    from cuga.backend.cuga_graph.policy.storage import PolicyStorage

    storage = PolicyStorage()
    await storage.initialize_async()
    for policy in policies:
        await storage.add_policy(policy)
    return storage


def _approval_policy(**overrides):
    from cuga.backend.cuga_graph.policy.models import ToolApproval

    return ToolApproval(
        id="approve-deletions",
        name="Approve deletions",
        description="Approval gate.",
        required_tools=["delete_record"],
        approval_message="Deleting needs approval.",
        **overrides,
    )


def _policy_system(**kwargs):
    from cuga.backend.cuga_graph.policy.configurable import PolicyConfigurable

    return PolicyConfigurable(**kwargs)


@pytest.mark.asyncio
async def test_a_policy_system_not_set_up_yet_with_no_policies_does_not_pause():
    policy_system = _policy_system(storage=await _store())
    assert policy_system.agent is None

    assert await _check(policy_system) is None, "an empty store paused the run"
    assert policy_system.agent is not None, "the check set the policy system up"


@pytest.mark.asyncio
async def test_a_policy_system_not_set_up_yet_still_enforces_its_stored_policy():
    command = await _check(_policy_system(storage=await _store(_approval_policy())))

    assert command is not None and command.update["hitl_action"] is not None
    assert command.update["cuga_lite_metadata"]["policy_id"] == "approve-deletions", (
        "paused for the wrong reason"
    )


@pytest.mark.asyncio
async def test_a_policy_system_that_cannot_be_set_up_pauses(monkeypatch):
    storage = await _store(_approval_policy())

    async def cannot_set_up(*args, **kwargs):
        raise RuntimeError("embedding service unavailable")

    monkeypatch.setattr(storage, "_embedding_initialized", False)
    monkeypatch.setattr(storage, "_initialize_embedding_function", cannot_set_up)

    command = await _check(_policy_system(storage=storage))

    assert command is not None
    assert command.update["cuga_lite_metadata"]["policy_id"] == _failed_check()[0]


@pytest.mark.asyncio
async def test_the_supervisor_pauses_the_same_way_when_the_check_fails():
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.supervisor_graph_adapter import SupervisorGraphAdapter

    crashed = AsyncMock(side_effect=RuntimeError("approval check crashed"))
    policy_system = _policy_system(agent=SimpleNamespace(check_tool_approval_for_code=crashed))
    adapter = SupervisorGraphAdapter(agents={})
    state = SimpleNamespace(
        supervisor_chat_messages=[HumanMessage(content="delete record 1")],
        step_count=0,
        supervisor_metadata={},
    )

    command = await _check(policy_system, adapter, state)

    update = command.update
    assert command.goto == END
    failed_id, failed_message = _failed_check()
    assert update["supervisor_metadata"]["policy_id"] == failed_id
    assert update["supervisor_metadata"]["policy_confidence"] == 0.0, "an outage is not a confident match"
    assert update["sender"] == adapter.sender_name and update["hitl_action"] is not None
    assert update["supervisor_chat_messages"][-1].content == _CODE, (
        "the assistant turn is kept with the pause"
    )
    assert failed_message in update["final_answer"]


def _matching_policy_agent(**policy_overrides):
    """A policy agent whose store holds the deletion approval policy."""
    from cuga.backend.cuga_graph.policy.agent import PolicyAgent

    policy_agent = PolicyAgent.__new__(PolicyAgent)
    policy_agent.storage = SimpleNamespace(
        list_policies=AsyncMock(return_value=[_approval_policy(**policy_overrides)])
    )
    return policy_agent


@pytest.mark.asyncio
async def test_an_error_pausing_for_a_matched_policy_is_not_reported_as_a_failed_check(monkeypatch):
    """Only the check falls back to the failed-check pause; the matched pause is not built twice."""
    real_interrupt = ToolApprovalHandler._create_approval_interrupt
    attempts = []

    def fails_once(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("cannot build the approval pause")
        return real_interrupt(*args, **kwargs)

    monkeypatch.setattr(ToolApprovalHandler, "_create_approval_interrupt", staticmethod(fails_once))
    state = _lite_state()

    with pytest.raises(RuntimeError, match="cannot build the approval pause"):
        await _check(_policy_system(agent=_matching_policy_agent()), state=state)

    decisions = [(d["policy_id"], d["outcome"]) for d in state.cuga_lite_metadata["policy_decisions"]]
    assert decisions == [("approve-deletions", "approval_required")]
    assert state.cuga_lite_metadata["policy_name"] == "Approve deletions"


@pytest.mark.asyncio
async def test_the_approval_check_sets_the_policy_system_up_like_the_other_checks():
    """One set-up step for every policy check: a system with an agent handed in is still marked set up."""
    policy_system = _policy_system(agent=_matching_policy_agent())

    await _check(policy_system)

    assert policy_system._initialized, "the approval check skipped the shared set-up step"


@pytest.mark.asyncio
async def test_concurrent_first_checks_set_the_policy_system_up_once(monkeypatch):
    storage = await _store()
    setups = []

    async def slow_setup(*args, **kwargs):
        setups.append(1)
        await asyncio.sleep(0.01)

    monkeypatch.setattr(storage, "_embedding_initialized", False)
    monkeypatch.setattr(storage, "_initialize_embedding_function", slow_setup)
    policy_system = _policy_system(storage=storage)

    results = await asyncio.gather(_check(policy_system), _check(policy_system))

    assert setups == [1], "two concurrent checks both ran the set-up"
    assert results == [None, None]


@pytest.mark.asyncio
@pytest.mark.parametrize("show_code_preview", [True, False])
async def test_a_matched_policy_pauses_exactly_as_before(show_code_preview):
    """The matched pause and the failed-check pause share one builder; the matched one must not change."""
    adapter = _lite_adapter()

    command = await _check(
        _policy_system(agent=_matching_policy_agent(show_code_preview=show_code_preview)), adapter
    )

    update = command.update
    metadata = update["cuga_lite_metadata"]
    expected = {
        "policy_type": "tool_approval",
        "policy_id": "approve-deletions",
        "policy_name": "Approve deletions",
        "policy_confidence": 1.0,
        "policy_reasoning": "Generated code uses tools that require approval",
        "required_tools": ["delete_record"],
        "matched_tools": ["delete_record"],
        "required_apps": None,
        "approval_message": "Deleting needs approval.",
        "show_code_preview": show_code_preview,
        "approval_required": True,
        "code_preview": [_CODE],
        "full_code": _CODE if show_code_preview else None,
    }
    assert command.goto == END
    assert {key: metadata.get(key) for key in expected} == expected
    assert [(d["policy_id"], d["outcome"]) for d in metadata["policy_decisions"]] == [
        ("approve-deletions", "approval_required")
    ]
    assert (update["script"], update["step_count"], update["sender"]) == (_CODE, 1, adapter.sender_name)
    assert update["final_answer"].startswith("## ✋ Approve deletions\n\nDeleting needs approval.")
    assert "**Tools requiring approval:** delete_record" in update["final_answer"]
    assert update["hitl_action"] is not None


# ── Storage ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_strict_listing_raises_where_the_default_returns_nothing():
    """Other callers keep the best-effort ``[]``; the approval check needs to tell an error apart."""
    from cuga.backend.cuga_graph.policy.storage import PolicyStorage

    class _UnavailableBackend:
        async def list_policies(self, *args, **kwargs):
            raise RuntimeError("policy store unavailable")

    storage = PolicyStorage(backend=_UnavailableBackend())
    storage._connected = True

    assert await storage.list_policies() == []
    with pytest.raises(RuntimeError, match="policy store unavailable"):
        await storage.list_policies(strict=True)
