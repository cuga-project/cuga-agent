"""Post-block pagination audit (#750).

A list call returning exactly ``page_limit`` items with no next page requested
must be flagged in the observation; a short page, an advanced page or an error
must not. The tool result itself is never modified — the note rides on the
execution output.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from cuga.backend.cuga_graph.nodes.cuga_lite.providers.registry import create_tool_from_api_dict
from cuga.backend.cuga_graph.nodes.cuga_lite.tracking.pagination_audit import (
    NOTE_FOOTER,
    audit_pagination,
    describe_pagination,
    list_length,
    render_pagination_notes,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.tracking.tracker import ToolCallTracker


def _call(name, args, result, *, app="gmail", error=None, defaults=None):
    return {
        "name": name,
        "app_name": app,
        "error": error,
        "pagination": describe_pagination(args, result, arg_defaults=defaults),
    }


def _rows(n):
    return [{"id": i} for i in range(n)]


# ── list_length ────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize(
    "result, expected",
    [
        (_rows(5), 5),
        ([], 0),
        ({"items": _rows(3)}, 3),
        ({"results": _rows(2), "total": 40}, 2),
        ({"threads": _rows(4), "page": 0}, 4),  # single list-valued field
        ({"a": _rows(1), "b": _rows(2)}, None),  # ambiguous
        ({"status": "exception", "message": "422"}, None),
        ({"error": "boom"}, None),
        ('[{"id": 1}, {"id": 2}]', 2),
        ("not json", None),
        (42, None),
        (None, None),
    ],
)
def test_list_length(result, expected):
    assert list_length(result) == expected


# ── describe_pagination ────────────────────────────────────────────────────


@pytest.mark.unit
def test_describe_returns_none_without_a_page_size():
    assert describe_pagination({"query": "x"}, _rows(3)) is None
    assert describe_pagination(None, _rows(3)) is None
    assert describe_pagination({"page_limit": "20"}, _rows(3)) is None  # not an int
    assert describe_pagination({"page_limit": True}, _rows(3)) is None  # bool is not a page size


@pytest.mark.unit
def test_describe_reads_explicit_page_args():
    info = describe_pagination({"query": "q", "page_index": 2, "page_limit": 20}, _rows(20))
    assert info["limit"] == 20 and info["index"] == 2
    assert info["limit_key"] == "page_limit" and info["index_key"] == "page_index"
    assert info["result_len"] == 20
    assert "page_index=2" in info["args_preview"] and "query='q'" in info["args_preview"]


@pytest.mark.unit
def test_describe_uses_schema_defaults_for_omitted_args():
    """AppWorld defaults page_limit to 5: an omitted page size is still a page size."""
    info = describe_pagination({"query": "q"}, _rows(5), arg_defaults={"page_index": 0, "page_limit": 5})
    assert info["limit"] == 5 and info["index"] == 0
    # Explicit values win over defaults.
    info = describe_pagination({"page_limit": 10}, _rows(5), arg_defaults={"page_limit": 5})
    assert info["limit"] == 10


@pytest.mark.unit
def test_describe_scope_ignores_page_keys_and_access_token():
    a = describe_pagination({"query": "q", "page_index": 0, "page_limit": 5, "access_token": "t1"}, [])
    b = describe_pagination({"query": "q", "page_index": 3, "page_limit": 5, "access_token": "t2"}, [])
    c = describe_pagination({"query": "other", "page_index": 0, "page_limit": 5}, [])
    assert a["scope"] == b["scope"]
    assert a["scope"] != c["scope"]
    assert "access_token" not in a["args_preview"]


@pytest.mark.unit
def test_describe_scope_is_a_fingerprint_not_the_arguments():
    """The record is persisted in timings-only mode: no argument values in scope."""
    info = describe_pagination({"query": "from:boss secret", "page_index": 0, "page_limit": 5}, [])
    assert "secret" not in info["scope"] and len(info["scope"]) == 64


@pytest.mark.unit
def test_describe_records_explicit_terminal_indicator():
    assert describe_pagination({"page_limit": 5}, {"items": _rows(5), "has_more": False})["terminal"] is True
    assert describe_pagination({"page_limit": 5}, {"items": _rows(5), "next_page": None})["terminal"] is True
    assert describe_pagination({"page_limit": 5}, {"items": _rows(5), "has_more": True})["terminal"] is False
    assert describe_pagination({"page_limit": 5}, {"items": _rows(5)})["terminal"] is False
    assert describe_pagination({"page_limit": 5}, _rows(5))["terminal"] is False


@pytest.mark.unit
def test_describe_redacts_non_page_values_when_asked():
    info = describe_pagination(
        {"query": "from:boss secret", "page_index": 0, "page_limit": 5}, [], redact_values=True
    )
    assert "secret" not in info["args_preview"]
    assert "query=…" in info["args_preview"]
    assert "page_limit=5" in info["args_preview"]


# ── audit_pagination ───────────────────────────────────────────────────────


@pytest.mark.unit
def test_full_page_without_next_page_is_flagged():
    """The f323bae_1 signature: one call, page_limit=20, 20 rows, no page 1."""
    notes = audit_pagination([_call("simple_note_search_notes", {"query": "", "page_limit": 20}, _rows(20))])
    assert len(notes) == 1
    assert "simple_note_search_notes(" in notes[0]
    assert "exactly 20 items" in notes[0]
    assert "page_index=1 was never requested" in notes[0]


@pytest.mark.unit
def test_short_page_is_not_flagged():
    notes = audit_pagination([_call("search_notes", {"query": "", "page_limit": 20}, _rows(7))])
    assert notes == []


@pytest.mark.unit
def test_empty_page_is_not_flagged():
    assert audit_pagination([_call("search_notes", {"query": "", "page_limit": 20}, [])]) == []


@pytest.mark.unit
def test_completed_pagination_is_not_flagged():
    """Full pages followed by a short page: the listing was exhausted."""
    calls = [
        _call("show_inbox", {"page_index": 0, "page_limit": 5}, _rows(5)),
        _call("show_inbox", {"page_index": 1, "page_limit": 5}, _rows(5)),
        _call("show_inbox", {"page_index": 2, "page_limit": 5}, _rows(2)),
    ]
    assert audit_pagination(calls) == []


@pytest.mark.unit
def test_advancing_past_the_full_page_is_not_flagged():
    """Page 1 requested in this block — even if its result is not list-shaped."""
    calls = [
        _call("show_inbox", {"page_index": 0, "page_limit": 5}, _rows(5)),
        _call("show_inbox", {"page_index": 1, "page_limit": 5}, {"status": "exception", "message": "x"}),
    ]
    assert audit_pagination(calls) == []


@pytest.mark.unit
def test_last_full_page_with_no_further_page_is_flagged():
    """Pages 0 and 1 both full, page 2 never requested: still incomplete."""
    calls = [
        _call("show_inbox", {"page_index": 0, "page_limit": 5}, _rows(5)),
        _call("show_inbox", {"page_index": 1, "page_limit": 5}, _rows(5)),
    ]
    notes = audit_pagination(calls)
    assert len(notes) == 1 and "page_index=2 was never requested" in notes[0]


@pytest.mark.unit
def test_repeated_identical_full_page_is_reported():
    """The 432dc7a_1 signature: the same page-0 call issued 3x, never page 1."""
    args = {"query": "from:amazon.com subject:promo", "page_index": 0, "page_limit": 5}
    calls = [_call("gmail_show_inbox_threads", args, _rows(5)) for _ in range(3)]
    notes = audit_pagination(calls)
    assert len(notes) == 1
    assert "fetched 3 times without advancing" in notes[0]


@pytest.mark.unit
def test_listings_with_different_scopes_are_independent():
    calls = [
        _call("search", {"query": "a", "page_index": 0, "page_limit": 5}, _rows(5)),
        _call("search", {"query": "a", "page_index": 1, "page_limit": 5}, _rows(1)),
        _call("search", {"query": "b", "page_index": 0, "page_limit": 5}, _rows(5)),
    ]
    notes = audit_pagination(calls)
    assert len(notes) == 1 and "query='b'" in notes[0]


@pytest.mark.unit
def test_same_tool_in_different_apps_are_independent():
    calls = [
        _call("show_orders", {"page_limit": 20}, _rows(20), app="amazon"),
        _call("show_orders", {"page_limit": 20}, _rows(3), app="shop"),
    ]
    notes = audit_pagination(calls)
    assert len(notes) == 1 and "show_orders(" in notes[0]


@pytest.mark.unit
def test_default_page_size_counts_as_a_page_size():
    """Omitted page_limit with AppWorld's default of 5 and 5 rows back: flagged."""
    calls = [_call("show_orders", {}, _rows(5), defaults={"page_index": 0, "page_limit": 5})]
    notes = audit_pagination(calls)
    assert len(notes) == 1 and "exactly 5 items" in notes[0]


