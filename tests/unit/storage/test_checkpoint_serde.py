"""``build_checkpoint_serde`` and ``CUGA_ALLOWED_MSGPACK_TYPES`` (issue #774, W16.1).

A type missing from the allowlist does not raise: langgraph logs once, emits a
``msgpack_blocked`` serde event and hands back the raw kwargs dict (or the raw
enum value). So these tests check ``type(...)`` on every value that comes back
and fail on any serde event, instead of waiting for an exception.

What langgraph actually checks: a pydantic model is stored as
``(module, name, model_dump())`` and rebuilt with ``cls(**data)``, so only the
outermost model of each stored value is looked up in the allowlist (nested
models are rebuilt by pydantic validation; nested enums are checked because
``model_dump()`` keeps them as members). A pydantic-state graph stores each
state field in its own channel, so every typed sub-model that sits directly in
a field is checked. The round-trip tests store values the same way, through a
real saver, and read the raw ``channel_values`` back with a fresh serializer so
the graph's own schema allowlist (merged in strict mode) cannot hide a gap.

The drift tests walk the state schemas and the compiled graphs and fail when a
reachable pydantic model, dataclass or Enum is not registered.

``LANGGRAPH_STRICT_MSGPACK=true`` is set for the whole test session in the
repo-root ``conftest.py``.
"""

from __future__ import annotations

import ast
import collections.abc
import dataclasses
import enum
import importlib
import os
import types
import typing
from pathlib import Path
from typing import Any, Literal, get_args, get_origin, get_type_hints
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel
from typing_extensions import is_typeddict

from cuga.backend.storage.checkpoint.serde import CUGA_ALLOWED_MSGPACK_TYPES, build_checkpoint_serde

pytestmark = pytest.mark.unit

_SRC_ROOT = Path(__file__).resolve().parents[3] / "src" / "cuga"

# Names that may be passed as a graph state schema anywhere under src/cuga.
# ``state_class`` is the parameter of ``shared_graph.build_agent_graph``; its
# callers are checked through the ``build_agent_graph(state_class=...)`` calls.
_KNOWN_STATE_SCHEMA_NAMES = {"AgentState", "CugaSupervisorState", "CugaLiteState", "state_class"}
# Calls whose state schema argument the AST guard inspects.
_GRAPH_FACTORIES = ("StateGraph", "build_agent_graph", "create_react_agent", "create_agent")


def _state_schemas() -> list[type[BaseModel]]:
    from cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_graph import CugaLiteState
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_state import CugaSupervisorState
    from cuga.backend.cuga_graph.state.agent_state import AgentState

    return [AgentState, CugaSupervisorState, CugaLiteState]


def _safe_msgpack_types() -> frozenset[tuple[str, ...]]:
    from langgraph.checkpoint.serde._msgpack import SAFE_MSGPACK_TYPES

    return SAFE_MSGPACK_TYPES


@pytest.fixture
def serde_events():
    """Collect every langgraph serde allowlist event raised during the test."""
    from langgraph.checkpoint.serde.event_hooks import register_serde_event_listener

    events: list[dict] = []
    unregister = register_serde_event_listener(events.append)
    yield events
    unregister()


# ---------------------------------------------------------------------------
# Fully populated state: every field filled, every typed sub-model present.
# ---------------------------------------------------------------------------


def _sample(tp: Any, path: str) -> Any:
    from langchain_core.messages import BaseMessage, HumanMessage

    origin = get_origin(tp)
    if origin is typing.Annotated:
        return _sample(get_args(tp)[0], path)
    if origin is Literal:
        return get_args(tp)[-1]
    if origin is typing.Union or isinstance(tp, types.UnionType):
        args = [a for a in get_args(tp) if a is not type(None)]
        return _sample(args[0], path)
    if origin in (set, frozenset, tuple):
        args = [a for a in get_args(tp) if a is not Ellipsis] or [str]
        return origin([_sample(args[0], f"{path}[]")])
    if origin in (list, collections.abc.Sequence):
        args = get_args(tp) or (str,)
        return [_sample(args[0], f"{path}[]")]
    if origin is dict:
        args = get_args(tp) or (str, Any)
        return {"k": _sample(args[1], f"{path}[k]")}
    if tp is Any or tp is object:
        return f"any:{path}"
    if tp is str:
        return f"s:{path}"
    if tp is bool:
        return True
    if tp is int:
        return 7
    if tp is float:
        return 1.5
    if tp in (dict, list):
        return tp()
    if isinstance(tp, type) and issubclass(tp, enum.Enum):
        return list(tp)[-1]
    if isinstance(tp, type) and issubclass(tp, BaseMessage):
        cls = HumanMessage if tp is BaseMessage else tp
        return cls(content=f"m:{path}", id=f"id:{path}")
    if isinstance(tp, type) and issubclass(tp, BaseModel):
        return _populate(tp, path)
    if dataclasses.is_dataclass(tp):
        hints = get_type_hints(tp)
        return tp(**{f.name: _sample(hints[f.name], f"{path}.{f.name}") for f in dataclasses.fields(tp)})
    raise AssertionError(f"test sample builder does not know how to fill {path}: {tp!r}")


