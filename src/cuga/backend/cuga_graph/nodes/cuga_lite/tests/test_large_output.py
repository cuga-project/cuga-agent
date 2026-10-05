"""Large tool results (function-calling / hybrid): saved to a file, searched by a subagent.

Pins the free-code shape (file per tool-call id, ``<persisted-output>`` stub with a
preview), the subagent's grep/read tools over the file, the second loop (a check that
sends the subagent back when something is missing), and the ``tool_exec`` wiring:
on, a large result never reaches the batch truncation; off, nothing changes.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, List, Optional
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.tool_exec_node import create_tool_exec_node
from cuga.backend.cuga_graph.nodes.cuga_lite.helpers import large_output as lo
from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.harness_messages import harness_message
from cuga.backend.cuga_graph.nodes.cuga_lite.tracking import tracker as tracker_module

pytestmark = pytest.mark.unit

NEEDLE = "[ARCHIVE NOTE 0042] The Wing C generator was serviced by Priya Nandakumar."


def _haystack(lines: int = 3000) -> str:
    body = [f"line {i}: filler text about nothing in particular" for i in range(1, lines + 1)]
    body[min(1499, lines - 1)] = NEEDLE
    return "\n".join(body)


class _ScriptedModel:
    """Replies in order to every ``ainvoke`` (bound or not); records what it was sent."""

    def __init__(self, replies: List[AIMessage]):
        self.replies = list(replies)
        self.sent: List[List[BaseMessage]] = []
        self.bound_tools: List[Any] = []

    def bind_tools(self, tools):
        self.bound_tools = list(tools)
        return self

    async def ainvoke(self, messages, config=None):
        self.sent.append(list(messages))
        return self.replies.pop(0)


def _grep_call(pattern: str, call_id: str = "g1") -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": "grep", "args": {"pattern": pattern}, "id": call_id}])


@pytest.fixture
def cfg(monkeypatch, tmp_path):
    from cuga.config import settings

    values = {
        "enabled": True,
        "threshold_chars": 1000,
        "spill_dir": str(tmp_path),
        "search_max_steps": 4,
        "max_rounds": 2,
        "subagent_timeout": 30,
        "max_answer_chars": 4000,
        "preview_chars": 200,
    }
    for key, value in values.items():
        monkeypatch.setattr(settings.large_output, key, value, raising=False)
    return tmp_path


# ── the file and its tools ───────────────────────────────────────────────────


def test_grep_finds_the_match_with_numbered_context(tmp_path):
    path = tmp_path / "out.txt"
    path.write_text(_haystack())
    out = lo.OutputFile(path).grep("Nandakumar", context_lines=1)
    assert out.startswith("1 match(es)")
    assert "1499- line 1499" in out and f"1500: {NEEDLE}" in out and "1501- line 1501" in out


def test_grep_caps_matches_and_reports_the_total(tmp_path):
    path = tmp_path / "out.txt"
    path.write_text(_haystack(200))
    out = lo.OutputFile(path).grep("filler", context_lines=0, max_matches=3)
    assert out.startswith("199 match(es)") and "showing the first 3" in out
    assert out.count("filler text") == 3


def test_grep_falls_back_to_a_literal_search_on_a_bad_regex(tmp_path):
    path = tmp_path / "out.txt"
    path.write_text("a (b\nc\n")
    assert "1: a (b" in lo.OutputFile(path).grep("(b")


def test_grep_reports_no_match(tmp_path):
    path = tmp_path / "out.txt"
    path.write_text("x\ny\n")
    assert lo.OutputFile(path).grep("zzz").startswith("No matches")


def test_read_returns_a_numbered_range(tmp_path):
    path = tmp_path / "out.txt"
    path.write_text(_haystack())
    out = lo.OutputFile(path).read(offset=1499, limit=2)
    assert out.splitlines()[:2] == [
        "1499: line 1499: filler text about nothing in particular",
        f"1500: {NEEDLE}",
    ]
    assert "[lines 1499-1500 of 3,000]" in out
    assert "past the end" in lo.OutputFile(path).read(offset=5000)


def test_structured_results_are_indented_and_long_lines_wrapped():
    text = lo.as_searchable_text({"items": [{"id": 1}, {"id": 2}]})
    assert json.loads(text) == {"items": [{"id": 1}, {"id": 2}]} and len(text.splitlines()) > 3
    wrapped = lo.as_searchable_text("x" * 9000)
    assert max(len(line) for line in wrapped.splitlines()) <= 2000


def test_spill_writes_one_file_per_call_id_under_the_thread(cfg):
    path = lo.spill_to_file("hello", thread_id="t/1", call_id="call_9")
    assert path == cfg / "t_1" / "call_9.txt" and path.read_text() == "hello"
    # A replayed call id keeps the original file.
    assert lo.spill_to_file("other", thread_id="t/1", call_id="call_9").read_text() == "hello"


def test_need_uses_the_latest_user_request_not_a_runtime_note():
    messages = [
        HumanMessage(content="first question"),
        HumanMessage(content="who serviced the Wing C generator?"),
        harness_message("Call a tool before answering.", "empty_reply"),
        AIMessage(content="Fetching the archive to find the servicing record.", tool_calls=[]),
    ]
    need = lo.describe_need(messages, "get_archive_document", {"doc": "a"})
    assert "who serviced the Wing C generator?" in need and "Call a tool" not in need
    assert "Fetching the archive" in need and 'get_archive_document({"doc": "a"})' in need


# ── the subagent and the second loop ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_research_greps_the_file_and_returns_its_answer(cfg):
    path = lo.spill_to_file(_haystack(), thread_id="t", call_id="c")
    model = _ScriptedModel(
        [
            _grep_call("Wing C"),
            AIMessage(content="Priya Nandakumar (line 1500)."),
            AIMessage(content='{"complete": true, "missing": ""}'),
        ]
    )
    result = await lo.research(model, lo.OutputFile(path), "who serviced it?", total_chars=1)
    assert result.answer == "Priya Nandakumar (line 1500)." and result.rounds == 1 and result.complete is True
    tool_reply = model.sent[1][-1]
    assert isinstance(tool_reply, ToolMessage) and NEEDLE in tool_reply.content
    assert [t.name for t in model.bound_tools] == ["grep", "read"]


@pytest.mark.asyncio
async def test_an_incomplete_answer_sends_the_subagent_back_with_the_gap(cfg):
    path = lo.spill_to_file(_haystack(), thread_id="t", call_id="c")
    model = _ScriptedModel(
        [
            _grep_call("generator"),
            AIMessage(content="A generator was serviced."),
            AIMessage(content='{"complete": false, "missing": "who serviced it"}'),
            _grep_call("serviced by", "g2"),
            AIMessage(content="Priya Nandakumar serviced the Wing C generator (line 1500)."),
        ]
    )
    result = await lo.research(model, lo.OutputFile(path), "who serviced it?", total_chars=1)
    assert result.rounds == 2 and result.tool_calls == 2
    assert result.answer.startswith("Priya Nandakumar")
    reviewer_note = model.sent[3][-1]
    assert isinstance(reviewer_note, HumanMessage) and "who serviced it" in reviewer_note.content
    check_prompt = model.sent[2]
    assert len(check_prompt) == 2, "the check sees only the need and the findings, not the search transcript"


@pytest.mark.asyncio
async def test_one_round_skips_the_check(cfg):
    path = lo.spill_to_file(_haystack(), thread_id="t", call_id="c")
    model = _ScriptedModel([AIMessage(content="done")])
    result = await lo.research(model, lo.OutputFile(path), "need", total_chars=1, max_rounds=1)
    assert result.complete is None and len(model.sent) == 1


@pytest.mark.asyncio
async def test_running_out_of_searches_still_answers(cfg):
    path = lo.spill_to_file(_haystack(), thread_id="t", call_id="c")
    two_calls = AIMessage(
        content="",
        tool_calls=[
            {"name": "grep", "args": {"pattern": "a"}, "id": "1"},
            {"name": "grep", "args": {"pattern": "b"}, "id": "2"},
        ],
    )
    model = _ScriptedModel([two_calls, AIMessage(content="partial")])
    result = await lo.research(model, lo.OutputFile(path), "need", total_chars=1, max_steps=1, max_rounds=1)
    assert result.answer == "partial" and result.tool_calls == 1
    last_prompt = model.sent[-1]
    assert "used all your searches" in last_prompt[-1].content
    replies = [m for m in last_prompt if isinstance(m, ToolMessage)]
    assert [m.tool_call_id for m in replies] == ["1", "2"], "every call is answered, even the one not run"
    assert replies[1].content.startswith("Not run")


@pytest.mark.asyncio
async def test_without_a_model_the_agent_still_gets_the_path_and_a_preview(cfg):
    persisted = await lo.handle_large_result(
        _haystack(), tool_name="get_doc", call_id="c1", thread_id="t", messages=[], args={}, model=None
    )
    reply = persisted.text
    assert persisted.path == cfg / "t" / "c1.txt"
    assert reply.startswith(lo.PERSISTED_OUTPUT_TAG) and reply.endswith(lo.PERSISTED_OUTPUT_CLOSING_TAG)
    assert str(persisted.path) in reply and "Findings" not in reply
    assert 'query_saved_output(file="c1.txt"' in reply
    assert "line 1:" in reply and NEEDLE not in reply


# ── query_saved_output ───────────────────────────────────────────────────────


def test_only_files_this_thread_saved_can_be_queried(cfg):
    lo.spill_to_file("mine", thread_id="t1", call_id="c1")
    lo.spill_to_file("theirs", thread_id="t2", call_id="c2")
    (cfg / "secret.txt").write_text("not a saved output")
    assert lo.resolve_saved_file("c1.txt", "t1") == cfg / "t1" / "c1.txt"
    assert lo.resolve_saved_file("c1", "t1") == cfg / "t1" / "c1.txt", "the call id alone is enough"
    assert lo.resolve_saved_file(str(cfg / "t1" / "c1.txt"), "t1") == cfg / "t1" / "c1.txt"
    assert lo.resolve_saved_file("c2.txt", "t1") is None, "another thread's file"
    assert lo.resolve_saved_file("../secret.txt", "t1") is None
    assert lo.resolve_saved_file("/etc/passwd", "t1") is None


@pytest.mark.asyncio
async def test_query_runs_the_subagent_on_the_saved_file(cfg):
    lo.spill_to_file(_haystack(), thread_id="t", call_id="c1")
    model = _ScriptedModel(
        [
            _grep_call("Nandakumar"),
            AIMessage(content="Priya Nandakumar, line 1500."),
            AIMessage(content='{"complete": true}'),
        ]
    )
    text, ok = await lo.query_saved_output(
        "c1.txt", "who serviced Wing C?", thread_id="t", messages=[HumanMessage(content="hi")], model=model
    )
    assert ok and text.startswith("Findings from c1.txt (3,000 lines):\nPriya Nandakumar")
    assert "who serviced Wing C?" in model.sent[0][1].content
    assert NEEDLE in model.sent[1][-1].content


@pytest.mark.asyncio
async def test_query_for_an_unknown_file_lists_the_saved_ones(cfg):
    lo.spill_to_file("x", thread_id="t", call_id="c1")
    text, ok = await lo.query_saved_output(
        "nope.txt", "q", thread_id="t", messages=[], model=_ScriptedModel([])
    )
    assert not ok and "c1.txt" in text


def test_code_cannot_call_query_saved_output():
    from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.execute_python import code_context

    state = SimpleNamespace(variables_manager=MagicMock(get_variable_names=lambda: []))

    async def tool() -> str:
        return ""

    context = code_context(state, {"query_saved_output": tool, "get_doc": tool})
    assert "get_doc" in context and "query_saved_output" not in context


def test_a_cleared_saved_result_points_at_its_file():
    from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.tool_result_clearing import clear_old_tool_results

    old = ToolMessage(content="x" * 5000, tool_call_id="1", name="get_doc", artifact={"saved_file": "1.txt"})
    messages = [
        HumanMessage(content="q"),
        AIMessage(content="", tool_calls=[{"name": "get_doc", "args": {}, "id": "1"}]),
        old,
        AIMessage(content="", tool_calls=[{"name": "get_doc", "args": {}, "id": "2"}]),
        ToolMessage(content="y", tool_call_id="2", name="get_doc"),
        AIMessage(content="done"),
    ]
    cleared = clear_old_tool_results(messages, keep_recent=1, min_chars=100)[2]
    assert 'query_saved_output(file="1.txt"' in cleared.content


# ── tool_exec wiring ─────────────────────────────────────────────────────────


class _Adapter:
    messages_key = "chat_messages"
    metadata_key = "cuga_lite_metadata"
    execute_node_name = "sandbox"
    sender_name = "CugaLite"

    def __init__(self, tools: dict, model: Optional[Any] = None):
        self._tools_context = tools
        self._tracker = MagicMock()
        self._model = model

    def get_messages(self, state):
        return list(state.chat_messages or [])

    def resolve_max_steps(self, state, override):
        return override if override is not None else 50

    def get_invoke_config(self, configurable):
        return {"callbacks": []}


@pytest.fixture(autouse=True)
def _reset_budget_contexts():
    yield
    tracker_module._tool_call_budget_context.set(None)
    tracker_module._thread_tool_call_budget_context.set(None)
    tracker_module._block_tool_call_budget_context.set(None)
    tracker_module._block_tool_call_cap_override_context.set(None)


def _state(call: dict):
    last = AIMessage(content="", tool_calls=[{**call, "type": "tool_call"}])
    return SimpleNamespace(
        chat_messages=[HumanMessage(content="who serviced the Wing C generator?"), last],
        step_count=0,
        tool_calls=[],
        tool_calls_used_run=0,
        tool_calls_used_thread=0,
    )


async def _run_node(model, monkeypatch, call: Optional[dict] = None) -> ToolMessage:
    from cuga.config import settings

    monkeypatch.setattr(settings.advanced_features, "max_tool_calls_per_block", 0, raising=False)

    async def get_doc() -> str:
        return _haystack()

    call = call or {"name": "get_doc", "args": {}, "id": "call_1"}
    node = create_tool_exec_node(_Adapter({"get_doc": get_doc}, model=model))
    result = await node(_state(call), config={"configurable": {"thread_id": "thread-x"}})
    return [m for m in result.update["chat_messages"] if isinstance(m, ToolMessage)][0]


@pytest.mark.asyncio
async def test_a_large_native_result_is_saved_and_searched_instead_of_truncated(cfg, monkeypatch):
    from cuga.config import settings

    monkeypatch.setattr(settings.advanced_features, "execution_output_max_length", 5000, raising=False)
    model = _ScriptedModel(
        [
            _grep_call("Wing C"),
            AIMessage(content="Priya Nandakumar."),
            AIMessage(content='{"complete": true}'),
        ]
    )
    reply = await _run_node(model, monkeypatch)
    assert reply.content.startswith(lo.PERSISTED_OUTPUT_TAG)
    assert "Findings:\nPriya Nandakumar." in reply.content
    assert "result truncated" not in reply.content
    assert reply.artifact == {"saved_file": "call_1.txt"}
    assert (cfg / "thread-x" / "call_1.txt").read_text() == _haystack()
    assert "who serviced the Wing C generator?" in model.sent[0][1].content


@pytest.mark.asyncio
async def test_query_saved_output_is_dispatched_by_tool_exec(cfg, monkeypatch):
    lo.spill_to_file(_haystack(), thread_id="thread-x", call_id="call_1")
    model = _ScriptedModel(
        [_grep_call("Wing C"), AIMessage(content="Priya Nandakumar."), AIMessage(content="{}")]
    )
    call = {
        "name": "query_saved_output",
        "args": {"file": "call_1.txt", "question": "who serviced it?"},
        "id": "q1",
    }
    reply = await _run_node(model, monkeypatch, call)
    assert reply.status == "success" and reply.content.startswith("Findings from call_1.txt")

    missing = await _run_node(
        _ScriptedModel([]), monkeypatch, {**call, "args": {"file": "other.txt", "question": "q"}}
    )
    assert missing.status == "error" and "call_1.txt" in missing.content


@pytest.mark.asyncio
async def test_disabled_keeps_the_batch_truncation(cfg, monkeypatch):
    from cuga.config import settings

    monkeypatch.setattr(settings.large_output, "enabled", False, raising=False)
    monkeypatch.setattr(settings.advanced_features, "execution_output_max_length", 5000, raising=False)
    model = _ScriptedModel([])
    reply = await _run_node(model, monkeypatch)
    assert "result truncated" in reply.content and lo.PERSISTED_OUTPUT_TAG not in reply.content
    assert model.sent == [] and not (cfg / "thread-x").exists()


# ── main_agent mode (ablation: no subagent) ──────────────────────────────────


@pytest.fixture
def main_agent(cfg, monkeypatch):
    from cuga.config import settings

    monkeypatch.setattr(settings.large_output, "mode", "main_agent", raising=False)
    return cfg


def test_each_mode_binds_its_own_tools(cfg, monkeypatch):
    from cuga.config import settings

    assert [t.name for t in lo.create_saved_output_tools()] == ["query_saved_output"]
    monkeypatch.setattr(settings.large_output, "mode", "main_agent", raising=False)
    assert [t.name for t in lo.create_saved_output_tools()] == ["grep_saved_output", "read_saved_output"]


@pytest.mark.asyncio
async def test_main_agent_mode_saves_the_file_and_never_runs_a_subagent(main_agent, monkeypatch):
    from cuga.config import settings

    monkeypatch.setattr(settings.advanced_features, "execution_output_max_length", 5000, raising=False)
    model = _ScriptedModel([])
    reply = await _run_node(model, monkeypatch)
    assert model.sent == [], "no subagent call"
    assert reply.content.startswith(lo.PERSISTED_OUTPUT_TAG) and "Findings" not in reply.content
    assert 'grep_saved_output(file="call_1.txt"' in reply.content
    assert (main_agent / "thread-x" / "call_1.txt").read_text() == _haystack()


@pytest.mark.asyncio
async def test_main_agent_greps_and_reads_the_saved_file_itself(main_agent, monkeypatch):
    lo.spill_to_file(_haystack(), thread_id="thread-x", call_id="call_1")
    grep = await _run_node(
        None,
        monkeypatch,
        {"name": "grep_saved_output", "args": {"file": "call_1.txt", "pattern": "Nandakumar"}, "id": "g"},
    )
    assert grep.status == "success" and f"1500: {NEEDLE}" in grep.content
    read = await _run_node(
        None,
        monkeypatch,
        {"name": "read_saved_output", "args": {"file": "call_1", "offset": 1500, "limit": 1}, "id": "r"},
    )
    assert read.status == "success" and read.content.startswith(f"1500: {NEEDLE}")
    other = await _run_node(
        None,
        monkeypatch,
        {"name": "grep_saved_output", "args": {"file": "../x.txt", "pattern": "a"}, "id": "o"},
    )
    assert other.status == "error" and "call_1.txt" in other.content


def test_every_part_of_a_long_line_is_reachable(tmp_path):
    long_line = "a" * 1500 + "NEEDLE" + "b" * 2500
    path = tmp_path / "out.txt"
    path.write_text(lo.as_searchable_text(long_line))
    file = lo.OutputFile(path)
    hit = file.grep("NEEDLE")
    assert "NEEDLE" in hit.split("\n", 1)[1], "the matched chunk is shown whole, not cut before the match"
    assert "line cut" not in file.read(1, 10)
