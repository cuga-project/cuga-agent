"""Exercise tracking and callback ownership through the SDK's real prepare node."""

from threading import get_ident
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import StructuredTool

from cuga.backend.cuga_graph.nodes.cuga_lite.bind_tools.tool_names import provider_safe_tool_name
from cuga.backend.cuga_graph.nodes.cuga_lite.providers import combined, registry
from cuga.backend.cuga_graph.nodes.cuga_lite.providers.base import AppDefinition, ToolProviderInterface
from cuga.backend.cuga_graph.nodes.cuga_lite.providers.toolguard import ToolGuardingToolProvider
from cuga.backend.cuga_graph.nodes.cuga_lite.tracking.tracker import tracked_tool
from cuga.backend.observability import langfuse_sdk
from cuga.config import settings
from cuga.sdk import CugaAgent

pytestmark = pytest.mark.unit


class ScriptedModel:
    def __init__(self, first):
        self.responses = [first, AIMessage(content="Synthetic answer.")]
        self.seen = []
        self.bound_names = set()

    def bind_tools(self, tools, **kwargs):
        self.bound_names.update(tool.name for tool in tools)

        async def call(messages, config=None, **kwargs):
            return await self.ainvoke(messages, config=config, **kwargs)

        return SimpleNamespace(ainvoke=call)

    async def ainvoke(self, messages, config=None, **kwargs):
        self.seen.append(list(messages))
        return self.responses.pop(0)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["codeact", "function_calling"])
@pytest.mark.parametrize("owner", ["off", "sdk", "caller"])
@pytest.mark.parametrize("denied", [False, True])
@pytest.mark.parametrize("decorated", [False, True])
async def test_guarded_direct_tool_through_sdk_prepare(monkeypatch, mode, owner, denied, decorated):
    actual = await _exercise_direct(monkeypatch, mode, owner, denied, decorated)
    if owner != "off" and decorated:
        baseline = await _exercise_direct(monkeypatch, mode, "off", denied, decorated)
        assert actual == baseline


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["codeact", "function_calling"])
@pytest.mark.parametrize("guarded,track", [(False, True), (True, True), (True, False)])
async def test_sync_decorator_aliased_tool_through_sdk_prepare(monkeypatch, mode, guarded, track):
    await _exercise_direct(monkeypatch, mode, "sdk", False, True, sync=True, guarded=guarded, track=track)


async def _exercise_direct(
    monkeypatch, mode, owner, denied, decorated, *, sync=False, guarded=True, track=True
):
    from langfuse.langchain import CallbackHandler

    calls = []
    callback_events = []
    observations = []

    class CallerHandler(CallbackHandler):
        def __init__(self):
            pass

        @property
        def ignore_chain(self):
            return True

        def on_tool_start(self, *args, **kwargs):
            callback_events.append("start")

        def on_tool_end(self, *args, **kwargs):
            callback_events.append("end")

    async def lookup(value: int) -> int:
        """Look up a synthetic integer."""
        calls.append(value)
        return value + 1

    tool_name = "lookup_synthetic_value_by_numeric_identifier_and_include_test_metadata"
    alias = provider_safe_tool_name(tool_name)
    assert alias != tool_name

    def sync_lookup(value: int) -> int:
        calls.append(value)
        return value + 1

    implementation = sync_lookup if sync else lookup
    delegate = tracked_tool(implementation) if decorated else implementation
    tool = StructuredTool.from_function(
        **({"func": delegate} if sync else {"coroutine": delegate}),
        name=tool_name,
        description=lookup.__doc__,
    )
    if mode == "function_calling":
        first = AIMessage(content="", tool_calls=[{"name": alias, "args": {"value": 7}, "id": "c1"}])
    else:
        first = AIMessage(content=f"```python\nprint(await {tool_name}(value=7))\n```")
    model = ScriptedModel(first)

    monkeypatch.setattr(settings.policy, "enabled", False, raising=False)
    monkeypatch.setattr(settings.evolve, "enabled", False, raising=False)
    monkeypatch.setattr(langfuse_sdk, "enabled", lambda: owner != "off")

    # Stub the exporter at its boundary; preserve the real tracker and callback dispatch.
    loop_thread = get_ident()

    def record(*args, **kwargs):
        # Raw synchronous execution uses the plain executor, which does not
        # carry the active span/trace. LangChain's guarded executor does.
        if sync and not guarded and get_ident() != loop_thread:
            return
        if owner != "off" and not kwargs["langchain_tool"]:
            observations.append((args, kwargs))

    monkeypatch.setattr(langfuse_sdk, "record_tool_call", record)
    monkeypatch.setattr(langfuse_sdk, "_client", lambda: None)
    verdict = AsyncMock(return_value="synthetic denial" if denied else None)
    monkeypatch.setattr(
        ToolGuardingToolProvider,
        "_get_or_create_toolguard_runtime",
        AsyncMock(return_value=SimpleNamespace(guard_tool_call=verdict)),
    )

    agent = CugaAgent(tools=[tool], model=model, execution_mode=mode)
    agent.tool_provider.set_enabled(guarded)
    config = {"callbacks": [CallerHandler()]} if owner == "caller" else None
    result = await agent.invoke("Look up number seven.", config=config, track_tool_calls=track)

    assert result.answer == "Synthetic answer."
    assert verdict.await_count == int(guarded)
    assert calls == ([] if denied else [7])
    if track:
        assert len(result.tool_calls) == 1
        tracked = result.tool_calls[0]
        assert tracked["name"] == tool_name
        assert tracked["arguments"] == {"value": 7}
        assert tracked["succeeded"] is not denied
        if denied:
            assert tracked["result"]["blocked_by_policy"] is True
        else:
            assert tracked["result"] == 8

    else:
        assert result.tool_calls == []

    assert callback_events == (["start", "end"] if owner == "caller" and not denied else [])
    assert len(observations) == (1 if owner == "sdk" or (owner == "caller" and denied) else 0)
    if observations:
        args, fields = observations[0]
        assert args[0] == tool_name
        assert bool(fields["error"]) is denied
    if mode == "function_calling":
        assert alias in model.bound_names and tool_name not in model.bound_names
        reply = next(message for message in model.seen[-1] if isinstance(message, ToolMessage))
        assert (reply.tool_call_id, reply.name) == ("c1", alias)
        assert ("synthetic denial" in reply.content) if denied else reply.content == "8"

    return [
        [
            message if isinstance(message, dict) else message.model_dump(exclude={"id", "response_metadata"})
            for message in messages
        ]
        for messages in model.seen
    ], model.bound_names


