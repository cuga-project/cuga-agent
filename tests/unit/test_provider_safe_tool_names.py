"""Provider-safe tool names for native tool binding (#879).

OpenAI-compatible providers reject a request whose tools array has a name
outside ``^[A-Za-z0-9_-]{1,64}$``. ``_safe_bind`` binds such tools under an
alias that lives only in the request; the decode boundary (``normalize_response``)
maps every alias in a reply back to the real name, so code, the approval check
and the sandbox only see real names.
"""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from typing import Any, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import MemorySaver
from loguru import logger
from pydantic import BaseModel

from cuga.backend.cuga_graph.nodes.cuga_agent_core.execution.code_extraction import (
    extract_code_from_model_response,
)
from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.graph_nodes import EXECUTION_OUTPUT_PREFIX
from cuga.backend.cuga_graph.nodes.cuga_agent_core.policy.tool_approval_handler import ToolApprovalHandler
from cuga.backend.cuga_graph.nodes.cuga_lite import cuga_lite_graph
from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.graph_adapter import AgentGraphAdapter
from cuga.backend.cuga_graph.nodes.cuga_lite.bind_tools import tool_names
from cuga.backend.cuga_graph.nodes.cuga_lite.bind_tools.tool_names import (
    PROVIDER_TOOL_NAME_RE,
    provider_safe_tool_name,
    provider_safe_tools,
    resolve_tool_names,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.bind_tools import (
    _safe_bind,
    resolve_model_with_bind_tools,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.providers.langchain import DirectLangChainToolsProvider
from cuga.backend.cuga_graph.nodes.cuga_lite.tracking import tracker as tracker_module
from cuga.backend.cuga_graph.policy.agent import PolicyAgent
from cuga.backend.cuga_graph.policy.configurable import PolicyConfigurable
from cuga.backend.cuga_graph.policy.models import ToolApproval

pytestmark = pytest.mark.unit

# Synthetic names only. LONG has 76 characters, a length registry names
# (``<app>_<tool>``) reach.
LONG = "acme_inventory_service_reconcile_warehouse_stock_levels_with_supplier_ledger"
ALIAS = provider_safe_tool_name(LONG)


class _Args(BaseModel):
    value: int


async def _reconcile(value: int) -> dict:
    return {"reconciled": value}


def _tool(name: str, **kwargs: Any) -> StructuredTool:
    return StructuredTool(
        name=name, description="Reconcile stock levels.", args_schema=_Args, coroutine=_reconcile, **kwargs
    )


def _code(name: str) -> str:
    return f"```python\nresult = await {name}(value=7)\nprint(result)\n```"


def _tool_call(name: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": {"value": 7}, "id": "call_1"}])


def _adapter(tools_context: dict) -> AgentGraphAdapter:
    return AgentGraphAdapter(
        tracker=MagicMock(),
        base_callbacks=[],
        task_todos_ref=[],
        tools_context_ref={},
        base_tool_provider=None,
        tools_context=tools_context,
    )


class _BindModel(BaseChatModel):
    """Chat model that records what ``bind_tools`` receives."""

    bound: Optional[List[Any]] = None

    @property
    def _llm_type(self) -> str:
        return "bind-recorder"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])

    def bind_tools(self, tools: Any, **kwargs: Any):
        return self.model_copy(update={"bound": list(tools)})


@pytest.fixture(autouse=True)
def _pin_bind_tools_cap():
    # As in test_bind_tools_safe_fallback.py: keep an ambient DYNACONF cap out of these tests.
    with patch(
        "cuga.backend.cuga_graph.nodes.cuga_lite.helpers.bind_tools.bind_tools_max_count_from_settings",
        return_value=128,
    ):
        yield


# ── The alias ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["a", "x" * 64, "get-weather", "A1_b-2"])
def test_legal_names_are_unchanged(name):
    assert provider_safe_tool_name(name) == name


def test_long_name_gets_a_deterministic_64_char_alias():
    name = "app_" + "b" * 61  # 65 characters
    expected = f"{name[:55]}_{hashlib.sha1(name.encode()).hexdigest()[:8]}"
    assert provider_safe_tool_name(name) == expected
    assert len(expected) == 64 and PROVIDER_TOOL_NAME_RE.fullmatch(expected)


def test_names_sharing_a_long_prefix_get_distinct_aliases():
    # Truncation alone would merge each pair, and the model would call the wrong tool.
    prefix = "geo_get_percentage_of_countries_grouped_by_population_band_and_by_"
    region, continent = prefix + "region", prefix + "continent"
    assert region[:64] == continent[:64]
    assert provider_safe_tool_name(region) != provider_safe_tool_name(continent)

    alpha, beta = "x" * 57 + "_alpha_tool", "x" * 57 + "_beta_tool"
    assert provider_safe_tool_name(alpha) != provider_safe_tool_name(beta)


def test_illegal_characters_get_an_alias_of_the_original_name():
    assert (
        provider_safe_tool_name("my.tool:v2") == "my_tool_v2_" + hashlib.sha1(b"my.tool:v2").hexdigest()[:8]
    )
    # The digest is of the original name, so "a.b" does not collapse into the legal "a_b".
    assert provider_safe_tool_name("a_b") == "a_b"
    assert provider_safe_tool_name("a.b") != "a_b"


@pytest.mark.parametrize("name", [LONG, "my.tool:v2", "", "x" * 64])
def test_alias_is_idempotent(name):
    alias = provider_safe_tool_name(name)
    assert provider_safe_tool_name(alias) == alias


def test_lone_surrogate_in_a_name_does_not_break_aliasing():
    # A JSON "\ud800" escape can put one in an MCP tool name.
    name = "tool_\ud800_name"
    alias = provider_safe_tool_name(name)
    assert PROVIDER_TOOL_NAME_RE.fullmatch(alias)
    assert resolve_tool_names(alias, {name: _reconcile}) == name


# ── Binding ────────────────────────────────────────────────────────────────


def test_safe_bind_binds_an_aliased_copy_and_leaves_the_tool_alone():
    tool = _tool(LONG, metadata={"owner": "acme"})
    [bound] = _safe_bind(_BindModel(), [tool]).bound

    assert bound.name == ALIAS
    assert bound.description == tool.description
    assert bound.args_schema is tool.args_schema
    assert bound.coroutine is tool.coroutine
    assert bound.metadata == {"owner": "acme"}
    assert tool.name == LONG


def test_provider_request_carries_only_legal_names():
    from langchain_openai import ChatOpenAI

    model = ChatOpenAI(model="gpt-4o-mini", api_key="test-key")  # nothing is sent
    bound = _safe_bind(model, [_tool(LONG), _tool("get_weather")])

    names = [tool["function"]["name"] for tool in bound.kwargs["tools"]]
    assert names == [ALIAS, "get_weather"]
    assert all(PROVIDER_TOOL_NAME_RE.fullmatch(name) for name in names)


def test_legal_names_are_bound_as_is_and_not_logged():
    tools = [_tool("get_weather"), _tool("get-forecast")]
    logs: list[str] = []
    handler_id = logger.add(lambda message: logs.append(str(message)), level="INFO")
    try:
        bound = _safe_bind(_BindModel(), tools).bound
    finally:
        logger.remove(handler_id)

    assert provider_safe_tools(tools) is tools
    assert all(b is t for b, t in zip(bound, tools, strict=True))
    assert not [line for line in logs if "provider-safe" in line]


def test_each_alias_is_logged_once_per_process(monkeypatch):
    monkeypatch.setattr(tool_names, "_logged_aliases", set())
    logs: list[str] = []
    handler_id = logger.add(lambda message: logs.append(str(message)), level="INFO")
    try:
        for _ in range(3):
            _safe_bind(_BindModel(), [_tool(LONG)])
    finally:
        logger.remove(handler_id)

    assert len([line for line in logs if ALIAS in line]) == 1


@pytest.mark.asyncio
async def test_two_tools_bound_under_one_name_fail_loudly():
    tools = [_tool(LONG), _tool(ALIAS)]  # the alias of one is the real name of the other
    with pytest.raises(RuntimeError, match="would both be bound") as excinfo:
        _safe_bind(_BindModel(), tools)
    assert LONG in str(excinfo.value) and ALIAS in str(excinfo.value)

    provider = AsyncMock()
    provider.get_all_tools = AsyncMock(return_value=tools)
    with pytest.raises(RuntimeError, match="would both be bound"):  # not degraded to the unbound model
        await resolve_model_with_bind_tools(
            _BindModel(),
            configurable={
                "cuga_lite_bind_tools_mode": "tools",
                "cuga_lite_bind_tools_tool_names": [LONG, ALIAS],
            },
            tools_context_ref={},
            tool_provider=provider,
        )


# ── Mapping aliases back ───────────────────────────────────────────────────


def test_resolve_tool_names_maps_every_alias_back_and_nothing_else():
    ctx = {LONG: _reconcile, "get_weather": _reconcile}
    assert resolve_tool_names(ALIAS, ctx) == LONG
    assert resolve_tool_names("get_weather", ctx) == "get_weather"
    assert resolve_tool_names("", ctx) == ""

    # Only whole alias tokens change: not a longer token, the real name, or an unknown alias shape.
    text = f"r = await {ALIAS}(value=7)  # not {ALIAS}x, {LONG} or deadbeef_0123abcd"
    assert resolve_tool_names(text, ctx) == text.replace(f"await {ALIAS}(", f"await {LONG}(")


def test_resolve_tool_names_refuses_ambiguous_aliases(monkeypatch):
    with pytest.raises(RuntimeError, match="ambiguous"):
        resolve_tool_names(ALIAS, {LONG: _reconcile, ALIAS: _reconcile})

    monkeypatch.setattr(tool_names, "provider_safe_tool_name", lambda name: "shared_0123abcd")
    with pytest.raises(RuntimeError, match="ambiguous"):
        resolve_tool_names("shared_0123abcd", {"tool_one": _reconcile, "tool_two": _reconcile})


# ── The decode boundary ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "reply",
    [
        _tool_call(ALIAS),
        SimpleNamespace(  # raw OpenAI shape
            content="",
            tool_calls=None,
            additional_kwargs={
                "tool_calls": [{"id": "call_1", "function": {"name": ALIAS, "arguments": '{"value": 7}'}}]
            },
        ),
        AIMessage(content=_code(ALIAS)),  # the model wrote the alias into its own code
    ],
    ids=["tool_call", "raw_tool_call", "code_in_reply"],
)
def test_reply_naming_the_alias_decodes_to_the_real_tool(reply):
    content, _ = _adapter({LONG: _reconcile}).normalize_response(reply)
    assert f"await {LONG}(value=7)" in content
    assert ALIAS not in content


