"""``CUGA_ALLOWED_MSGPACK_TYPES`` must cover every type in CUGA's graph state (issue #774).

A type missing from the allowlist does not raise: langgraph emits a ``msgpack_blocked``
serde event and returns a plain dict. ``LANGGRAPH_STRICT_MSGPACK=true`` is set for the
test session in the repo-root ``conftest.py``.
"""

from __future__ import annotations

import dataclasses
import enum
import importlib
import json
import types
from pathlib import Path
from typing import Any, Literal, get_args, get_origin, get_type_hints

import pytest
from pydantic import BaseModel
from typing_extensions import is_typeddict

from cuga.backend.storage.checkpoint.serde import CUGA_ALLOWED_MSGPACK_TYPES, build_checkpoint_serde

pytestmark = pytest.mark.unit

_STATE_FIXTURE = Path(__file__).parents[1] / "state" / "fixtures" / "agent_state_v0_4_2.json"


def _state_schemas() -> list[type]:
    from cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_graph import CugaLiteState
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_state import CugaSupervisorState
    from cuga.backend.cuga_graph.state.agent_state import AgentState

    return [AgentState, CugaSupervisorState, CugaLiteState]


def _reachable_types(roots: list[type]) -> dict[tuple[str, str], str]:
    """Pydantic models, dataclasses and Enums reachable from ``roots``, with the path to each."""
    from langgraph.checkpoint.serde._msgpack import SAFE_MSGPACK_TYPES

    found: dict[tuple[str, str], str] = {}
    visited: set[Any] = set()

    def visit(tp: Any, path: str) -> None:
        origin = get_origin(tp)
        if origin is Literal:
            return
        if origin is not None or isinstance(tp, types.UnionType):
            for arg in get_args(tp):
                visit(arg, path)
            return
        if not isinstance(tp, type) or tp in visited:
            return
        visited.add(tp)
        key = (tp.__module__, tp.__name__)
        if key in SAFE_MSGPACK_TYPES:
            return
        if issubclass(tp, enum.Enum):
            found.setdefault(key, path)
            return
        if issubclass(tp, BaseModel) or dataclasses.is_dataclass(tp):
            found.setdefault(key, path)
        elif not is_typeddict(tp):
            return
        for name, hint in get_type_hints(tp, include_extras=True).items():
            visit(hint, f"{path}.{name}")

    for root in roots:
        visit(root, root.__name__)
    return found


def test_allowlist_covers_every_type_reachable_from_the_state_schemas():
    missing = {
        k: p for k, p in _reachable_types(_state_schemas()).items() if k not in CUGA_ALLOWED_MSGPACK_TYPES
    }
    assert not missing, (
        "Add these (module, ClassName) pairs to CUGA_ALLOWED_MSGPACK_TYPES, "
        "or checkpoints will silently load them as dicts:\n"
        + "\n".join(f"  {key!r},  # reached via {path}" for key, path in sorted(missing.items()))
    )


def test_allowlist_entries_name_classes_at_their_defining_module():
    # The serializer writes ``cls.__module__``; a re-export path or a bare module name never matches.
    for module, name in CUGA_ALLOWED_MSGPACK_TYPES:
        cls = getattr(importlib.import_module(module), name)
        assert (cls.__module__, cls.__name__) == (module, name)


async def test_populated_agent_state_survives_a_sqlite_restart(tmp_path):
    import aiosqlite
    from langgraph.checkpoint.serde.event_hooks import register_serde_event_listener
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from langgraph.graph import END, START, StateGraph

    from cuga.backend.cuga_graph.state.agent_state import AgentState

    state = AgentState(**json.loads(_STATE_FIXTURE.read_text(encoding="utf-8")))
    update = {name: getattr(state, name) for name in AgentState.model_fields}

    async def write_everything(_state):
        return update

    builder = StateGraph(AgentState)
    builder.add_node("write_everything", write_everything)
    builder.add_edge(START, "write_everything")
    builder.add_edge("write_everything", END)
    config = {"configurable": {"thread_id": "serde"}}
    events: list[dict] = []
    unregister = register_serde_event_listener(events.append)
    try:
        async with aiosqlite.connect(tmp_path / "cp.db") as conn:
            saver = AsyncSqliteSaver(conn, serde=build_checkpoint_serde())
            await saver.setup()
            await builder.compile(checkpointer=saver).ainvoke({}, config)
        # A new connection and serializer, and no graph: only the allowlist decides the types.
        async with aiosqlite.connect(tmp_path / "cp.db") as conn:
            stored = await AsyncSqliteSaver(conn, serde=build_checkpoint_serde()).aget_tuple(config)
    finally:
        unregister()

    assert events == [], f"serde allowlist events fired: {events}"
    channels = stored.checkpoint["channel_values"]
    for name in AgentState.model_fields:
        if name in channels:
            # pydantic equality includes the class, so a model loaded as a dict fails here.
            assert channels[name] == update[name], name
