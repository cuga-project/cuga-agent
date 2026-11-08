"""Tool-call hooks: positional arguments, and tools that come from a custom tool provider."""

import pytest
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from cuga.backend.cuga_graph.nodes.cuga_lite.providers.langchain import DirectLangChainToolsProvider
from cuga.backend.hooks import HookRegistry, wrap_provider_with_hooks, wrap_tools_with_hooks

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
    assert await wrapped.ainvoke({}) == [1, 2]
    assert "check the policy" in reg.before_llm_call()