@pytest.mark.unit
def test_wrapped_list_result_is_audited():
    calls = [_call("list_users", {"page": 1, "per_page": 50}, {"items": _rows(50), "total": 120})]
    notes = audit_pagination(calls)
    assert len(notes) == 1 and "page=2 was never requested" in notes[0]


@pytest.mark.unit
def test_offset_pagination_suggests_next_offset():
    calls = [_call("list_rows", {"offset": 0, "limit": 100}, _rows(100))]
    notes = audit_pagination(calls)
    assert len(notes) == 1 and "offset=100 was never requested" in notes[0]


@pytest.mark.unit
def test_explicit_last_page_marker_suppresses_the_note():
    """A full page that says has_more=false is complete — no note, no extra call."""
    calls = [_call("list_users", {"page": 1, "per_page": 50}, {"items": _rows(50), "has_more": False})]
    assert audit_pagination(calls) == []
    calls = [_call("list_users", {"page": 1, "per_page": 50}, {"items": _rows(50), "next_page": None})]
    assert audit_pagination(calls) == []
    # has_more=true is not a terminal claim: the note stands.
    calls = [_call("list_users", {"page": 1, "per_page": 50}, {"items": _rows(50), "has_more": True})]
    assert len(audit_pagination(calls)) == 1


@pytest.mark.unit
def test_skipped_page_is_flagged_even_though_a_higher_page_was_requested():
    calls = [
        _call("show_inbox", {"page_index": 0, "page_limit": 5}, _rows(5)),
        _call("show_inbox", {"page_index": 2, "page_limit": 5}, _rows(1)),  # page 1 jumped over
    ]
    notes = audit_pagination(calls)
    assert len(notes) == 1 and "page_index=1 was never requested" in notes[0]