def test_reasoning_naming_the_alias_decodes_to_the_real_tool():
    reply = AIMessage(content="Done.", additional_kwargs={"reasoning_content": f"Call {ALIAS} next."})
    _, reasoning = _adapter({LONG: _reconcile}).normalize_response(reply)
    assert reasoning == f"Call {LONG} next."


@pytest.mark.asyncio
async def test_tool_use_failed_recovery_decodes_to_the_real_tool():
    error = Exception(
        "Error code: 400 - {'error': {'message': 'Failed to call a function. tool_use_failed', "
        f"'failed_generation': '{{\"name\": \"{ALIAS}\", \"arguments\": {{\"value\": 7}}}}'}}}}"
    )

    class _Rejecting:
        async def ainvoke(self, messages, config=None):
            raise error

    adapter = _adapter({LONG: _reconcile})
    content, _ = adapter.normalize_response(await adapter.ainvoke_model(_Rejecting(), [], {}))
    assert f"await {LONG}(value=7)" in content
    assert ALIAS not in content


@pytest.mark.parametrize(
    "reply", [_tool_call(ALIAS), AIMessage(content=_code(ALIAS))], ids=["tool_call", "code"]
)
@pytest.mark.asyncio
async def test_approval_policy_on_the_real_name_stops_a_call_made_under_the_alias(reply):
    """The call runs as the real tool, so a ToolApproval on the real name must still stop it.

    ToolApproval matches real names in the code text: code naming the alias would not match.
    """
    adapter = _adapter({LONG: _reconcile})
    content, reasoning = adapter.normalize_response(reply)
    code = extract_code_from_model_response(content, reasoning)

    policy = ToolApproval(
        id="approve-reconcile",
        name="Reconcile needs approval",
        description="Approval gate.",
        required_tools=[LONG],
    )
    policy_agent = PolicyAgent.__new__(PolicyAgent)
    policy_agent.storage = SimpleNamespace(list_policies=AsyncMock(return_value=[policy]))
    state = SimpleNamespace(
        chat_messages=[HumanMessage(content="Reconcile.")], step_count=0, cuga_lite_metadata={}
    )

    with (
        patch.object(PolicyConfigurable, "from_config", return_value=SimpleNamespace(agent=policy_agent)),
        patch.object(
            PolicyConfigurable,
            "create_context_from_state",
            return_value=SimpleNamespace(user_input="Reconcile."),
        ),
    ):
        command = await ToolApprovalHandler.check_and_create_approval_interrupt(
            adapter, state, code, content, {}
        )

    # None means "no policy matched", or any error on the way: the handler fails open (#880).
    assert command is not None
    assert command.update["hitl_action"] is not None
    assert command.update["script"] == code


