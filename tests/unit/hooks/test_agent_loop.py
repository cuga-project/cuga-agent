"""Hooks in the real CugaAgent loop (SDK, CodeAct), driven by a scripted chat model: no network, no real model.

Covers what the other hook tests do not: ``CugaAgent._invoke_with_hooks`` (session_start, on_user_message,
the before_reply send-back, on_task_end, ``result.hook_events``), ``after_plan`` blocking a code block before it
runs, and ``after_llm_call`` through ``HooksCallback``.
"""

from __future__ import annotations

from typing import Any, List, Optional

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool

from cuga.backend.hooks import AgentHooks, HookResult
from cuga.sdk import CugaAgent

pytestmark = pytest.mark.unit

MARKER = "SCRIPTED-HOOK-MARKER"
SEND_BACK = "Send-back: check again before you answer."

TOUCHED: List[str] = []


@tool
async def touch(name: str) -> str:
    """Record a name and return it."""
    TOUCHED.append(name)
    return f"touched {name}"


class ScriptedModel(BaseChatModel):
    """Replies with the next script entry on each agent-loop call.

    An entry that starts with ``ANSWER:`` is a plain-text final answer; any other entry is a code block. Calls
    without the marker in the system prompt are helper calls (for example a summarizer) and use no script entry.
    """

    script: List[str]
    step: int = 0
    seen: List[str] = []  # the last message the model received on each agent-loop call

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedModel":
        return self

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        system = next((m.content for m in messages if isinstance(m, SystemMessage)), "")
        if MARKER not in str(system):
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="helper reply"))])
        self.seen.append(str(messages[-1].content) if messages else "")
        text = self.script[min(self.step, len(self.script) - 1)]
        self.step += 1
        content = text[len("ANSWER:") :] if text.startswith("ANSWER:") else f"```python\n{text}\n```"
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content))])


class RecordingHooks(AgentHooks):
    """Blocks code blocks with two touch calls; sends the reply back ``send_backs`` times; records what it saw."""

    def __init__(self, send_backs: int):
        self.send_backs = send_backs
        self.plans: List[str] = []
        self.llm_texts: List[str] = []
        self.replies: List[str] = []
        self.ended: List[str] = []

    def before_llm_call(self, thread_id):
        return MARKER

    def after_llm_call(self, text):
        self.llm_texts.append(text)

    def after_plan(self, code):
        self.plans.append(code)
        if code.count("touch(") > 1:
            return "One touch per code block."
        return None

    def before_reply(self, answer):
        self.replies.append(answer)
        if len(self.replies) <= self.send_backs:
            return SEND_BACK
        return None

    def on_task_end(self, answer):
        self.ended.append(answer)


def _run(monkeypatch, script: List[str], hooks: RecordingHooks):
    monkeypatch.setenv("OPENAI_API_KEY", "scripted-run-no-calls")  # helper clients are built, never called
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:9/unused")
    TOUCHED.clear()
    model = ScriptedModel(script=script, seen=[])
    agent = CugaAgent(
        tools=[touch],
        model=model,
        hooks=[hooks],
        auto_load_policies=False,
        filesystem_sync=False,
    )
    return model, agent


TWO_TOUCHES = 'await touch(name="a")\nawait touch(name="b")'
ONE_TOUCH = 'print(await touch(name="a"))'


async def test_after_plan_blocks_a_code_block_before_it_runs(monkeypatch):
    hooks = RecordingHooks(send_backs=0)
    model, agent = _run(monkeypatch, [TWO_TOUCHES, ONE_TOUCH, "ANSWER:done"], hooks)

    result = await agent.invoke("Touch a.")

    assert hooks.plans[0].count("touch(") == 2 and hooks.plans[1].count("touch(") == 1
    assert TOUCHED == ["a"]  # the blocked block (a, b) never ran; the corrected block ran once
    assert any("One touch per code block." in seen for seen in model.seen)  # the model was told why
    assert result.answer == "done"
    blocks = [e for e in result.hook_events if e["hook"] == "after_plan" and e["outcome"] == "block"]
    assert len(blocks) == 1


async def test_after_llm_call_sees_each_model_output(monkeypatch):
    hooks = RecordingHooks(send_backs=0)
    _, agent = _run(monkeypatch, [ONE_TOUCH, "ANSWER:done"], hooks)

    await agent.invoke("Touch a.")

    assert any("touch(" in t for t in hooks.llm_texts)  # the code block the model wrote
    assert "done" in hooks.llm_texts  # the final answer