@pytest.mark.unit
def test_skipped_offset_is_flagged_only_with_a_uniform_limit():
    calls = [
        _call("list_rows", {"offset": 0, "limit": 100}, _rows(100)),
        _call("list_rows", {"offset": 200, "limit": 100}, _rows(3)),
    ]
    notes = audit_pagination(calls)
    assert len(notes) == 1 and "offset=100 was never requested" in notes[0]
    # Mixed limits make offsets incomparable: fall back to the plain rules (short page seen → quiet).
    calls = [
        _call("list_rows", {"offset": 0, "limit": 100}, _rows(100)),
        _call("list_rows", {"offset": 200, "limit": 50}, _rows(3)),
    ]
    assert audit_pagination(calls) == []


@pytest.mark.unit
def test_cursor_pagination_is_followed_by_call_order():
    """Cursor tokens are opaque: advancing to the next token is progress, not a new listing."""
    walk = [
        _call("list_events", {"cursor": None, "limit": 50}, {"items": _rows(50), "next_cursor": "tok1"}),
        _call("list_events", {"cursor": "tok1", "limit": 50}, {"items": _rows(50), "next_cursor": "tok2"}),
        _call("list_events", {"cursor": "tok2", "limit": 50}, {"items": _rows(7), "next_cursor": None}),
    ]
    assert audit_pagination(walk) == []
    # Scope excludes the cursor: all three calls are one listing.
    assert len({c["pagination"]["scope"] for c in walk}) == 1