# ── End to end ─────────────────────────────────────────────────────────────


class _ProviderLikeModel:
    """Scripted model that, like a provider, only knows the names it was bound with.

    It calls the tool for a request and answers once it sees the tool's output,
    so an extra model call cannot put it out of step.
    """

    def __init__(self, reply_with: str):
        self.reply_with = reply_with
        self.bound_names: list[list[str]] = []

    def bind_tools(self, tools, **kwargs):
        self.bound_names.append([tool.name for tool in tools])
        return self

    async def ainvoke(self, messages, config=None, **kwargs):
        last = messages[-1]
        text = last.get("content") if isinstance(last, dict) else getattr(last, "content", "")
        if str(text).startswith(EXECUTION_OUTPUT_PREFIX):
            return AIMessage(content="Reconciled.")
        name = self.bound_names[-1][0]
        return AIMessage(content=_code(name)) if self.reply_with == "code" else _tool_call(name)


@pytest.fixture
def _reset_budgets():
    yield
    tracker_module._tool_call_budget_context.set(None)
    tracker_module._thread_tool_call_budget_context.set(None)
    tracker_module._block_tool_call_budget_context.set(None)


@pytest.mark.parametrize("reply_with", ["tool_call", "code"])
@pytest.mark.asyncio
async def test_call_under_the_alias_runs_the_real_tool_on_every_run(monkeypatch, _reset_budgets, reply_with):
    from cuga.config import settings

    monkeypatch.setattr(settings.policy, "enabled", False, raising=False)
    monkeypatch.setattr(settings.advanced_features, "cuga_lite_nl_auto_continue", False, raising=False)
    adapters: list[AgentGraphAdapter] = []

    class _RecordingAdapter(cuga_lite_graph.AgentGraphAdapter):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            adapters.append(self)

    monkeypatch.setattr(cuga_lite_graph, "AgentGraphAdapter", _RecordingAdapter)

    executed: list[int] = []

    async def reconcile(value: int) -> dict:
        executed.append(value)
        return {"reconciled": value}

    tool = StructuredTool(
        name=LONG, description="Reconcile stock levels.", args_schema=_Args, coroutine=reconcile
    )
    model = _ProviderLikeModel(reply_with)
    graph = cuga_lite_graph.create_cuga_lite_graph(
        model=model,
        tool_provider=DirectLangChainToolsProvider(tools=[tool], app_name="acme"),
        apps_list=[],
        thread_id="alias-e2e",
    ).compile(checkpointer=MemorySaver())
    config = {
        "configurable": {
            "thread_id": "alias-e2e",
            "enable_todos": False,
            "track_tool_calls": True,
            "reflection_enabled": False,
            "pre_execute_verify_enabled": False,
            "cuga_lite_bind_tools_mode": "tools",
            "cuga_lite_bind_tools_tool_names": [LONG],
        }
    }

    # The execution context outlives a run, so run the same graph twice.
    for run in (1, 2):
        result = await graph.ainvoke(
            cuga_lite_graph.CugaLiteState(chat_messages=[HumanMessage(content="Reconcile the stock.")]),
            config=config,
        )
        assert executed == [7] * run
        assert result["final_answer"] == "Reconciled."
        assert [call["name"] for call in result["tool_calls"]] == [LONG]
        transcript = " ".join(str(message.content) for message in result["chat_messages"])
        assert f"await {LONG}(value=7)" in transcript and ALIAS not in transcript

    # The alias reached the provider and nothing else.
    assert model.bound_names and all(names == [ALIAS] for names in model.bound_names)
    assert LONG in adapters[0]._tools_context and ALIAS not in adapters[0]._tools_context