def _populate(model: type[BaseModel], path: str = "", **overrides: Any) -> BaseModel:
    path = path or model.__name__
    values = {
        name: _sample(field.annotation, f"{path}.{name}")
        for name, field in model.model_fields.items()
        if name not in overrides
    }
    return model(**values, **overrides)


def _hitl_models() -> tuple[BaseModel, BaseModel]:
    from cuga.backend.cuga_graph.nodes.human_in_the_loop.followup_model import ActionResponse, FollowUpAction

    return _populate(FollowUpAction), _populate(ActionResponse)


def _populated_state(state_cls: type[BaseModel]) -> BaseModel:
    from cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_graph import CugaLiteState

    if state_cls is CugaLiteState:
        # ``hitl_action`` / ``hitl_response`` are ``Optional[Any]`` here, but the
        # tool-approval handler stores real FollowUpAction / ActionResponse models.
        action, response = _hitl_models()
        return _populate(state_cls, hitl_action=action, hitl_response=response)
    return _populate(state_cls)


def _assert_same_types(
    original: Any,
    loaded: Any,
    path: str,
    seen_types: set[type],
    *,
    whole_object: bool = False,
    _inside_dump: bool = False,
) -> None:
    """Assert ``loaded`` has the same concrete type as ``original``, recursively.

    ``whole_object=True`` is for a state object stored as one value. langgraph
    stores it as ``model_dump()`` and rebuilds it with ``cls(**data)``, so below
    the root two things change no matter which serializer or allowlist is used:
    a model inside an ``Any`` field stays a dict, and a message in a plain
    ``List[BaseMessage]`` field (no reviving validator, unlike
    ``AgentState.chat_messages``) comes back as a ``BaseMessage`` of the same
    ``type``. Those are tolerated there; enums are still checked strictly,
    because ``model_dump()`` keeps them as members and the allowlist decides.
    Per-field (channel) storage keeps every concrete class and is checked
    strictly everywhere.
    """
    from langchain_core.messages import BaseMessage

    if _inside_dump and isinstance(original, BaseMessage):
        assert isinstance(loaded, BaseMessage) and loaded.type == original.type, path
        assert loaded.content == original.content, path
        return
    if _inside_dump and isinstance(original, BaseModel) and type(loaded) is dict:
        assert loaded == original.model_dump(), path
        return
    assert type(loaded) is type(original), (
        f"{path}: expected {type(original).__qualname__}, got {type(loaded).__qualname__} "
        "(a blocked msgpack type comes back as its raw dict/value)"
    )
    if isinstance(original, (BaseModel, enum.Enum)) or dataclasses.is_dataclass(original):
        seen_types.add(type(original))

    def recurse(a: Any, b: Any, sub_path: str) -> None:
        _assert_same_types(a, b, sub_path, seen_types, _inside_dump=_inside_dump or whole_object)

    if isinstance(original, BaseModel):
        for name in type(original).model_fields:
            recurse(getattr(original, name), getattr(loaded, name), f"{path}.{name}")
    elif isinstance(original, dict):
        assert loaded.keys() == original.keys(), path
        for key, value in original.items():
            recurse(value, loaded[key], f"{path}[{key!r}]")
    elif isinstance(original, (list, tuple)):
        assert len(loaded) == len(original), path
        for i, (a, b) in enumerate(zip(original, loaded)):
            recurse(a, b, f"{path}[{i}]")
    else:
        assert loaded == original, path


def _registered_classes() -> set[type]:
    return {getattr(importlib.import_module(module), name) for module, name in CUGA_ALLOWED_MSGPACK_TYPES}


def _assert_no_serde_events(events: list[dict]) -> None:
    assert events == [], f"serde allowlist events fired: {events}"