class ProviderFixture(ToolProviderInterface):
    """Supply the real provider-created tools without a registry discovery request."""

    def __init__(self, tool):
        self.tool = tool

    async def initialize(self):
        pass

    async def get_apps(self):
        return [AppDefinition(name="shop", description="Synthetic lookup service.")]

    async def get_tools(self, app_name):
        return [self.tool] if app_name == "shop" else []

    async def get_all_tools(self):
        return [self.tool]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["codeact", "function_calling"])
@pytest.mark.parametrize("route", ["registry", "mcp"])
@pytest.mark.parametrize(
    "guarded,caller,failed",
    [
        (False, False, True),
        (True, False, False),
        (True, True, True),
    ],
)
async def test_provider_transport_result_keeps_sdk_tool_contract(
    monkeypatch, mode, route, guarded, caller, failed
):
    from langfuse.langchain import CallbackHandler

    observed = []
    callbacks = []
    transport_calls = []

    class CallerHandler(CallbackHandler):
        def __init__(self):
            pass

        @property
        def ignore_chain(self):
            return True

        def on_tool_start(self, *args, **kwargs):
            callbacks.append("start")

        def on_tool_end(self, output, **kwargs):
            callbacks.append(("end", output))

        def on_tool_error(self, error, **kwargs):
            callbacks.append(("error", error))

    definition = {
        "description": "Look up a synthetic integer.",
        "parameters": {"properties": {"value": {"type": "integer"}}, "required": ["value"]},
    }
    if route == "registry":

        async def post(_session, _host, payload, _timeout):
            transport_calls.append(payload["args"]["value"])
            return (503, "synthetic transport failure") if failed else (200, "8")

        monkeypatch.setattr(registry, "post_registry_call", post)
        tool = registry.create_tool_from_api_dict("lookup", definition, "shop")
    else:

        async def invoke(_app, _name, args):
            transport_calls.append(args["value"])
            if failed:
                raise ValueError("synthetic transport failure")
            return 8

        monkeypatch.setattr(combined.tracker, "invoke_tool", invoke)
        tool = combined.create_tool_from_tracker("lookup", definition, "shop")

    def record(*args, **kwargs):
        if not kwargs["langchain_tool"]:
            observed.append((args, kwargs))

    monkeypatch.setattr(settings.policy, "enabled", False, raising=False)
    monkeypatch.setattr(settings.evolve, "enabled", False, raising=False)
    monkeypatch.setattr(langfuse_sdk, "enabled", lambda: True)
    monkeypatch.setattr(langfuse_sdk, "record_tool_call", record)
    monkeypatch.setattr(langfuse_sdk, "_client", lambda: None)
    verdict = AsyncMock(return_value=None)
    monkeypatch.setattr(
        ToolGuardingToolProvider,
        "_get_or_create_toolguard_runtime",
        AsyncMock(return_value=SimpleNamespace(guard_tool_call=verdict)),
    )
    first = (
        AIMessage(content="", tool_calls=[{"name": "lookup", "args": {"value": 7}, "id": "c1"}])
        if mode == "function_calling"
        else AIMessage(content="```python\nprint(await lookup(value=7))\n```")
    )
    model = ScriptedModel(first)
    agent = CugaAgent(tool_provider=ProviderFixture(tool), model=model, execution_mode=mode)
    agent.tool_provider.set_enabled(guarded)
    config = {"callbacks": [CallerHandler()]} if caller else None
    result = await agent.invoke("Look up number seven.", config=config, track_tool_calls=True)

    assert result.answer == "Synthetic answer."
    assert transport_calls == [7]
    assert verdict.await_count == int(guarded)
    assert len(result.tool_calls) == 1
    tracked = result.tool_calls[0]
    assert tracked["name"] == "lookup"
    assert tracked["succeeded"] is not failed
    if failed:
        assert "synthetic transport failure" in tracked["error"]
    else:
        assert tracked["result"] == 8

    if caller:
        # A provider error is an ordinary return by design. Classifying that
        # payload as an error is up to the callback owner; don't emit a second span.
        assert len(callbacks) == 2 and callbacks[0] == "start"
        event, output = callbacks[1]
        assert event == "end"
        assert "synthetic transport failure" in output["error"]
        assert observed == []
    else:
        assert callbacks == []
        assert len(observed) == 1
        assert bool(observed[0][1]["error"]) is failed

    if mode == "function_calling":
        reply = next(message for message in model.seen[-1] if isinstance(message, ToolMessage))
        assert (reply.tool_call_id, reply.name) == ("c1", "lookup")
        assert ("synthetic transport failure" in reply.content) if failed else reply.content == "8"