@pytest.mark.unit
def test_cursor_listing_left_on_a_full_page_is_flagged():
    calls = [_call("list_events", {"cursor": None, "limit": 50}, {"items": _rows(50), "next_cursor": "tok1"})]
    notes = audit_pagination(calls)
    assert len(notes) == 1 and "the next cursor was never requested" in notes[0]
    # Advanced once and stopped on another full page: still flagged, once.
    calls.append(
        _call("list_events", {"cursor": "tok1", "limit": 50}, {"items": _rows(50), "next_cursor": "tok2"})
    )
    assert len(audit_pagination(calls)) == 1


@pytest.mark.unit
def test_cursor_listing_with_explicit_end_is_quiet():
    calls = [
        _call("list_events", {"page_token": "", "page_size": 20}, {"items": _rows(20), "next_page_token": ""})
    ]
    assert audit_pagination(calls) == []


@pytest.mark.unit
def test_failed_calls_and_unpaged_calls_are_ignored():
    calls = [
        _call("show_inbox", {"page_index": 0, "page_limit": 5}, None, error="timed out"),
        {"name": "get_profile", "app_name": "gmail", "error": None, "pagination": None},
        {"name": "legacy", "app_name": "x"},  # record without the pagination key
    ]
    assert audit_pagination(calls) == []
    assert audit_pagination([]) == []
    assert audit_pagination(None) == []


@pytest.mark.unit
def test_render_notes():
    assert render_pagination_notes([]) == ""
    text = render_pagination_notes(["one", "two"])
    assert text.startswith("⚠ Pagination check:")
    assert "- one" in text and "- two" in text
    assert text.endswith(NOTE_FOOTER)


# ── tracker integration ────────────────────────────────────────────────────


@pytest.mark.unit
def test_tracker_records_pagination_facts_in_timings_only_mode():
    """Timings-only tracking keeps the (payload-free) pagination facts."""
    ToolCallTracker.start_tracking(enabled=True, timings_only=True)
    try:
        ToolCallTracker.record_call(
            tool_name="show_inbox",
            arguments={"query": "secret text", "page_index": 0, "page_limit": 5},
            result=_rows(5),
            app_name="gmail",
        )
        record = ToolCallTracker.get_current_calls()[0]
    finally:
        ToolCallTracker.stop_tracking()
    assert record["arguments"] is None and record["result"] is None
    info = record["pagination"]
    assert info["limit"] == 5 and info["result_len"] == 5
    assert "secret" not in json.dumps(info)  # neither in the preview nor in the scope
    assert audit_pagination([record])


@pytest.mark.unit
@pytest.mark.asyncio
async def test_registry_tool_passes_schema_defaults_to_the_record():
    """An omitted page_limit is recorded with its schema default (5), so a
    5-row result reads as a full page."""
    tool = create_tool_from_api_dict(
        tool_name="show_orders",
        tool_def={
            "description": "orders",
            "parameters": {
                "properties": {
                    "page_index": {"type": "integer", "default": 0},
                    "page_limit": {"type": "integer", "default": 5},
                    "query": {"type": "string"},
                },
                "required": [],
            },
        },
        app_name="amazon",
    )

    async def fake_call_api(app_name, api_name, args, operation_id=None, agent_id=None, arg_defaults=None):
        assert args == {}  # defaults are not sent on the wire
        assert arg_defaults == {"page_index": 0, "page_limit": 5}
        ToolCallTracker.record_call(
            tool_name=api_name, arguments=args, result=_rows(5), app_name=app_name, arg_defaults=arg_defaults
        )
        return _rows(5)

    ToolCallTracker.start_tracking(enabled=True)
    try:
        with patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.providers.registry.call_api",
            new=AsyncMock(side_effect=fake_call_api),
        ):
            result = await tool.coroutine({})
        assert len(result) == 5
        notes = audit_pagination(ToolCallTracker.get_current_calls())
    finally:
        ToolCallTracker.stop_tracking()
    assert len(notes) == 1 and "show_orders(" in notes[0]


# ── sandbox node gating ────────────────────────────────────────────────────