# ---------------------------------------------------------------------------
# Strict mode and the shape of the allowlist
# ---------------------------------------------------------------------------


def test_strict_msgpack_is_active_for_the_test_session():
    from langgraph._internal import _serde
    from langgraph.checkpoint.serde import _msgpack

    flag = os.environ.get("LANGGRAPH_STRICT_MSGPACK", "true").strip().lower()
    if flag not in ("1", "true", "yes"):
        pytest.skip(f"LANGGRAPH_STRICT_MSGPACK={flag!r} was set explicitly")
    # Both copies are read once at import; the root conftest sets the env var first.
    assert _msgpack.STRICT_MSGPACK_ENABLED is True
    assert _serde.STRICT_MSGPACK_ENABLED is True


def test_allowlist_entries_are_module_class_pairs():
    # langgraph matches on (module, ClassName). Bare module names or 1-tuples are
    # accepted silently, match nothing, and block every CUGA type.
    for entry in CUGA_ALLOWED_MSGPACK_TYPES:
        assert isinstance(entry, tuple) and len(entry) == 2, entry
        assert all(isinstance(part, str) and part for part in entry), entry
    assert len(set(CUGA_ALLOWED_MSGPACK_TYPES)) == len(CUGA_ALLOWED_MSGPACK_TYPES), "duplicate entries"


def test_allowlist_entries_name_real_classes_at_their_defining_module():
    for module, name in CUGA_ALLOWED_MSGPACK_TYPES:
        cls = getattr(importlib.import_module(module), name)
        # The serializer writes ``cls.__module__``; a re-export path would never match.
        assert (cls.__module__, cls.__name__) == (module, name)
        assert issubclass(cls, (BaseModel, enum.Enum)) or dataclasses.is_dataclass(cls), cls


def test_build_checkpoint_serde_uses_the_allowlist():
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    serde = build_checkpoint_serde()

    assert isinstance(serde, JsonPlusSerializer)
    assert serde._allowed_msgpack_modules == set(CUGA_ALLOWED_MSGPACK_TYPES)
    assert build_checkpoint_serde(encrypt=False)._allowed_msgpack_modules == set(CUGA_ALLOWED_MSGPACK_TYPES)


def test_build_checkpoint_serde_refuses_encryption_until_w16_2():
    with pytest.raises(NotImplementedError):
        build_checkpoint_serde(encrypt=True)


def test_bare_module_names_block_every_cuga_type(serde_events):
    """Negative control: the wrong allowlist shape silently degrades to dicts."""
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    from cuga.backend.cuga_graph.state.agent_state import AgentState

    bare = JsonPlusSerializer(allowed_msgpack_modules=[(module,) for module, _ in CUGA_ALLOWED_MSGPACK_TYPES])
    loaded = bare.loads_typed(bare.dumps_typed(_populated_state(AgentState)))

    assert type(loaded) is dict
    assert {(e["kind"], e["name"]) for e in serde_events} >= {("msgpack_blocked", "AgentState")}


# ---------------------------------------------------------------------------
# Round trips
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("state_name", ["AgentState", "CugaSupervisorState", "CugaLiteState"])
def test_state_round_trips_through_dumps_and_loads(state_name, serde_events):
    state_cls = next(s for s in _state_schemas() if s.__name__ == state_name)
    state = _populated_state(state_cls)
    serde = build_checkpoint_serde()
    seen: set[type] = set()

    # The whole object, and each field on its own the way a channel stores it.
    _assert_same_types(
        state, serde.loads_typed(serde.dumps_typed(state)), state_name, seen, whole_object=True
    )
    for name in state_cls.model_fields:
        value = getattr(state, name)
        _assert_same_types(value, serde.loads_typed(serde.dumps_typed(value)), f"{state_name}.{name}", seen)

    _assert_no_serde_events(serde_events)


def test_round_trips_exercise_every_registered_type():
    """The populated states really contain an instance of every allowlisted type,
    and each one comes back as itself when stored per field (as a channel)."""
    serde = build_checkpoint_serde()
    seen: set[type] = set()
    for state_cls in _state_schemas():
        state = _populated_state(state_cls)
        seen.add(type(serde.loads_typed(serde.dumps_typed(state))))
        for name in state_cls.model_fields:
            value = getattr(state, name)
            _assert_same_types(
                value, serde.loads_typed(serde.dumps_typed(value)), f"{state_cls.__name__}.{name}", seen
            )

    from cuga.backend.cuga_graph.nodes.human_in_the_loop.followup_model import ActionType

    # FollowUpAction / ActionResponse use ``use_enum_values=True``, so ActionType is
    # stored as its string value today. It stays registered because the schemas
    # reach it (and langgraph's own compile-time walk lists it).
    never_stored_as_itself = {ActionType}
    missing = _registered_classes() - seen - never_stored_as_itself
    assert not missing, f"no populated value for: {sorted(c.__qualname__ for c in missing)}"