async def test_before_reply_sends_the_agent_back_once_then_accepts(monkeypatch):
    hooks = RecordingHooks(send_backs=1)
    model, agent = _run(monkeypatch, ["ANSWER:first", "ANSWER:second"], hooks)

    result = await agent.invoke("Say something.")

    assert hooks.replies == ["first", "second"]
    assert SEND_BACK in model.seen[1]  # the send-back message is the next user turn of the same thread
    assert result.answer == "second"
    assert hooks.ended == ["second"]
    assert [e["outcome"] for e in result.hook_events if e["hook"] == "before_reply"] == [
        "followup",
        "observe",
    ]


async def test_before_reply_send_back_stops_after_two_rounds(monkeypatch):
    hooks = RecordingHooks(send_backs=99)  # always asks for more
    model, agent = _run(monkeypatch, ["ANSWER:one", "ANSWER:two", "ANSWER:three", "ANSWER:four"], hooks)

    result = await agent.invoke("Say something.")

    assert model.step == 3  # the first run and two send-backs; no fourth run
    assert hooks.replies == ["one", "two", "three"]
    assert result.answer == "three"  # the third answer is returned although the hook still wanted more
    assert hooks.ended == ["three"]
    followups = [e for e in result.hook_events if e["hook"] == "before_reply" and e["outcome"] == "followup"]
    assert len(followups) == 3  # the hook was asked for each answer; the last request was not acted on


async def test_hook_events_cover_the_whole_lifecycle(monkeypatch):
    hooks = RecordingHooks(send_backs=0)
    _, agent = _run(monkeypatch, [ONE_TOUCH, "ANSWER:done"], hooks)

    result = await agent.invoke("Touch a.", thread_id="t-1")

    assert result.hook_events, "hook_events must be filled"
    assert {e["hook"] for e in result.hook_events} >= {
        "session_start",
        "on_user_message",
        "before_llm_call",
        "after_llm_call",
        "after_plan",
        "before_tool_call",
        "after_tool_call",
        "before_reply",
        "on_task_end",
    }
    assert any(e["hook"] == "before_llm_call" and e["outcome"] == "inject" for e in result.hook_events)
    order = [e["hook"] for e in result.hook_events]
    assert order[0] == "session_start" and order[-1] == "on_task_end"
    assert agent.hooks.run_events() == []  # the registry keeps no events after the invocation


def test_scripted_model_needs_the_marker_to_use_a_script_entry():
    model = ScriptedModel(script=["ANSWER:x"], seen=[])
    out = model.invoke([SystemMessage(content="no marker here")])
    assert out.content == "helper reply" and model.step == 0


class CountingHooks(AgentHooks):
    def __init__(self):
        self.sessions: List[Optional[str]] = []
        self.messages: List[str] = []

    def before_llm_call(self, thread_id):
        return MARKER

    def session_start(self, thread_id):
        self.sessions.append(thread_id)

    def on_user_message(self, message, thread_id):
        self.messages.append(message)


async def test_session_start_fires_once_per_thread(monkeypatch):
    hooks = CountingHooks()
    _, agent = _run(monkeypatch, ["ANSWER:done"], hooks)

    for thread_id in ("t-a", "t-b", "t-a"):
        await agent.invoke("Say something.", thread_id=thread_id)
    first = await agent.invoke("Say something.")  # invoke() creates the thread id
    await agent.invoke("Again.", thread_id=first.thread_id)

    assert hooks.sessions == ["t-a", "t-b", None]


async def test_concurrent_invocations_each_run_the_hooks(monkeypatch):
    import asyncio

    hooks = CountingHooks()
    _, agent = _run(monkeypatch, ["ANSWER:done"], hooks)

    a, b = await asyncio.gather(
        agent.invoke("Message A.", thread_id="t-a"), agent.invoke("Message B.", thread_id="t-b")
    )

    assert sorted(hooks.messages) == ["Message A.", "Message B."]
    for result, thread_id in ((a, "t-a"), (b, "t-b")):
        assert result.hook_events and {e["thread_id"] for e in result.hook_events} == {thread_id}
        assert [e["hook"] for e in result.hook_events].count("on_user_message") == 1


class _FakeAgent:
    """Just the parts of CugaAgent that ``_invoke_with_hooks`` uses; invoke() records its calls."""

    _invoke_with_hooks = CugaAgent._invoke_with_hooks

    def __init__(self, hooks: AgentHooks, pending: List[bool]):
        from cuga.backend.hooks import HookRegistry

        self.hooks = HookRegistry([hooks])
        self.pending = pending
        self.calls: List[tuple] = []

    async def invoke(self, message, **kwargs):
        import inspect

        from cuga.sdk import InvokeResult

        # the arguments must fit the real invoke(): a renamed parameter fails here
        bound = inspect.signature(CugaAgent.invoke).bind(self, message, **kwargs)
        self.calls.append((message, bound.arguments["action_response"]))
        return InvokeResult(answer=f"answer {len(self.calls)}", thread_id="t-1")

    async def _approval_pending(self, thread_id):
        return self.pending.pop(0) if self.pending else False

    async def _thread_snapshot(self, thread_id):
        return None, []


