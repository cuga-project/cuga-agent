"""`cuga evaluate` returns when the evaluation ends, not when the registry does."""

import pytest

from cuga.cli import main as cli_main


@pytest.fixture(autouse=True)
def _clean_processes():
    cli_main.direct_processes.clear()
    yield
    cli_main.direct_processes.clear()


class _FakeProcess:
    """Reports each value of *polls* in turn, then the last one for good."""

    def __init__(self, pid, polls):
        self.pid = pid
        self._polls = list(polls)

    def poll(self):
        return self._polls.pop(0) if len(self._polls) > 1 else self._polls[0]


@pytest.mark.unit
def test_wait_returns_the_exit_code_and_stops_the_registry(monkeypatch):
    registry = _FakeProcess(1, [None])
    evaluation = _FakeProcess(2, [None, 3])
    cli_main.direct_processes.update({"registry": registry, "evaluation": evaluation})
    stopped = []
    monkeypatch.setattr(cli_main, "kill_process_tree", lambda pid: stopped.append(pid))
    monkeypatch.setattr(cli_main.time, "sleep", lambda _: None)

    assert cli_main.wait_for_direct_process("evaluation") == 3

    # The registry was still running, so it was stopped; nothing is left behind.
    assert stopped == [registry.pid]
    assert cli_main.direct_processes == {}


@pytest.mark.unit
def test_wait_without_the_process_returns_none():
    assert cli_main.wait_for_direct_process("evaluation") is None
