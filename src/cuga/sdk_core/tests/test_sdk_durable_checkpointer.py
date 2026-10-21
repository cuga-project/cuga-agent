"""A ``CugaAgent`` on ``cuga.checkpointers.sqlite`` resumes a thread in a new process (#774, W15.4).

Each step runs ``_durable_checkpointer_child.py`` in its own interpreter
(``sys.executable``, the parent's environment), so the only thing two steps share
is the SQLite file. The child uses a fake chat model, blocks socket connects and DNS
lookups, and reports any attempt; the tests assert there were none.

``LANGGRAPH_STRICT_MSGPACK`` is set to ``true`` by the root conftest and inherited by
the children, so a CUGA state type missing from the serde allowlist fails these
tests instead of loading as a dict.

Marked ``slow``: each child pays the ``cuga.sdk`` import (about 5 s warm), so the file
takes about 20 s. The ``sdk`` CI job does not exclude ``slow``, so it still runs there.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow

_CHILD = Path(__file__).with_name("_durable_checkpointer_child.py")
_RESULT_PREFIX = "RESULT_JSON: "
_ANSWER = "There are 3 accounts."
_PAUSED = "Execution paused for approval"
# Mirrors VARIABLE_* in the child. Plain JSON values: a user pydantic model kept in
# variables_storage would come back from the default allowlist serde as a dict, by
# design (#774 ledger), so it could not be compared with ==.
_VARIABLE_NAME = "customer"
_VARIABLE_VALUE = {"id": 42, "name": "Acme", "tags": ["vip", "emea"], "balance": 12.5, "active": True}
# Tracing exporters would try the network; the child would refuse and report it.
_TRACING_ENV = (
    "LANGSMITH_TRACING",
    "LANGCHAIN_TRACING_V2",
    "LANGCHAIN_TRACING",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
)


def _run_child(tmp_path: Path, step: str, threads: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in _TRACING_ENV}
    # The children do not get this directory's conftest, which points DBS_DIR at
    # tmp_path; keep their local config/policy store out of the developer's DBS_DIR.
    env["CUGA_DBS_DIR"] = str(tmp_path / "dbs")
    assert env.get("LANGGRAPH_STRICT_MSGPACK") == "true", "root conftest should set strict msgpack"

    proc = subprocess.run(
        [sys.executable, str(_CHILD), step, str(tmp_path / "cp.db"), threads],
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
        cwd=tmp_path,
    )
    lines = [line for line in proc.stdout.splitlines() if line.startswith(_RESULT_PREFIX)]
    assert proc.returncode == 0 and lines, (
        f"child step {step!r} failed (rc={proc.returncode})\n"
        f"--- stdout ---\n{proc.stdout[-4000:]}\n--- stderr ---\n{proc.stderr[-8000:]}"
    )
    out = json.loads(lines[-1][len(_RESULT_PREFIX) :])
    assert out["saver"] == "AsyncSqliteSaver"
    assert out["network_attempts"] == []
    return out


def test_second_process_continues_the_thread_with_messages_and_variables(tmp_path):
    first = _run_child(tmp_path, "turn1", "t")

    assert first["answer"] == _ANSWER
    assert first["fake_llm_calls"] >= 1  # the answer came from the fake model
    assert first["after"]["chat_messages"] == ["first question", _ANSWER]
    assert first["after"]["variables"] == {_VARIABLE_NAME: _VARIABLE_VALUE}

    second = _run_child(tmp_path, "turn2", "t")

    # Read from the file before process 2 ran anything on the thread.
    assert second["before"]["chat_messages"] == ["first question", _ANSWER]
    assert second["before"]["variables"] == {_VARIABLE_NAME: _VARIABLE_VALUE}
    # Turn 2 appended to that history and kept the variable.
    assert second["answer"] == _ANSWER
    assert second["fake_llm_calls"] >= 1
    assert second["after"]["chat_messages"] == ["first question", _ANSWER, "second question", _ANSWER]
    assert second["after"]["variables"] == {_VARIABLE_NAME: _VARIABLE_VALUE}
    assert second["result_variables"] == {_VARIABLE_NAME: _VARIABLE_VALUE}
    assert second["after"]["next"] == []


def test_second_process_resumes_a_hitl_pause(tmp_path):
    # Two paused threads: one resumed with the public API, one on the compiled graph.
    paused = _run_child(tmp_path, "hitl_pause", "h-sdk,h-graph")

    for thread_id in ("h-sdk", "h-graph"):
        pause = paused["threads"][thread_id]
        assert _PAUSED in pause["answer"]
        assert pause["after"]["next"] == ["WaitForResponse"]
        assert pause["after"]["hitl_action"] is True
        assert pause["after"]["chat_messages"] == ["delete the users table"]

    resumed = _run_child(tmp_path, "hitl_resume", "h-sdk,h-graph")

    for thread_id in ("h-sdk", "h-graph"):
        resume = resumed["threads"][thread_id]
        # Process 2 found the pending approval in the file ...
        assert resume["before"]["next"] == ["WaitForResponse"], thread_id
        assert resume["before"]["hitl_action"] is True, thread_id
        # ... resumed it to the final answer, and nothing is pending any more.
        assert resume["answer"] == "deleted after approval", thread_id
        assert resume["after"]["next"] == [], thread_id
        assert resume["after"]["interrupts"] == 0, thread_id
        assert resume["after"]["hitl_action"] is False, thread_id
        assert resume["after"]["chat_messages"] == ["delete the users table"], thread_id