def test_invoke_with_hooks_declares_the_arguments_of_invoke():
    import inspect

    def names(fn):
        return {p for p in inspect.signature(fn).parameters if p not in ("self", "message")}

    assert names(CugaAgent._invoke_with_hooks) == names(CugaAgent.invoke)
    assert not any(
        p.kind is inspect.Parameter.VAR_KEYWORD
        for p in inspect.signature(CugaAgent._invoke_with_hooks).parameters.values()
    )


def _kwargs(**overrides):
    base = dict(
        thread_id="t-1",
        config=None,
        action_response=None,
        user_context=None,
        track_tool_calls=False,
        variables=None,
        shortlister=None,
        execution_mode=None,
        step_discipline=None,
    )
    return {**base, **overrides}


async def test_a_run_paused_for_approval_gets_no_reply_hooks():
    hooks = RecordingHooks(send_backs=1)
    agent = _FakeAgent(hooks, pending=[True])

    result = await agent._invoke_with_hooks("Delete the record.", **_kwargs())

    assert agent.calls == [("Delete the record.", None)]  # no send-back into the paused thread
    assert hooks.replies == [] and hooks.ended == []
    assert {e["hook"] for e in result.hook_events} == {"session_start", "on_user_message"}


async def test_a_send_back_after_a_resume_is_a_new_turn():
    hooks = RecordingHooks(send_backs=1)
    agent = _FakeAgent(hooks, pending=[False, False])

    result = await agent._invoke_with_hooks(None, **_kwargs(action_response="approved"))

    assert agent.calls == [(None, "approved"), (SEND_BACK, None)]
    assert hooks.replies == ["answer 1", "answer 2"] and hooks.ended == ["answer 2"]
    assert result.answer == "answer 2"


class RewriteHooks(AgentHooks):
    """Replaces every reply, like a redactor."""

    def before_llm_call(self, thread_id):
        return MARKER

    def before_reply(self, answer):
        return HookResult(answer="REDACTED-REPLY-4b8")


class FullHistoryModel(ScriptedModel):
    """Also records every message text of each agent-loop call."""

    history: List[List[str]] = []

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        system = next((m.content for m in messages if isinstance(m, SystemMessage)), "")
        if MARKER in str(system):
            self.history.append([str(m.content) for m in messages])
        return super()._generate(messages, stop, run_manager, **kwargs)


async def test_a_rewritten_reply_is_the_saved_answer_for_the_next_turn(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "scripted-run-no-calls")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:9/unused")
    model = FullHistoryModel(script=["ANSWER:ORIGINAL-SECRET-91f", "ANSWER:second"], seen=[], history=[])
    agent = CugaAgent(
        tools=[touch], model=model, hooks=[RewriteHooks()], auto_load_policies=False, filesystem_sync=False
    )

    first = await agent.invoke("First question.")
    assert first.answer == "REDACTED-REPLY-4b8"
    saved = (await agent.graph.aget_state({"configurable": {"thread_id": first.thread_id}})).values
    assert "ORIGINAL-SECRET-91f" not in str(
        saved
    )  # final_answer, chat_messages and messages all hold the rewrite
    assert saved["final_answer"] == "REDACTED-REPLY-4b8"

    await agent.invoke("Repeat your last answer.", thread_id=first.thread_id)
    turn_two = model.history[-1]
    assert "REDACTED-REPLY-4b8" in turn_two
    assert not any("ORIGINAL-SECRET-91f" in text for text in turn_two)


@pytest.mark.parametrize(
    "raw_answer, final_answer",
    [
        ("ORIGINAL-SECRET-91f", lambda text: text + " (formatted)"),  # CugaAgent(final_answer=fn)
        ("ORIGINAL-SECRET-91f [s1]", None),  # citation markers are resolved (or stripped) after the chat copy
    ],
    ids=["final-answer-function", "citation-markers"],
)
async def test_a_rewrite_is_saved_when_the_answer_changed_after_the_chat_copy(
    monkeypatch, raw_answer, final_answer
):
    monkeypatch.setenv("OPENAI_API_KEY", "scripted-run-no-calls")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:9/unused")
    model = FullHistoryModel(script=[f"ANSWER:{raw_answer}", "ANSWER:second"], seen=[], history=[])
    agent = CugaAgent(
        tools=[touch],
        model=model,
        hooks=[RewriteHooks()],
        auto_load_policies=False,
        filesystem_sync=False,
        final_answer=final_answer,
    )

    first = await agent.invoke("First question.")
    saved = (await agent.graph.aget_state({"configurable": {"thread_id": first.thread_id}})).values
    assert first.answer == saved["final_answer"] == "REDACTED-REPLY-4b8"
    assert "ORIGINAL-SECRET-91f" not in str(saved)

    await agent.invoke("Repeat your last answer.", thread_id=first.thread_id)
    turn_two = model.history[-1]
    assert "REDACTED-REPLY-4b8" in turn_two
    assert not any("ORIGINAL-SECRET-91f" in text for text in turn_two)