def test_dropping_an_entry_is_detected(serde_events):
    """Sensitivity check: without one pair, the field comes back as a dict."""
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    from cuga.backend.cuga_graph.state.agent_state import AgentState

    dropped = ("cuga.backend.cuga_graph.nodes.cuga_agent_core.schemas.task_models", "TaskDecompositionPlan")
    serde = JsonPlusSerializer(
        allowed_msgpack_modules=[p for p in CUGA_ALLOWED_MSGPACK_TYPES if p != dropped]
    )
    value = _populated_state(AgentState).task_decomposition

    loaded = serde.loads_typed(serde.dumps_typed(value))

    assert type(loaded) is dict
    assert ("msgpack_blocked", "TaskDecompositionPlan") in {(e["kind"], e["name"]) for e in serde_events}


def _single_node_graph(state_cls: type[BaseModel], state: BaseModel, checkpointer):
    from langgraph.graph import END, START, StateGraph

    update = {name: getattr(state, name) for name in state_cls.model_fields}

    async def write_everything(_state):
        return update

    graph = StateGraph(state_cls)
    graph.add_node("write_everything", write_everything)
    graph.add_edge(START, "write_everything")
    graph.add_edge("write_everything", END)
    return graph.compile(checkpointer=checkpointer)


def _assert_channels_match(state: BaseModel, channel_values: dict, label: str) -> None:
    seen: set[type] = set()
    for name in type(state).model_fields:
        assert name in channel_values, f"{label}: channel {name} was not stored"
        _assert_same_types(getattr(state, name), channel_values[name], f"{label}.{name}", seen)


@pytest.mark.parametrize("state_name", ["AgentState", "CugaSupervisorState", "CugaLiteState"])
async def test_state_round_trips_through_memory_saver(state_name, serde_events):
    from langgraph.checkpoint.memory import MemorySaver

    state_cls = next(s for s in _state_schemas() if s.__name__ == state_name)
    state = _populated_state(state_cls)
    saver = MemorySaver(serde=build_checkpoint_serde())
    graph = _single_node_graph(state_cls, state, saver)
    config = {"configurable": {"thread_id": f"serde-{state_name}"}}

    await graph.ainvoke({}, config)
    snapshot = await graph.aget_state(config)
    _assert_channels_match(state, snapshot.values, f"{state_name} via graph")

    # ``saver`` is the original instance: its serde has only CUGA_ALLOWED_MSGPACK_TYPES,
    # not the schema allowlist the compiled graph merges in under strict mode.
    stored = await saver.aget_tuple(config)
    _assert_channels_match(state, stored.checkpoint["channel_values"], f"{state_name} raw")

    _assert_no_serde_events(serde_events)


@pytest.mark.parametrize("state_name", ["AgentState", "CugaSupervisorState", "CugaLiteState"])
async def test_state_survives_a_sqlite_restart(state_name, tmp_path, serde_events):
    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    state_cls = next(s for s in _state_schemas() if s.__name__ == state_name)
    state = _populated_state(state_cls)
    db_path = tmp_path / "checkpoints.db"
    config = {"configurable": {"thread_id": f"serde-{state_name}"}}

    async with aiosqlite.connect(db_path) as conn:
        saver = AsyncSqliteSaver(conn, serde=build_checkpoint_serde())
        await saver.setup()
        await _single_node_graph(state_cls, state, saver).ainvoke({}, config)

    # "Restart": a new connection and a new serializer, and no graph, so nothing
    # but CUGA_ALLOWED_MSGPACK_TYPES decides what is reconstructed.
    async with aiosqlite.connect(db_path) as conn:
        stored = await AsyncSqliteSaver(conn, serde=build_checkpoint_serde()).aget_tuple(config)

    assert stored is not None
    _assert_channels_match(state, stored.checkpoint["channel_values"], f"{state_name} after restart")
    _assert_no_serde_events(serde_events)


# ---------------------------------------------------------------------------
# Drift: a new state type must be registered
# ---------------------------------------------------------------------------


