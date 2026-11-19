"""Tool-call hooks: positional arguments, and tools that come from a custom tool provider."""

from typing import Optional

import pytest
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from cuga.backend.cuga_graph.nodes.cuga_lite.providers.langchain import DirectLangChainToolsProvider
from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import PromptUtils
from cuga.backend.hooks import HookBlocked, HookRegistry, wrap_provider_with_hooks, wrap_tools_with_hooks

pytestmark = pytest.mark.unit


class _Args(BaseModel):
    user_id: str = Field(...)
    verbose: bool = Field(False)


async def _get_user(user_id: str, verbose: bool = False):
    return {"user_id": user_id}


class _Recorder:
    def __init__(self):
        self.calls = []

    def before_tool_call(self, tool, args):
        self.calls.append((tool, dict(args)))
        return None


def _tool():
    return StructuredTool.from_function(
        coroutine=_get_user, name="get_user", description="Get a user.", args_schema=_Args
    )


@pytest.mark.asyncio
async def test_hooked_tool_accepts_positional_arguments():
    rec = _Recorder()
    (tool,) = wrap_tools_with_hooks([_tool()], HookRegistry([rec]))
    assert await tool.coroutine("USER_004") == {"user_id": "USER_004"}
    assert rec.calls == [("get_user", {"user_id": "USER_004"})]
    with pytest.raises(TypeError):
        await tool.coroutine("a", True, "extra")


@pytest.mark.asyncio
async def test_provider_tools_get_tool_call_hooks():
    rec = _Recorder()
    provider = wrap_provider_with_hooks(
        DirectLangChainToolsProvider([_tool()], app_name="itsm"), HookRegistry([rec])
    )
    await provider.initialize()
    assert provider.initialized is True
    assert [a.name for a in await provider.get_apps()] == ["itsm"]
    (tool,) = await provider.get_all_tools()
    assert (await provider.get_tools("itsm"))[0] is tool  # wrapped once, same object each time
    await tool.coroutine(user_id="USER_001")
    assert rec.calls == [("get_user", {"user_id": "USER_001"})]


class _Noter:
    def after_tool_call(self, tool, args, result):
        return "NOTE: check the policy before the next write."


@pytest.mark.asyncio
async def test_after_tool_call_note_is_attached_to_the_tool_result():
    reg = HookRegistry([_Noter()])
    text_tool = StructuredTool.from_function(coroutine=lambda: _text(), name="t", description="Text.")
    [wrapped_dict] = wrap_tools_with_hooks([_tool()], reg)
    [wrapped_text] = wrap_tools_with_hooks([text_tool], reg)
    out = await wrapped_dict.ainvoke({"user_id": "u1"})
    assert out == {"user_id": "u1", "hook_note": "NOTE: check the policy before the next write."}
    assert (await wrapped_text.ainvoke({})).endswith("\n\nNOTE: check the policy before the next write.")
    assert reg.before_llm_call() is None  # attached, so not repeated in the system prompt


async def _text():
    return "row 1"


@pytest.mark.asyncio
async def test_note_on_a_list_result_goes_to_the_next_model_call():
    async def rows():
        return [1, 2]

    reg = HookRegistry([_Noter()])
    [wrapped] = wrap_tools_with_hooks(
        [StructuredTool.from_function(coroutine=rows, name="rows", description="Rows.")], reg
    )
    with reg.run("t1"):
        assert await wrapped.ainvoke({}) == [1, 2]
        assert "check the policy" in reg.before_llm_call("t1")


@pytest.mark.asyncio
async def test_a_carried_note_stays_in_its_run():
    async def rows():
        return [1, 2]

    reg = HookRegistry([_Noter()])
    [wrapped] = wrap_tools_with_hooks(
        [StructuredTool.from_function(coroutine=rows, name="rows", description="Rows.")], reg
    )
    with reg.run("thread-A"):
        await wrapped.ainvoke({})
        with reg.run("thread-B"):  # another invocation, e.g. a concurrent stream
            assert reg.before_llm_call("thread-B") is None
        assert "check the policy" in reg.before_llm_call("thread-A")
    await wrapped.ainvoke({})  # outside any run the note has no run to wait in: it is dropped
    assert reg.before_llm_call("thread-D") is None  # not handed to whichever thread calls the model next
    with reg.run("thread-C"):
        assert reg.before_llm_call("thread-C") is None


@pytest.mark.asyncio
async def test_a_stream_keeps_its_run_across_steps_and_the_reader_is_never_inside_it():
    reg = HookRegistry([_Noter()])
    seen = []

    async def updates():
        seen.append((reg.in_run(), reg.thread_id))
        reg.carry_note("STREAM-NOTE-2e6")
        yield "step 1"
        seen.append((reg.in_run(), reg.thread_id))
        yield reg.before_llm_call("s-1")  # the note of step 1 is in the same run
        yield "step 3"

    stream = reg.run_stream(updates(), "s-1")
    got = []
    async for update in stream:
        assert not reg.in_run()  # the code that reads the stream is not inside its run
        got.append(update)
        if len(got) == 2:
            break  # stop reading early
    await stream.aclose()
    assert got == ["step 1", "STREAM-NOTE-2e6"]
    assert seen == [(True, "s-1"), (True, "s-1")]
    assert not reg.in_run() and reg.before_llm_call() is None  # nothing left behind for the reader