@pytest.mark.unit
def test_pagination_audit_setting_defaults_on(monkeypatch):
    from cuga.backend.cuga_graph.nodes.cuga_lite.adapter import sandbox_node

    monkeypatch.setattr(sandbox_node, "settings", SimpleNamespace(advanced_features=SimpleNamespace()))
    assert sandbox_node._pagination_audit_enabled() is True
    monkeypatch.setattr(
        sandbox_node,
        "settings",
        SimpleNamespace(advanced_features=SimpleNamespace(pagination_audit_enabled=False)),
    )
    assert sandbox_node._pagination_audit_enabled() is False


# ── sandbox node end-to-end ────────────────────────────────────────────────


def _sandbox_adapter():
    from unittest.mock import MagicMock

    adapter = MagicMock()
    adapter._tools_context = {}
    adapter._weak_schema_tool_names = frozenset()
    adapter._observed_tool_shapes = {}
    adapter._tracker = MagicMock()
    adapter.messages_key = "chat_messages"
    adapter.get_messages = MagicMock(return_value=[])
    adapter.resolve_max_steps = MagicMock(return_value=1000)
    return adapter


def _sandbox_state():
    from unittest.mock import MagicMock

    variables_manager = MagicMock()
    variables_manager.get_variable_names = MagicMock(return_value=[])
    variables_manager.get_variable = MagicMock(return_value=None)
    return SimpleNamespace(
        variables_manager=variables_manager,
        chat_messages=[],
        tool_calls=[],
        step_count=0,
        script="threads = await gmail_show_inbox_threads(page_limit=5)\nprint(len(threads))",
        thread_id="t",
        variables_storage={},
        variable_counter_state=0,
        variable_creation_order=[],
        reflection_apps=[],
        reflection_enable_find_tools=False,
        reflection_skills_enabled=False,
        reflection_skills_prompt_section="",
    )


async def _run_sandbox_block(audit_enabled: bool):
    """Run one sandbox block whose script fetches a full page-0 and stops."""
    from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.sandbox_node import create_sandbox_node

    adapter = _sandbox_adapter()

    async def fake_eval(*args, **kwargs):
        ToolCallTracker.record_call(
            tool_name="gmail_show_inbox_threads",
            arguments={"query": "from:amazon.com", "page_index": 0, "page_limit": 5},
            result=_rows(5),
            app_name="gmail",
        )
        return "5", {}

    target = "cuga.backend.cuga_graph.nodes.cuga_lite.adapter.sandbox_node."
    with (
        patch(target + "CodeExecutor.eval_with_tools_async", new=AsyncMock(side_effect=fake_eval)),
        patch(target + "settings.policy.enabled", new=False),
        patch(target + "settings.advanced_features.reflection_enabled", new=False),
        patch(target + "settings.advanced_features.pre_execute_verify_enabled", new=False),
        patch(target + "settings.advanced_features.pagination_audit_enabled", new=audit_enabled, create=True),
    ):
        node = create_sandbox_node(adapter, base_thread_id="base", base_apps_list=[])
        # No track_tool_calls in configurable: the production default.
        result = await node(_sandbox_state(), config={"configurable": {}})
    return adapter, result


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sandbox_appends_pagination_note_without_exposing_tool_calls():
    adapter, result = await _run_sandbox_block(audit_enabled=True)

    observation = result["chat_messages"][-1].content
    assert "⚠ Pagination check:" in observation
    assert "gmail_show_inbox_threads(" in observation
    assert "page_index=1 was never requested" in observation
    # The audit forced tracking on internally; that must not grow persisted state.
    assert result["tool_calls"] == []
    step_names = [call.kwargs["step"].name for call in adapter._tracker.collect_step.call_args_list]
    assert "PaginationAudit" in step_names


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sandbox_audit_can_be_disabled():
    adapter, result = await _run_sandbox_block(audit_enabled=False)

    assert "Pagination check" not in result["chat_messages"][-1].content
    step_names = [call.kwargs["step"].name for call in adapter._tracker.collect_step.call_args_list]
    assert "PaginationAudit" not in step_names
