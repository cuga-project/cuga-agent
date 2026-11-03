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


@pytest.mark.parametrize(
    "src",
    [
        "x = self.graph.get_state(cfg)\n",
        "self.agent_loop_obj.graph.update_state(cfg, state)\n",
        "x = graph.get_state_history(cfg)\n",
        "graph.bulk_update_state(cfg, updates)\n",
        "values = run_agent.graph.get_state(\n    {'configurable': {}}\n).values\n",
    ],
)
def test_flags_sync_state_calls_at_their_first_line(src):
    assert _lines(src) == [1]


def test_ignores_async_calls_text_mentions_noqa_and_similar_names():
    src = '''\
    async def resume(agent, cfg, state):
        """Example: state = agent.graph.get_state(cfg)"""
        # do not call self.graph.get_state(cfg) here
        snapshot = await agent.graph.aget_state(cfg)
        await agent.graph.aupdate_state(cfg, state)
        get_state(cfg)
        self.browser_update_state(state)
        a = store.get_state(key)  # noqa: sync-graph-state
        return snapshot
    '''
    assert _lines(src) == []


_PLANTED = "class A:\n    async def run(self, cfg):\n        return self.graph.get_state(cfg)\n"


def test_cli_reports_file_and_line_and_skips_tests_dirs(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src" / "pkg" / "tests").mkdir(parents=True)
    (tmp_path / "src" / "pkg" / "mod.py").write_text(_PLANTED, encoding="utf-8")
    (tmp_path / "src" / "pkg" / "tests" / "test_mod.py").write_text(_PLANTED, encoding="utf-8")

    assert checker.main(["src"]) == 1
    err = capsys.readouterr().err
    assert "src/pkg/mod.py:3:" in err
    assert "1 finding(s)" in err
    # pre-commit passes file names; files under tests/ are skipped that way too.
    assert checker.main(["src/pkg/tests/test_mod.py"]) == 0