def _content(message):
    return message["content"] if isinstance(message, dict) else message.content


async def test_a_rewrite_replaces_only_what_the_run_saved(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "scripted-run-no-calls")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:9/unused")
    model = FullHistoryModel(script=["ANSWER:first"], seen=[], history=[])
    agent = CugaAgent(
        tools=[touch], model=model, hooks=[RewriteHooks()], auto_load_policies=False, filesystem_sync=False
    )
    first = await agent.invoke("First question.")
    config = {"configurable": {"thread_id": first.thread_id}}

    async def saved():
        return (await agent.graph.aget_state(config)).values

    # nothing was saved since the snapshot (the graph did not run): the earlier turn's answer stays
    before = await agent._thread_snapshot(first.thread_id)
    await agent._save_rewritten_answer(first.thread_id, "not run", "NOT-SAVED-1c4", before)
    assert "NOT-SAVED-1c4" not in str(await saved())

    # the run saved an answer but added no chat message (a denied approval ends like this):
    # final_answer is replaced, the earlier reply in chat_messages is not
    await agent.graph.aupdate_state(config, {"final_answer": "Execution Cancelled"})
    await agent._save_rewritten_answer(first.thread_id, "Execution Cancelled", "CANCEL-REWRITE-6a0", before)
    values = await saved()
    assert values["final_answer"] == "CANCEL-REWRITE-6a0"
    assert _content(values["chat_messages"][-1]) == "REDACTED-REPLY-4b8"


class GuardHooks(AgentHooks):
    """Answers a forbidden message itself, like an intent guard."""

    def before_llm_call(self, thread_id):
        return MARKER

    def on_user_message(self, message, thread_id):
        return HookResult(answer="GUARD-ANSWER-3d1") if "forbidden" in message else None


async def test_a_stream_the_caller_leaves_early_does_not_turn_off_the_invoke_hooks(monkeypatch):
    hooks = GuardHooks()
    model, agent = _run(monkeypatch, ["ANSWER:the agent ran"], hooks)

    stream = agent.stream("Say something.")
    try:
        async for _ in stream:
            assert not agent.hooks.in_run()  # the caller is not inside the stream's run
            break  # stop reading; the stream stays open

        assert not agent.hooks.in_run()
        result = await agent.invoke("A forbidden thing.")
        assert result.answer == "GUARD-ANSWER-3d1"  # the guard still answers: the agent did not run
        assert [e["hook"] for e in result.hook_events] == ["session_start", "on_user_message", "on_task_end"]
    finally:
        await stream.aclose()


def _direct_provider(tools):
    from cuga.backend.cuga_graph.nodes.cuga_lite.providers.langchain import DirectLangChainToolsProvider

    return DirectLangChainToolsProvider(tools=tools, app_name="kv")


def _caller_toolguard_provider(tools):
    from cuga.backend.cuga_graph.nodes.cuga_lite.providers.toolguard import ToolGuardingToolProvider

    return ToolGuardingToolProvider(_direct_provider(tools), enabled=False)


@pytest.mark.parametrize(
    "setup",
    ["tools", "direct-provider", "caller-toolguard-provider", "add-tool-to-direct-provider"],
)
async def test_a_tool_call_is_tracked_once_whatever_the_provider_layers(monkeypatch, setup):
    """The prepare node and the hooks wrapper split the recording between them; a layering that both or neither
    record shows up here as two records or none."""
    monkeypatch.setenv("OPENAI_API_KEY", "scripted-run-no-calls")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:9/unused")
    TOUCHED.clear()
    kwargs = {
        "tools": {"tools": [touch]},
        "direct-provider": {"tool_provider": _direct_provider([touch])},
        "caller-toolguard-provider": {"tool_provider": _caller_toolguard_provider([touch])},
        "add-tool-to-direct-provider": {"tool_provider": _direct_provider([])},
    }[setup]
    agent = CugaAgent(
        model=ScriptedModel(script=[ONE_TOUCH, "ANSWER:done"], seen=[]),
        hooks=[RecordingHooks(send_backs=0)],
        auto_load_policies=False,
        filesystem_sync=False,
        **kwargs,
    )
    if setup == "add-tool-to-direct-provider":
        agent.add_tool(touch)

    result = await agent.invoke("Touch a.", track_tool_calls=True)

    assert TOUCHED == ["a"]
    assert [c["name"] for c in result.tool_calls] == ["touch"]
