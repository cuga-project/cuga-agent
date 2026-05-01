import copy
import numpy as np
from langchain_core.messages import HumanMessage, AIMessage
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_graph import (
    _sanitize_cuga_lite_update,
)
from cuga.backend.evolve.formatting import (
    build_evolve_guidelines_section,
    build_evolve_user_preference_section,
    format_evolve_guidelines,
    format_evolve_user_preference,
    get_first_human_message_content,
    get_latest_memory_query,
    parse_evolve_guideline_items,
)
from cuga.backend.cuga_graph.state.agent_state import AgentState


def test_get_first_human_message_content_uses_first_user_message():
    messages = [
        AIMessage(content="system-ish"),
        HumanMessage(content="first user message"),
        HumanMessage(content="latest user message"),
    ]

    assert get_first_human_message_content(messages) == "first user message"


def test_get_latest_memory_query_skips_internal_execution_output():
    messages = [
        HumanMessage(content="user preference"),
        HumanMessage(content="Execution output: internal loop"),
    ]

    assert get_latest_memory_query(messages) == "user preference"


def test_format_evolve_user_preference_keeps_preference_section_separate():
    formatted = format_evolve_user_preference(
        {
            "style": [
                {
                    "content": "Prefers concise answers",
                    "key": "response_style",
                    "value": "concise",
                }
            ]
        }
    )

    assert "If any remembered preference conflicts with the latest user message" in formatted
    assert "Style:" in formatted
    assert "- Prefers concise answers" in formatted
    assert "Guidelines" not in formatted


def test_format_evolve_user_preference_returns_empty_for_no_facts():
    assert format_evolve_user_preference({}) == ""


def test_build_evolve_user_preference_section_includes_header():
    section = build_evolve_user_preference_section({"style": [{"content": "Prefers concise answers"}]})

    assert section.startswith("\n\n## Evolve User Preference\n")
    assert "Prefers concise answers" in section


def test_parse_evolve_guideline_items_strips_header_and_keeps_numbered_items():
    raw_guidelines = (
        "# Guidelines for: fetch all users\n\n"
        "1. Check pagination before assuming the first page is complete.\n"
        "2. Reuse IDs from prior responses instead of guessing them.\n"
    )

    assert parse_evolve_guideline_items(raw_guidelines) == [
        "Check pagination before assuming the first page is complete.",
        "Reuse IDs from prior responses instead of guessing them.",
    ]


def test_format_evolve_guidelines_uses_legacy_memory_tip_framing():
    formatted = format_evolve_guidelines(
        "# Guidelines for: fetch all users\n\n"
        "1. Check pagination before assuming the first page is complete.\n"
        "2. Reuse IDs from prior responses instead of guessing them.\n"
    )

    assert formatted.startswith("Here are some useful guidelines derived from past experiences")
    assert "Do not ignore them" in formatted
    assert "1. Check pagination before assuming the first page is complete." in formatted
    assert "2. Reuse IDs from prior responses instead of guessing them." in formatted
    assert "# Guidelines for:" not in formatted


def test_build_evolve_guidelines_section_includes_header_and_formatted_content():
    section = build_evolve_guidelines_section(
        "# Guidelines for: fetch all users\n\n"
        "1. Check pagination before assuming the first page is complete.\n"
    )

    assert section.startswith("\n\n## Evolve Guidelines\n")
    assert "Guideline Usage" in section
    assert "Check pagination before assuming the first page is complete." in section


def test_format_evolve_guidelines_returns_empty_for_blank_input():
    assert format_evolve_guidelines("") == ""
    assert build_evolve_guidelines_section("   ") == ""


def test_memory_helpers_do_not_mutate_chat_messages():
    state = AgentState(
        input="hello",
        url="https://example.com",
        chat_messages=[HumanMessage(content="I prefer concise answers")],
    )
    before = copy.deepcopy(state.chat_messages)

    _ = get_first_human_message_content(state.chat_messages)
    _ = get_latest_memory_query(state.chat_messages)
    _ = format_evolve_user_preference({"style": [{"content": "Prefers concise answers"}]})
    _ = format_evolve_guidelines("1. Prefer concise answers.")

    assert state.chat_messages == before


def test_sanitize_cuga_lite_update_converts_nested_numpy_scalars_for_checkpointing():
    update = _sanitize_cuga_lite_update(
        {
            "chat_messages": [HumanMessage(content="find matching contacts")],
            "variables_storage": {
                "account_summary": {
                    "value": {
                        "score": np.float64(0.91),
                        "totals": {"revenue": np.float64(1250.5)},
                    },
                    "description": "Created during code execution",
                    "type": "dict",
                    "created_at": "2026-04-15T12:00:00",
                    "count_items": 2,
                }
            },
            "cuga_lite_metadata": {
                "policy_confidence": np.float64(0.82),
                "trigger_details": {"trigger_0": {"confidence": np.float64(0.77)}},
            },
            "tool_calls": [
                {
                    "name": "find_tools",
                    "result": {"output": {"value": "# Found 2 Matching Tool(s)", "score": np.float64(0.66)}},
                }
            ],
        }
    )

    assert isinstance(update["chat_messages"][0], HumanMessage)
    assert update["variables_storage"]["account_summary"]["value"]["score"] == 0.91
    assert isinstance(update["variables_storage"]["account_summary"]["value"]["score"], float)
    assert update["cuga_lite_metadata"]["policy_confidence"] == 0.82
    assert isinstance(update["cuga_lite_metadata"]["policy_confidence"], float)
    assert update["tool_calls"][0]["result"]["output"]["score"] == 0.66
    assert isinstance(update["tool_calls"][0]["result"]["output"]["score"], float)

    JsonPlusSerializer().dumps_typed(update)
