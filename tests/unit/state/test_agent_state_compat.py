"""Checkpoint compatibility: an ``AgentState`` written by an older release must still load.

Durable checkpointers keep a thread's state across CUGA upgrades, and LangGraph does
not migrate application state (design §5.3, issue #774). When a stored thread is
resumed, LangGraph rebuilds the state from the saved channel values. That is
``AgentState(**values)``, and nested pydantic models are rebuilt with ``cls(**kwargs)``
too. So a release breaks old threads when it:

* adds a field with no default (old checkpoints lack it, and validation fails), or
* renames or removes a field (the stored value is dropped without an error).

``fixtures/agent_state_v<release>.json`` holds a fully populated ``AgentState``
dumped by each release. Every fixture in the directory is loaded on every build, so
adding a release means adding a file, not editing this module.

Regenerating (once per release, from that release's code)::

    uv run python tests/unit/state/test_agent_state_compat.py            # writes v<pyproject version>
    uv run python tests/unit/state/test_agent_state_compat.py --check    # dry run, prints the JSON

The generator refuses to write a fixture in which any ``AgentState`` field still
holds its default value. A field that was never populated would not catch a later
rename or removal, so extend ``build_representative_state`` when you add a field.
Never edit or regenerate an *existing* fixture: it records what that release
actually wrote.

Format: JSON from ``model_dump(mode="json")``. The savers store each channel with
``JsonPlusSerializer`` (msgpack), not JSON. msgpack keeps nested pydantic models as
typed values, while JSON flattens them to dicts. Both reach the same
``cls(**kwargs)`` validation on load, so JSON is enough to catch schema drift and
stays readable in review. Messages are dumped with their ``type`` discriminator
(``SerializeAsAny``) and rebuilt by ``AgentState._revive_message_types``, the same
path a ``Command(update=state.model_dump())`` hop takes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import AliasChoices, AliasPath
from pydantic_core import PydanticUndefined

from cuga.backend.cuga_graph.state.agent_state import AgentState

pytestmark = pytest.mark.unit

FIXTURES_DIR = Path(__file__).parent / "fixtures"
FIXTURE_GLOB = "agent_state_v*.json"

# Keys an older release wrote that the current AgentState no longer declares.
# Loading still tolerates them (pydantic's default ``extra="ignore"`` drops them), but
# each one must be listed here on purpose. Dropping a key loses the data a stored
# thread had in it, so a rename must add a ``validation_alias`` for the old name
# instead of landing here. Map each key to the reason it was dropped.
ACKNOWLEDGED_REMOVED_FIELDS: dict[str, str] = {}


def _fixture_paths() -> list[Path]:
    return sorted(FIXTURES_DIR.glob(FIXTURE_GLOB))


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _accepted_input_keys() -> dict[str, str]:
    """Every input key the model accepts, mapped to its field (a rename keeps the old name here)."""
    keys: dict[str, str] = {}
    for name, field in AgentState.model_fields.items():
        keys[name] = name
        if field.alias:
            keys[field.alias] = name
        va = field.validation_alias
        choices = va.choices if isinstance(va, AliasChoices) else [va] if va else []
        for choice in choices:
            if isinstance(choice, str):
                keys[choice] = name
            elif isinstance(choice, AliasPath) and isinstance(choice.path[0], str):
                keys[choice.path[0]] = name
    return keys


def _assert_preserved(expected: Any, actual: Any, where: str) -> None:
    """``expected`` survives inside ``actual``: equal values, with dict keys checked as a subset.

    A subset check (not equality) means a dependency that adds a key to a dumped
    message does not fail this test, while a value the new code drops or changes does.
    """
    if isinstance(expected, dict):
        assert isinstance(actual, dict), f"{where}: expected a dict, got {type(actual).__name__}"
        for key, value in expected.items():
            assert key in actual, f"{where}.{key}: stored value was dropped on load"
            _assert_preserved(value, actual[key], f"{where}.{key}")
    elif isinstance(expected, list):
        assert isinstance(actual, list) and len(actual) == len(expected), f"{where}: list changed on load"
        for i, (e, a) in enumerate(zip(expected, actual)):
            _assert_preserved(e, a, f"{where}[{i}]")
    else:
        assert actual == expected, f"{where}: {expected!r} loaded as {actual!r}"


@pytest.fixture(params=_fixture_paths(), ids=lambda p: p.stem)
def fixture_values(request) -> dict[str, Any]:
    return _load(request.param)


def test_fixtures_exist():
    assert _fixture_paths(), f"no {FIXTURE_GLOB} under {FIXTURES_DIR}"


def test_fixture_loads_with_kwargs_and_model_validate(fixture_values):
    """A field added without a default, or a type narrowed incompatibly, fails here."""
    by_kwargs = AgentState(**fixture_values)
    by_validate = AgentState.model_validate(fixture_values)
    assert by_kwargs.model_dump(mode="json") == by_validate.model_dump(mode="json")


def test_fixture_keys_are_known_or_acknowledged(fixture_values):
    """A stored key the model no longer accepts is silently dropped, so it must be deliberate.

    Rename a field with ``validation_alias=AliasChoices("new_name", "old_name")`` and the
    old key stays accepted. Remove a field and add it to ``ACKNOWLEDGED_REMOVED_FIELDS``.
    """
    unknown = set(fixture_values) - set(_accepted_input_keys()) - set(ACKNOWLEDGED_REMOVED_FIELDS)
    assert not unknown, (
        f"stored keys no longer accepted by AgentState: {sorted(unknown)}. "
        "Add a validation_alias for a rename, or list a removal in ACKNOWLEDGED_REMOVED_FIELDS."
    )


def test_fixture_values_survive_load(fixture_values):
    """Each stored value that still has a field (by name or alias) comes back out of the loaded state."""
    loaded = AgentState(**fixture_values).model_dump(mode="json", by_alias=False)
    accepted = _accepted_input_keys()
    for key, value in fixture_values.items():
        if key in accepted:
            _assert_preserved(value, loaded[accepted[key]], key)


def test_fixture_restores_working_objects(fixture_values):
    """The loaded state is usable, not only valid: message classes and variables come back."""
    state = AgentState(**fixture_values)
    for name in ("chat_messages", "chat_agent_messages", "supervisor_chat_messages", "messages"):
        for stored, message in zip(fixture_values.get(name) or [], getattr(state, name) or []):
            assert message.type == stored["type"], f"{name}: {stored['type']} revived as {message.type}"
            if stored.get("tool_calls"):
                assert message.tool_calls == stored["tool_calls"]
            if stored.get("tool_call_id"):
                assert message.tool_call_id == stored["tool_call_id"]
    manager = state.variables_manager
    for name, entry in (fixture_values.get("variables_storage") or {}).items():
        assert manager.get_variable(name) == entry["value"]


def test_removed_field_is_tolerated():
    """A key from a field that no longer exists does not break loading (pydantic ``extra="ignore"``)."""
    values = AgentState().model_dump(mode="json")
    values["field_removed_in_a_later_release"] = {"anything": [1, 2, 3]}

    state = AgentState(**values)

    assert not hasattr(state, "field_removed_in_a_later_release")
    assert AgentState.model_config.get("extra", "ignore") == "ignore", (
        "AgentState must ignore unknown keys so old checkpoints keep loading; "
        "extra='forbid' breaks them and extra='allow' resurrects removed fields as attributes"
    )


@pytest.mark.parametrize("field_name", [n for n, f in AgentState.model_fields.items() if not f.is_required()])
def test_field_with_default_may_be_missing(field_name):
    """The "new field needs a default" rule: a checkpoint written before a field existed lacks it."""
    values = AgentState().model_dump(mode="json")
    del values[field_name]

    state = AgentState(**values)

    expected = AgentState.model_fields[field_name].get_default(call_default_factory=True)
    assert getattr(state, field_name) == expected


def test_every_field_has_a_default():
    """A required field cannot be filled from a checkpoint written before it existed."""
    required = [n for n, f in AgentState.model_fields.items() if f.is_required()]
    assert not required, f"AgentState fields without a default break old checkpoints: {required}"


# --------------------------------------------------------------------------------------
# Fixture generator. Run this module as a script at release time (see the docstring).
# --------------------------------------------------------------------------------------

_FIXED_TIME = "2026-10-20T12:00:00"


def build_representative_state() -> AgentState:
    """An AgentState with every field set to a non-default, realistic value."""
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

    from cuga.backend.cuga_graph.nodes.cuga_agent_core.schemas.api_models import ApiDescription
    from cuga.backend.cuga_graph.nodes.cuga_agent_core.schemas.browser_models import NextAgentPlan
    from cuga.backend.cuga_graph.nodes.cuga_agent_core.schemas.task_models import (
        AnalyzeTaskOutput,
        DecomposedTask,
        TaskDecompositionPlan,
    )
    from cuga.backend.cuga_graph.nodes.human_in_the_loop.followup_model import (
        ActionResponse,
        ActionType,
        AdditionalData,
        FollowUpAction,
        SelectOption,
    )
    from cuga.backend.cuga_graph.state.agent_state import AnalyzeTaskAppsOutput, SubTaskHistory
    from cuga.backend.cuga_graph.state.api_planner_history import HistoricalAction

    call = {"name": "crm_get_accounts", "args": {"limit": 2}, "id": "call_1", "type": "tool_call"}
    plan = NextAgentPlan(
        thoughts=["The accounts page is open", "Read the table"],
        next_agent="ActionAgent",
        instruction="Click the Accounts tab",
    )
    option = SelectOption(value="approve", label="Approve", description="Run the plan")
    state = AgentState(
        user_id="user-42",
        thread_id="thread-compat-fixture",
        tool_calls_used_thread=3,
        service_scope={"tenant_id": "tenant-a", "instance_id": "instance-1"},
        current_datetime="2026-10-20 12:00",
        lite_mode=True,
        supervisor_mode=True,
        current_app="crm",
        current_app_description="Customer relationship manager",
        api_last_step="crm_get_accounts",
        guidance="Prefer the accounts API",
        chat_messages=[
            SystemMessage(content="You are CUGA."),
            HumanMessage(content="List my top two accounts", id="msg-h1"),
            AIMessage(content="", tool_calls=[call], id="msg-a1"),
            ToolMessage(content='[{"name": "Acme"}]', tool_call_id="call_1", name="crm_get_accounts"),
            AIMessage(content="Acme and Globex.", id="msg-a2"),
        ],
        chat_agent_messages=[HumanMessage(content="hi"), AIMessage(content="Hello, how can I help?")],
        supervisor_chat_messages=[
            HumanMessage(content="Delegate the CRM task"),
            AIMessage(content="", tool_calls=[{**call, "id": "call_s1"}]),
            ToolMessage(content="done", tool_call_id="call_s1", name="crm_get_accounts"),
        ],
        supervisor_metadata={"plan_approved": True, "delegations": 1},
        script="result = crm_agent('List the top two accounts')",
        api_intent_relevant_apps=[AnalyzeTaskAppsOutput(name="crm", description="CRM", type="api")],
        api_intent_relevant_apps_current=[
            AnalyzeTaskAppsOutput(name="crm", url="http://localhost:8007", type="api")
        ],
        shortlister_relevant_apps=["crm"],
        shortlister_query="top accounts",
        coder_task="Fetch the two largest accounts",
        coder_variables=["variable_1"],
        coder_relevant_apis=[
            ApiDescription(app_name="crm", api_name="get_accounts", api_description="List accounts")
        ],
        api_planner_codeagent_plan="1. call get_accounts 2. sort by revenue",
        api_shortlister_planner_filtered_apis="get_accounts",
        api_shortlister_all_filtered_apis={"crm": ["get_accounts", "get_contacts"]},
        sub_task="List the top two accounts",
        api_planner_history=[
            HistoricalAction(
                action_taken="CoderAgent",
                input_to_agent={"task": "Fetch accounts"},
                agent_output={"variables_summary": "variable_1: list", "final_output": "ok"},
            )
        ],
        api_planner_human_consultations=[{"question": "Which region?", "answer": "EMEA"}],
        sub_task_app="crm",
        sub_task_type="hybrid",
        hybrid_original_task="Find the accounts and open the first one",
        hybrid_api_task="Find the accounts",
        hybrid_web_task="Open the first account",
        hybrid_api_answer="Acme",
        hybrid_phase="web",
        input="List my top two accounts",
        last_planner_answer="Acme and Globex",
        last_question="Which region?",
        final_answer="Acme and Globex.",
        final_answer_finalized=True,
        task_decomposition=TaskDecompositionPlan(
            thoughts="One API step and one web step",
            task_decomposition=[
                DecomposedTask(task="Find the accounts", app="crm", type="api"),
                DecomposedTask(task="Open the first account", app="crm-web", type="web"),
            ],
        ),
        sub_tasks_progress=["completed", "in-progress"],
        feedback=[{"rating": "up", "comment": "good"}],
        sites=["http://localhost:8007"],
        observation="Table with 2 rows",
        url="http://localhost:8007/accounts",
        elements_as_string="[12] button 'Accounts'",
        focused_element_bid="12",
        elements="<button bid=12>Accounts</button>",
        messages=[AIMessage(content="Clicked Accounts", id="msg-browser-1")],
        sender="ActionAgent",
        previous_steps=[plan],
        stm_steps_history=["Clicked Accounts"],
        stm_all_history=[
            SubTaskHistory(sub_task="Find the accounts", steps=["api call"], final_answer="Acme")
        ],
        next_step="Read the table",
        task_analyzer_output=AnalyzeTaskOutput(
            attrs={"needs_web": True},
            paraphrased_intent="Show the top two accounts",
            navigation_paths={"crm-web": ["/accounts"]},
            resolved_intent="List accounts sorted by revenue",
        ),
        plan=plan,
        plan_next_agent="ActionAgent",
        pi="Profile: sales manager",
        hitl_action=FollowUpAction(
            action_id="approve_plan",
            action_name="Approve plan",
            description="Approve the delegation plan",
            type=ActionType.SINGLE_SELECT,
            callback_url="/functions_call",
            additional_data=AdditionalData(tool={"name": "crm_get_accounts"}),
            return_to="CugaSupervisor",
            options=[option],
            timeout_seconds=300,
        ),
        hitl_response=ActionResponse(
            action_id="approve_plan",
            response_type=ActionType.SINGLE_SELECT,
            timestamp=_FIXED_TIME,
            user_id="user-42",
            session_id="thread-compat-fixture",
            selected_values=["approve"],
            selected_options=[option],
            confirmed=True,
            metadata={"source": "ui"},
        ),
        update_plan_reason="The table loaded",
        read_page="Accounts\nAcme\nGlobex",
        env_policy=[{"name": "no_delete", "rule": "never delete records"}],
        tool_call={"name": "crm_get_accounts", "args": {"limit": 2}},
        cuga_lite_metadata={"step_count": 2, "mode": "native_fc"},
        tool_calls=[{"name": "crm_get_accounts", "arguments": {"limit": 2}, "result": ["Acme", "Globex"]}],
        sources=[{"id": "s1", "title": "Accounts report", "uri": "kb://accounts.pdf"}],
        last_summarization_metrics={"tokens_before": 9000, "tokens_after": 2500},
    )
    # Real variable entries, written the way the agent writes them; pin the clock.
    manager = state.variables_manager
    manager.add_variable([{"name": "Acme"}, {"name": "Globex"}], name="variable_1", description="Accounts")
    manager.add_variable("Acme", description="Top account")
    for entry in state.variables_storage.values():
        entry["created_at"] = _FIXED_TIME
    return state


def _fields_left_at_default(state: AgentState) -> list[str]:
    left = []
    for name, field in AgentState.model_fields.items():
        default = field.get_default(call_default_factory=True)
        if default is not PydanticUndefined and getattr(state, name) == default:
            left.append(name)
    return left


def _main(argv: list[str]) -> int:
    import tomllib

    root = Path(__file__).resolve().parents[3]
    version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    state = build_representative_state()
    left = _fields_left_at_default(state)
    if left:
        print(f"populate these fields in build_representative_state first: {left}", file=sys.stderr)
        return 1
    text = json.dumps(state.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
    if "--check" in argv:
        print(text)
        return 0
    target = FIXTURES_DIR / f"agent_state_v{version.replace('.', '_')}.json"
    if target.exists():
        print(f"{target} exists; fixtures record a past release and are never rewritten", file=sys.stderr)
        return 1
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
