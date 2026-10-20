"""Tests for the static check that keeps synchronous LangGraph state calls out of src/cuga.

The checker (``scripts/checks/no_sync_graph_state.py``) supports #774: a durable async
checkpointer raises on the synchronous saver API when it is called from the event loop,
so production code must use ``aget_state`` / ``aupdate_state``. The check flags any
synchronous ``.get_state(...)`` / ``.update_state(...)`` call, skips docstrings, comments
and string literals (it works on the AST), and skips files under a ``tests`` directory.
"""

from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path

import pytest

_MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "checks" / "no_sync_graph_state.py"
_spec = importlib.util.spec_from_file_location("no_sync_graph_state", _MODULE_PATH)
assert _spec and _spec.loader
checker = importlib.util.module_from_spec(_spec)
# Register before exec so dataclasses can resolve the module under `from __future__
# import annotations` (it looks the module up in sys.modules by __module__).
sys.modules["no_sync_graph_state"] = checker
_spec.loader.exec_module(checker)

pytestmark = pytest.mark.unit


def _lines(src: str) -> list[int]:
    """Return the 1-based line numbers flagged in ``src``."""
    return [v.line for v in checker.find_violations(textwrap.dedent(src), "<test>")]


# --- SHOULD flag -----------------------------------------------------------------


def test_flags_self_graph_get_state():
    src = """\
    class Agent:
        async def run(self, cfg):
            return self.graph.get_state(cfg).values
    """
    assert _lines(src) == [3]


def test_flags_update_state():
    src = """\
    class Agent:
        async def run(self, cfg, state):
            self.graph.update_state(cfg, state)
    """
    assert _lines(src) == [3]


@pytest.mark.parametrize(
    "call",
    [
        "agent.graph.get_state(cfg)",
        "run_agent.graph.get_state(cfg)",
        "self.agent_loop_obj.graph.update_state(cfg, state)",
        "graph.get_state(cfg)",
        "compiled.get_state(cfg)",
        "graph.get_state_history(cfg)",
        "graph.bulk_update_state(cfg, updates)",
    ],
)
def test_flags_any_receiver(call):
    assert _lines(f"x = {call}\n") == [1]


def test_flags_multiline_call_at_its_first_line():
    src = """\
    values = run_agent.graph.get_state(
        {"configurable": {"thread_id": tid}}
    ).values
    """
    assert _lines(src) == [1]


def test_message_names_the_async_replacement():
    (violation,) = checker.find_violations("s = self.graph.get_state(cfg)\n", "<test>")
    assert "self.graph.get_state" in violation.message
    assert "await self.graph.aget_state" in violation.message


# --- should NOT flag -------------------------------------------------------------


def test_allows_async_calls():
    src = """\
    class Agent:
        async def run(self, cfg, state):
            snapshot = await self.graph.aget_state(cfg)
            await self.graph.aupdate_state(cfg, state)
            return snapshot
    """
    assert _lines(src) == []


def test_ignores_docstring_and_comment_mentions():
    src = '''\
    def resume(agent, config):
        """Example:

            state = agent.graph.get_state(config)
            agent.graph.update_state(config, {"hitl_response": {}})
        """
        # do not call self.graph.get_state(cfg) here
        msg = "Use agent.graph.get_state() and agent.graph.update_state()."
        return msg
    '''
    assert _lines(src) == []


def test_ignores_bare_function_and_similar_names():
    src = """\
    get_state(cfg)
    self.browser_update_state(state)
    self.get_state_chat_messages_count(resp)
    """
    assert _lines(src) == []


def test_noqa_suppresses_on_any_line_of_the_call():
    src = """\
    a = store.get_state(key)  # noqa: sync-graph-state
    b = store.update_state(
        key, value,  # noqa: sync-graph-state
    )
    """
    assert _lines(src) == []


def test_syntax_error_is_not_a_violation():
    assert _lines("def broken(:\n") == []


# --- CLI -------------------------------------------------------------------------


_PLANTED = "class A:\n    async def run(self, cfg):\n        return self.graph.get_state(cfg)\n"


def test_cli_fails_on_planted_call_and_prints_file_line(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    pkg = tmp_path / "src" / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "mod.py").write_text(_PLANTED, encoding="utf-8")

    assert checker.main(["src"]) == 1
    err = capsys.readouterr().err
    assert "src/pkg/mod.py:3:" in err
    assert "1 finding(s)" in err


def test_cli_passes_on_async_code(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "mod.py").write_text(
        "async def f(graph, cfg):\n    return await graph.aget_state(cfg)\n", encoding="utf-8"
    )
    assert checker.main(["mod.py"]) == 0


@pytest.mark.parametrize("test_dir", ["tests", "src/pkg/tests", "src/pkg/tests/nested"])
def test_cli_skips_files_under_tests_dirs(tmp_path, monkeypatch, test_dir):
    monkeypatch.chdir(tmp_path)
    target = tmp_path / test_dir
    target.mkdir(parents=True)
    (target / "test_mod.py").write_text(_PLANTED, encoding="utf-8")

    # Skipped both when found by walking a directory and when passed explicitly
    # (pre-commit passes file names).
    assert checker.main(["."]) == 0
    assert checker.main([f"{test_dir}/test_mod.py"]) == 0


def test_cli_does_not_skip_a_file_merely_named_like_a_test(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "tests_helpers.py").write_text(_PLANTED, encoding="utf-8")
    assert checker.main(["tests_helpers.py"]) == 1


def test_default_target_is_src_cuga():
    assert checker.DEFAULT_TARGETS == ("src/cuga",)