def _reachable_types(roots: list[type]) -> dict[tuple[str, str], str]:
    """Pydantic models, dataclasses and Enums reachable from ``roots``, keyed by
    ``(module, name)``, with the field path that reaches each one.

    Broader than langgraph's own walk (any generic container, TypedDicts,
    dataclasses); stops at types langgraph already treats as safe.
    """
    safe = _safe_msgpack_types()
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
        if key in safe:
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
    reachable = _reachable_types(_state_schemas())
    missing = {key: path for key, path in reachable.items() if key not in set(CUGA_ALLOWED_MSGPACK_TYPES)}

    assert not missing, (
        "Add these (module, ClassName) pairs to CUGA_ALLOWED_MSGPACK_TYPES in "
        "cuga/backend/storage/checkpoint/serde.py, or checkpoints will silently load them as dicts:\n"
        + "\n".join(f"  {key!r},  # reached via {path}" for key, path in sorted(missing.items()))
    )


def _graph_state_schema_names() -> set[str]:
    names: set[str] = set()
    for path in _SRC_ROOT.rglob("*.py"):
        if "tests" in path.relative_to(_SRC_ROOT).parts:
            continue
        text = path.read_text(encoding="utf-8")
        if not any(f"{name}(" in text for name in _GRAPH_FACTORIES):
            continue
        for node in ast.walk(ast.parse(text)):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            func_name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if func_name == "StateGraph":
                schema = (
                    node.args[0]
                    if node.args
                    else next((kw.value for kw in node.keywords if kw.arg == "state_schema"), None)
                )
            elif func_name == "build_agent_graph":
                schema = next((kw.value for kw in node.keywords if kw.arg == "state_class"), None)
            elif func_name in ("create_react_agent", "create_agent"):
                # Prebuilt agents default to langgraph's own TypedDict state; only a
                # custom ``state_schema=`` needs walking.
                schema = next((kw.value for kw in node.keywords if kw.arg == "state_schema"), None)
            else:
                continue
            if schema is not None:
                names.add(f"{ast.unparse(schema)}  ({path.relative_to(_SRC_ROOT)})")
    return names


def test_every_graph_state_schema_is_walked_by_the_drift_test():
    unknown = {
        entry for entry in _graph_state_schema_names() if entry.split()[0] not in _KNOWN_STATE_SCHEMA_NAMES
    }

    assert not unknown, (
        "A graph uses a state schema this test does not walk. Add it to _state_schemas() and "
        f"_KNOWN_STATE_SCHEMA_NAMES, then register its types: {sorted(unknown)}"
    )


async def _build_entry_graph(*, supervisor: bool):
    from cuga.backend.cuga_graph.entry_graph import CugaEntryGraph
    from cuga.backend.cuga_graph.nodes.cuga_lite.providers.base import ToolProviderInterface
    from cuga.backend.llm.models import LLMManager

    tool_provider = MagicMock(spec=ToolProviderInterface)
    tool_provider.initialize = AsyncMock()
    tool_provider.get_apps = AsyncMock(return_value=[])
    with patch.object(LLMManager, "get_model", return_value=MagicMock()):
        agent = CugaEntryGraph(
            None,
            tool_provider=tool_provider,
            supervisor_enabled=supervisor,
            supervisor_agents={} if supervisor else None,
        )
        await agent.build_graph()
    return agent.graph


async def test_allowlist_covers_the_compiled_graph_schema_allowlists():
    """Cross-check with langgraph's own compile-time walk (state, input, output,
    node and branch schemas, channels) on the real entry graph and its subgraphs."""
    from langgraph._internal import _serde

    if not _serde.STRICT_MSGPACK_ENABLED:
        pytest.skip("compile-time schema allowlists are only built in strict mode")

    root = await _build_entry_graph(supervisor=True)
    graphs = {"entry": root}
    for name, node in root.nodes.items():
        bound = getattr(node, "bound", None)
        if hasattr(bound, "_serde_allowlist"):
            graphs[name] = bound
    assert {"CugaLiteSubgraph", "CugaSupervisorSubgraph"} <= graphs.keys()

    safe = _safe_msgpack_types()
    registered = set(CUGA_ALLOWED_MSGPACK_TYPES)
    for name, graph in graphs.items():
        allowlist = graph._serde_allowlist
        assert allowlist, f"{name}: no compile-time allowlist"
        missing = {key for key in allowlist if key not in safe and key not in registered}
        assert not missing, f"{name}: register these in CUGA_ALLOWED_MSGPACK_TYPES: {sorted(missing)}"