class _NullableArgs(BaseModel):
    parent: Optional[str] = Field(...)
    limit: Optional[int] = Field(5)


async def _list_items(parent: Optional[str], limit: Optional[int] = 5):
    return {"parent": parent, "limit": limit}


@pytest.mark.asyncio
async def test_explicit_none_arguments_reach_the_tool():
    rec = _Recorder()
    nullable = StructuredTool.from_function(
        coroutine=_list_items, name="list_items", description="List items.", args_schema=_NullableArgs
    )
    (tool,) = wrap_tools_with_hooks([nullable], HookRegistry([rec]))
    assert await tool.ainvoke({"parent": None}) == {"parent": None, "limit": 5}
    assert await tool.coroutine(parent="p", limit=None) == {"parent": "p", "limit": None}
    assert rec.calls == [("list_items", {"limit": 5}), ("list_items", {"parent": "p"})]


class _RaiseBlock:
    def before_tool_call(self, tool, args):
        raise HookBlocked("RAISED-BLOCK-7f3")


@pytest.mark.asyncio
async def test_a_block_raised_by_a_hook_is_an_event():
    reg = HookRegistry([_RaiseBlock()])
    seen = []
    reg.add_listener(seen.append)
    (tool,) = wrap_tools_with_hooks([_tool()], reg)
    with reg.run("t1"):
        with pytest.raises(HookBlocked, match="RAISED-BLOCK-7f3"):
            await tool.ainvoke({"user_id": "u1"})
        events = reg.run_events()
    assert [(e["hook"], e["outcome"], e["message"]) for e in events] == [
        ("before_tool_call", "block", "RAISED-BLOCK-7f3")
    ]
    assert [(e.hook, e.outcome) for e in seen] == [("before_tool_call", "block")]


class _AfterRecorder:
    def __init__(self):
        self.results = []

    def after_tool_call(self, tool, args, result):
        self.results.append(result)
        return "FAILURE-NOTE-91c"


@pytest.mark.asyncio
async def test_a_failed_tool_call_reaches_after_tool_call():
    async def broken(user_id: str):
        raise RuntimeError("backend down")

    rec = _AfterRecorder()
    reg = HookRegistry([rec])
    (tool,) = wrap_tools_with_hooks(
        [StructuredTool.from_function(coroutine=broken, name="broken", description="Broken.")], reg
    )
    with reg.run("t1"):
        with pytest.raises(RuntimeError, match="backend down"):  # the agent still gets the tool error
            await tool.ainvoke({"user_id": "u1"})
        assert [e["hook"] for e in reg.run_events()] == ["before_tool_call", "after_tool_call"]
        assert "FAILURE-NOTE-91c" in reg.before_llm_call("t1")
    assert len(rec.results) == 1 and isinstance(rec.results[0], RuntimeError)


@pytest.mark.asyncio
async def test_the_wrapper_keeps_response_schemas_and_parameter_constraints():
    def get_user_sync(user_id: str, verbose: bool = False):
        return {"user_id": user_id}

    get_user_sync._response_schemas = {
        "success": {"type": "object", "properties": {"id": {"type": "string"}}}
    }
    get_user_sync._param_constraints = {"user_id": "MARKER-CONSTRAINT-5d2"}
    original = StructuredTool.from_function(
        func=get_user_sync, coroutine=_get_user, name="get_user", description="Get a user.", args_schema=_Args
    )
    rec = _Recorder()
    (tool,) = wrap_tools_with_hooks([original], HookRegistry([rec]))
    assert tool.func._response_schemas == get_user_sync._response_schemas
    assert tool.func._param_constraints == get_user_sync._param_constraints
    assert PromptUtils.get_tool_docs(tool) == PromptUtils.get_tool_docs(original)
    assert tool.invoke({"user_id": "u2"}) == {"user_id": "u2"}  # a sync call runs the hooks too
    assert rec.calls == [
        ("get_user", {"user_id": "u2", "verbose": False})
    ]  # schema defaults filled, as in ainvoke


def test_the_wrapper_keeps_the_app_name_and_operation_id_of_a_registry_tool():
    from cuga.backend.cuga_graph.nodes.cuga_lite.providers.registry import create_tool_from_api_dict
    from cuga.backend.cuga_graph.nodes.cuga_lite.providers.toolguard import _METADATA_ATTRS
    from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.doc import app_name_for_tool

    original = create_tool_from_api_dict(
        "crm_get_account",
        {
            "description": "Get an account.",
            "parameters": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
            "operation_id": "getAccount",
        },
        app_name="crm",
    )
    (tool,) = wrap_tools_with_hooks([original], HookRegistry([_Recorder()]))
    # ToolGuard, tracking and the shortlister read any of these
    for target in (tool, tool.func, tool.coroutine):
        assert target._operation_id == "getAccount" and target._app_name == "crm"
    for attr in _METADATA_ATTRS:  # the same list ToolGuard keeps
        assert getattr(tool.func, attr) == getattr(original.func, attr)
    assert app_name_for_tool(tool) == "crm"
