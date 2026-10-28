"""Configuration fingerprint and stale-interrupt decision table (#774)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Literal, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from typing_extensions import TypedDict

from cuga.backend.server import main as main_mod
from cuga.backend.server.config_fingerprint import (
    ConfigDecision,
    ConfigFingerprint,
    classify_config_change,
    compute_fingerprint,
)

pytestmark = pytest.mark.unit

OLD = compute_fingerprint({"tools": [{"name": "search"}]})
NEW = compute_fingerprint({"tools": [{"name": "search"}, {"name": "browser"}]})


def _scope(fp: Optional[ConfigFingerprint], **extra) -> dict:
    scope = dict(extra)
    if fp is not None:
        scope["config_fingerprint"] = fp.to_scope_dict()
    return scope


def _with_runtime(fp: ConfigFingerprint, runtime_ver: str) -> ConfigFingerprint:
    return ConfigFingerprint(agent_hash=fp.agent_hash, runtime_ver=runtime_ver)


# --- decision table -------------------------------------------------------------------------

OLD_RUNTIME = _with_runtime(OLD, "0.0.1")
NEW_RUNTIME = _with_runtime(OLD, "9.9.9")


@pytest.mark.parametrize(
    "scope, current, pending, expected_version, decision",
    [
        (_scope(OLD), NEW, False, None, ConfigDecision.NOTICE),
        (_scope(OLD_RUNTIME), NEW_RUNTIME, False, None, ConfigDecision.NONE),
        (_scope(OLD), NEW, True, None, ConfigDecision.CANCEL_PENDING),
        (_scope(OLD_RUNTIME), NEW_RUNTIME, True, None, ConfigDecision.CANCEL_PENDING),
        (_scope(OLD), OLD, False, None, ConfigDecision.NONE),
        (_scope(OLD), OLD, True, None, ConfigDecision.NONE),
        # Checkpoints from before fingerprints fall back to the published version.
        ({"published_config_version": "3"}, NEW, False, "4", ConfigDecision.NONE),
        ({"published_config_version": "3"}, NEW, True, "4", ConfigDecision.CANCEL_PENDING),
        ({"published_config_version": "3"}, NEW, True, "3", ConfigDecision.NONE),
        # So does a graph built without a fingerprint.
        (_scope(OLD, published_config_version="3"), None, True, "4", ConfigDecision.CANCEL_PENDING),
    ],
    ids=[
        "completed-agent-changed",
        "completed-runtime-only",
        "pending-agent-changed",
        "pending-runtime-only",
        "completed-unchanged",
        "pending-unchanged",
        "completed-legacy",
        "pending-legacy-moved",
        "pending-legacy-same",
        "pending-graph-without-fingerprint",
    ],
)
def test_decision_table(scope, current, pending, expected_version, decision):
    assert classify_config_change(scope, current, pending, expected_version) is decision


def test_agent_state_keeps_the_fingerprint_dict_in_service_scope():
    from cuga.backend.cuga_graph.state.agent_state import AgentState, default_state

    state = default_state(page=None, observation=None, goal="")
    state.service_scope["config_fingerprint"] = OLD.to_scope_dict()
    restored = AgentState(**state.model_dump())
    assert restored.service_scope["config_fingerprint"] == OLD.to_scope_dict()


# --- _cancel_pending_interrupt against a real LangGraph ------------------------------------


class _S(TypedDict, total=False):
    log: str
    hitl_action: Optional[str]


def _approval_graph(interrupt_after=None):
    """ask -> wait (dynamic interrupt) -> act: the shape of a tool-approval turn."""

    def ask(_s) -> Command[Literal["wait"]]:
        return Command(update={"log": "asked", "hitl_action": "approve?"}, goto="wait")

    def wait(_s) -> Command[Literal["act"]]:
        interrupt("approve?")
        return Command(update={"log": "approved"}, goto="act")

    def act(_s):
        return {"log": "acted"}

    builder = StateGraph(_S)
    builder.add_node("ask", ask)
    builder.add_node("wait", wait)
    builder.add_node("act", act)
    builder.add_edge(START, "ask")
    builder.add_edge("act", END)
    return builder.compile(checkpointer=MemorySaver(), interrupt_after=interrupt_after)


@pytest.mark.asyncio
@pytest.mark.parametrize("interrupt_after", [None, ["ask"]], ids=["dynamic", "static"])
async def test_cancel_pending_interrupt_clears_the_pause_and_keeps_history(interrupt_after):
    graph = _approval_graph(interrupt_after)
    config = {"configurable": {"thread_id": "t"}}
    await graph.ainvoke({"log": "start"}, config)
    assert (await graph.aget_state(config)).next == ("wait",)

    assert await main_mod._cancel_pending_interrupt(graph, "t") is True

    after = await graph.aget_state(config)
    assert after.next == ()
    assert after.values["log"] == "asked"  # history is untouched
    assert after.values["hitl_action"] is None  # the stale approval request is gone


@pytest.mark.asyncio
async def test_cancel_pending_interrupt_does_not_run_the_cancelled_action():
    graph = _approval_graph()
    config = {"configurable": {"thread_id": "t"}}
    await graph.ainvoke({"log": "start"}, config)

    await main_mod._cancel_pending_interrupt(graph, "t")

    assert (await graph.aget_state(config)).values["log"] != "acted"


# --- event_stream: notices ------------------------------------------------------------------


def _event_stream_agent(graph, fingerprint):
    return SimpleNamespace(
        graph=graph,
        _checkpoint_config_version="4",
        _config_fingerprint=fingerprint,
        policy_system=None,
        enable_todos=None,
        reflection_enabled=None,
        shortlisting_tool_threshold=None,
        cuga_lite_max_steps=None,
        enable_filesystem_tools=None,
        special_instructions=None,
        chat=None,
    )


async def _run_event_stream(
    monkeypatch,
    run_agent,
    *,
    saved: dict,
    scope_seen: Optional[dict] = None,
    stored_config: Optional[dict] = None,
    store_error: Optional[Exception] = None,
    refreshed_agent=None,
    stored_version: str = "4",
    graph_seen: Optional[list] = None,
):
    from cuga.backend.cuga_graph.utils.agent_loop import AgentLoopAnswer

    class _ImmediateLoop:
        def __init__(self, **_kwargs):
            if graph_seen is not None:
                graph_seen.append(_kwargs["graph"])

        def get_langfuse_trace_id(self):
            return None

        async def run_stream(self, **kwargs):
            if scope_seen is not None:
                scope_seen.update(kwargs["state"].service_scope)
            yield AgentLoopAnswer(end=True, answer="done", tools=[])

    async def fake_save(**kwargs):
        saved.update(kwargs)

    import cuga.backend.cuga_graph.utils.controller  # noqa: F401

    # The published config the turn lease re-reads: same version, same content as the graph's.
    monkeypatch.setattr(
        "cuga.backend.server.config_store.load_config",
        AsyncMock(side_effect=store_error)
        if store_error
        else AsyncMock(return_value=(stored_config if stored_config is not None else {}, stored_version)),
    )
    if stored_config is not None:
        replacement = refreshed_agent or _event_stream_agent(
            run_agent.graph, compute_fingerprint(stored_config)
        )
        monkeypatch.setattr(main_mod, "_refresh_published_graph", AsyncMock(return_value=replacement))
    monkeypatch.setattr("cuga.backend.cuga_graph.utils.agent_loop.AgentLoop", _ImmediateLoop)
    monkeypatch.setattr(main_mod, "_assert_run_thread_access", AsyncMock())
    monkeypatch.setattr(main_mod, "_save_conversation_and_events_async", fake_save)
    monkeypatch.setattr(main_mod, "_knowledge_enabled_for_app_state", lambda _state: False)
    monkeypatch.setattr(main_mod, "_rehydrate_citation_ledger", AsyncMock())
    monkeypatch.setattr(main_mod, "_dispatch_slash_for_stream", AsyncMock(return_value=None))
    monkeypatch.setattr(main_mod.app_state, "agent_id", "cuga-default")
    monkeypatch.setattr(main_mod.app_state, "current_llm", "default-llm")
    monkeypatch.setattr(main_mod.app_state, "stop_events", {})
    monkeypatch.setattr(main_mod.app_state, "output_format", None)
    monkeypatch.setattr(main_mod.app_state, "knowledge_provider", None)
    return [
        chunk
        async for chunk in main_mod.event_stream(
            "hello",
            api_mode=True,
            thread_id="thread-1",
            agent=run_agent,
            agent_id="sales-east",
            current_llm=None,
            issued_thread_id="thread-1",
        )
    ]


def _completed_checkpoint(fp: Optional[ConfigFingerprint]):
    from cuga.backend.cuga_graph.state.agent_state import default_state

    state = default_state(page=None, observation=None, goal="")
    if fp is not None:
        state.service_scope["config_fingerprint"] = fp.to_scope_dict()
    return SimpleNamespace(values=state.model_dump(), next=())


@pytest.mark.asyncio
async def test_event_stream_emits_config_changed_after_thread_created_and_keeps_it_out_of_history(
    monkeypatch,
):
    graph = MagicMock()
    graph.aget_state = AsyncMock(return_value=_completed_checkpoint(OLD))
    saved: dict = {}

    chunks = await _run_event_stream(monkeypatch, _event_stream_agent(graph, NEW), saved=saved)

    names = [chunk.split("\n", 1)[0] for chunk in chunks]
    assert names[:2] == ["event: thread_created", "event: config_changed"]
    payload = json.loads(chunks[1].split("data: ", 1)[1])
    assert payload == {"previous_agent_hash": OLD.short_hash(), "current_agent_hash": NEW.short_hash()}
    assert names[-1] == "event: Answer"
    assert {e["event_name"] for e in saved["events"]} == {"UserMessage", "Answer"}


@pytest.mark.asyncio
async def test_event_stream_stamps_the_fingerprint_into_the_checkpoint_scope(monkeypatch):
    graph = MagicMock()
    graph.aget_state = AsyncMock(return_value=SimpleNamespace(values=None, next=()))
    seen: dict = {}

    await _run_event_stream(monkeypatch, _event_stream_agent(graph, NEW), saved={}, scope_seen=seen)

    assert seen["config_fingerprint"] == NEW.to_scope_dict()


# --- upgrade, end to end through event_stream ----------------------------------------------


async def test_pending_interrupt_from_before_an_upgrade_is_cancelled_with_a_notice(monkeypatch):
    from cuga.backend.cuga_graph.state.agent_state import default_state

    config = {"tools": [{"name": "search"}]}
    monkeypatch.setattr("cuga.backend.server.config_fingerprint.runtime_version", lambda: "0.3.0")
    before_upgrade = compute_fingerprint(config)
    monkeypatch.setattr("cuga.backend.server.config_fingerprint.runtime_version", lambda: "0.4.0")
    after_upgrade = compute_fingerprint(config)
    assert before_upgrade.agent_hash == after_upgrade.agent_hash  # only the runtime changed

    state = default_state(page=None, observation=None, goal="")
    state.service_scope["config_fingerprint"] = before_upgrade.to_scope_dict()
    values = state.model_dump()
    pending = {"next": ("WaitForResponse",)}

    async def aget_state(_config):
        return SimpleNamespace(values=values, next=pending["next"])

    async def aupdate_state(_config, _update, as_node=None):
        if as_node == "__end__":
            pending["next"] = ()

    graph = MagicMock()
    graph.aget_state = AsyncMock(side_effect=aget_state)
    graph.aupdate_state = AsyncMock(side_effect=aupdate_state)
    saved: dict = {}

    chunks = await _run_event_stream(
        monkeypatch, _event_stream_agent(graph, after_upgrade), saved=saved, scope_seen=(seen := {})
    )

    names = [chunk.split("\n", 1)[0] for chunk in chunks]
    assert names[:2] == ["event: thread_created", "event: pending_cancelled"]
    assert json.loads(chunks[1].split("data: ", 1)[1]) == {"reason": "config_changed"}
    assert "event: config_changed" not in names  # the agent did not change, only the runtime
    assert names[-1] == "event: Answer"  # the new request then runs on the cancelled thread
    assert graph.aupdate_state.await_count == 2  # the stale approval was cleared, then dropped
    assert pending["next"] == ()
    assert seen["config_fingerprint"] == after_upgrade.to_scope_dict()
    assert {e["event_name"] for e in saved["events"]} == {"UserMessage", "Answer"}


# --- in-place policy updates at an unchanged published version ----------------------------


async def test_same_version_policy_rewrite_cancels_a_pending_approval(monkeypatch):
    """A tool-guard sync rewrites the published config in place; the graph is not rebuilt."""
    from cuga.backend.cuga_graph.state.agent_state import default_state

    built_from = {"policies": {"policies": [{"id": "p1", "tool_guards": {"delete": {"code": "v1"}}}]}}
    rewritten = {"policies": {"policies": [{"id": "p1", "tool_guards": {"delete": {"code": "v2"}}}]}}
    at_build = compute_fingerprint(built_from)
    assert at_build.agent_hash != compute_fingerprint(rewritten).agent_hash

    state = default_state(page=None, observation=None, goal="")
    state.service_scope["config_fingerprint"] = at_build.to_scope_dict()
    values = state.model_dump()
    pending = {"next": ("WaitForResponse",)}

    async def aget_state(_config):
        return SimpleNamespace(values=values, next=pending["next"])

    async def aupdate_state(_config, _update, as_node=None):
        if as_node == "__end__":
            pending["next"] = ()

    graph = MagicMock()
    graph.aget_state = AsyncMock(side_effect=aget_state)
    graph.aupdate_state = AsyncMock(side_effect=aupdate_state)
    run_agent = _event_stream_agent(graph, at_build)  # the graph still carries the build-time hash
    rebuilt_graph = MagicMock()
    rebuilt_graph.aget_state = AsyncMock(return_value=_completed_checkpoint(at_build))
    seen = []

    chunks = await _run_event_stream(
        monkeypatch,
        run_agent,
        saved={},
        stored_config=rewritten,
        refreshed_agent=_event_stream_agent(rebuilt_graph, compute_fingerprint(rewritten)),
        graph_seen=seen,
    )

    names = [chunk.split("\n", 1)[0] for chunk in chunks]
    assert "event: pending_cancelled" in names
    assert pending["next"] == ()
    # The old graph retains its build identity; a replacement graph handles the new message.
    assert run_agent._config_fingerprint.agent_hash == at_build.agent_hash
    assert seen == [rebuilt_graph]
    main_mod._refresh_published_graph.assert_awaited_once()


@pytest.mark.parametrize("stored_version", ["4", "5"], ids=["same-version-rewrite", "remote-publish"])
async def test_changed_published_config_rebuilds_the_graph_and_is_announced(monkeypatch, stored_version):
    """Another replica published, or a sync rewrote the config in place: rebuild, then notify."""
    built_from = {"tools": [{"name": "old"}]}
    rewritten = {"tools": [{"name": "new"}]}
    at_build = compute_fingerprint(built_from)
    graph = MagicMock()
    graph.aget_state = AsyncMock(return_value=_completed_checkpoint(at_build))
    rebuilt_graph = MagicMock()
    rebuilt_graph.aget_state = AsyncMock(return_value=_completed_checkpoint(at_build))
    rebuilt = _event_stream_agent(rebuilt_graph, compute_fingerprint(rewritten))
    rebuilt._checkpoint_config_version = stored_version
    seen = []
    scope_seen = {}

    chunks = await _run_event_stream(
        monkeypatch,
        _event_stream_agent(graph, at_build),
        saved={},
        stored_config=rewritten,
        stored_version=stored_version,
        refreshed_agent=rebuilt,
        graph_seen=seen,
        scope_seen=scope_seen,
    )

    names = [chunk.split("\n", 1)[0] for chunk in chunks]
    assert names[:2] == ["event: thread_created", "event: config_changed"]
    assert seen == [rebuilt_graph]
    assert scope_seen["config_fingerprint"]["agent_hash"] == compute_fingerprint(rewritten).agent_hash
    main_mod._refresh_published_graph.assert_awaited_once()


async def test_unreadable_published_config_cancels_a_pending_approval(monkeypatch):
    from cuga.backend.cuga_graph.state.agent_state import default_state

    state = default_state(page=None, observation=None, goal="")
    state.service_scope["config_fingerprint"] = OLD.to_scope_dict()
    values = state.model_dump()
    pending = {"next": ("WaitForResponse",)}

    async def aupdate_state(_config, _update, as_node=None):
        if as_node == "__end__":
            pending["next"] = ()

    graph = MagicMock()
    graph.aget_state = AsyncMock(side_effect=lambda _c: SimpleNamespace(values=values, next=pending["next"]))
    graph.aupdate_state = AsyncMock(side_effect=aupdate_state)

    chunks = await _run_event_stream(
        monkeypatch, _event_stream_agent(graph, OLD), saved={}, store_error=RuntimeError("store down")
    )

    assert any(chunk.startswith("event: pending_cancelled") for chunk in chunks)
    assert pending["next"] == ()
