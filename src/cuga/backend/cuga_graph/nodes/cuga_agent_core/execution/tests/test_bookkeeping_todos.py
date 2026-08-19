"""Bookkeeping-todo filter (issue #676, defect 3).

Self-referential plan items ("Confirm operation completed", "Provide summary to user")
are never part of the user's task, and discharging them has produced real damage:
07bb666_1 lost its only failing assertion to two file_system_create_file calls made to
"persist a summary" of the agent's own progress; 6474048_1 repeated the pattern.

The positive cases below are actual item texts harvested from three AppWorld bundles
(Aug 6 easy, Aug 17 med, Aug 18/19 fixed-branch runs). The negative cases are actual
*legitimate* plan items from the same bundles — each one burned by an earlier,
too-greedy draft of the pattern — plus user-requested artifact phrasings.
"""

from __future__ import annotations

import pytest

from cuga.backend.cuga_graph.nodes.cuga_agent_core.execution.todos import (
    create_update_todos_tool,
    extract_task_todos_from_new_vars,
    is_bookkeeping_todo,
    split_bookkeeping_todos,
)

BOOKKEEPING = [
    # confirm family — every leading-"Confirm" item observed was bookkeeping
    "Confirm operation completed",
    "Confirm completion",
    "Confirm order placement",
    "Confirm order placement and provide summary",
    "Confirm purchase and provide summary to user",
    "Confirm request sent and report outcome",
    "Confirm deletion and report",
    "Confirm deletion completed",
    "Confirm all scheduled emails have been sent",
    "Confirm playlist creation and provide summary",
    "Confirm acceptance and finish",
    "Confirm labeling completed",
    "Confirm order details and provide summary to user",
    # summarize / report family
    "Summarize actions",
    "Summarize actions taken",
    "Report completion",
    "Report back",
    "Report deletion summary",
    # final-answer / summary-artifact family
    "Generate summary report",
    "Prepare final answer",
    "Compose final answer for the user",
    "Return final answer",
    "Provide summary to user",
    # finalize / record family
    "Finalize and report",
    "Record actions taken",
    "Persist the results summary",
]

LEGITIMATE = [
    # real work items from the same bundles
    "List markdown files in ~/documents/personal/notes/",
    "Fetch all items in Amazon cart",
    "Check sender numbers against contacts",
    "Finalize actions (accept or delete)",
    "Search each song on Spotify to get track IDs",
    "Retrieve song list from Simple Note",
    # verification that IS the task (pre-action checks)
    "Confirm cart contents before purchase",
    "Verify the transfer amount matches the invoice",
    # user-requested artifacts — a named destination defeats the artifact patterns
    "Write summary to Simple Note",
    "Create a summary report in ~/documents/work/",
    "Summarize the article",
    "Send summary email to my boss",
    # ordinary uses of matched verbs
    "Record a voice memo for the meeting",
    "Save the playlist as Random Songs",
    "Report the outage via the support form",
]


@pytest.mark.unit
@pytest.mark.parametrize("text", BOOKKEEPING)
def test_bookkeeping_items_are_detected(text):
    assert is_bookkeeping_todo(text) is True


@pytest.mark.unit
@pytest.mark.parametrize("text", LEGITIMATE)
def test_legitimate_items_are_kept(text):
    assert is_bookkeeping_todo(text) is False


@pytest.mark.unit
def test_leading_numbering_is_ignored():
    assert is_bookkeeping_todo("3. Confirm completion") is True
    assert is_bookkeeping_todo(" - Report back") is True


@pytest.mark.unit
def test_empty_and_none_are_kept():
    assert is_bookkeeping_todo("") is False
    assert is_bookkeeping_todo(None) is False


@pytest.mark.unit
def test_split_preserves_order_and_partitions():
    items = [
        {"text": "Fetch all items in Amazon cart", "status": "pending"},
        {"text": "Confirm operation completed", "status": "pending"},
        {"text": "Move low-rated items", "status": "pending"},
    ]
    kept, dropped = split_bookkeeping_todos(items)
    assert [i["text"] for i in kept] == ["Fetch all items in Amazon cart", "Move low-rated items"]
    assert [i["text"] for i in dropped] == ["Confirm operation completed"]


# --- tool-level behavior -------------------------------------------------------------

PLAN = [
    {"text": "Fetch all items in Amazon cart", "status": "pending"},
    {"text": "Filter items with rating < 4.2", "status": "pending"},
    {"text": "Confirm operation completed", "status": "pending"},
]


async def _call_tool(todos, **factory_kwargs):
    store: list = []
    tool = await create_update_todos_tool(todos_store_ref=store, **factory_kwargs)
    # StructuredTool.from_function(func=async_fn) stores the async fn in .func and
    # leaves .coroutine None — same fallback prepare_node uses in production.
    fn = tool.coroutine or tool.func
    result = await fn(todos=todos)
    return result, store


@pytest.mark.unit
@pytest.mark.asyncio
async def test_tool_drops_bookkeeping_from_store_and_output():
    result, store = await _call_tool(PLAN)

    stored_texts = [i["text"] for i in store]
    assert "Confirm operation completed" not in stored_texts
    assert len(store) == 2
    assert [t.text for t in result.todos] == stored_texts
    assert "Confirm operation completed" in result.note
    assert "Do not re-add" in result.note


@pytest.mark.unit
@pytest.mark.asyncio
async def test_tool_note_is_none_when_nothing_dropped():
    result, store = await _call_tool(PLAN[:2])
    assert result.note is None
    assert len(store) == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_all_bookkeeping_plan_stores_empty_with_note():
    result, store = await _call_tool([{"text": "Confirm completion", "status": "pending"}])
    assert store == []
    assert result.todos == []
    assert "Dropped 1 bookkeeping item" in result.note


@pytest.mark.unit
@pytest.mark.asyncio
async def test_filter_can_be_disabled():
    result, store = await _call_tool(PLAN, filter_bookkeeping=False)
    assert len(store) == 3
    assert result.note is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_write_todos_callback_receives_filtered_list():
    """The sandbox node's call counter and the supervisor's run-state store both hang off
    write_todos — they must see the same filtered list the prompt will render."""
    seen = []
    store: list = []
    tool = await create_update_todos_tool(todos_store_ref=store, write_todos=seen.append)
    await (tool.coroutine or tool.func)(todos=PLAN)
    assert seen == [store]
    assert len(seen[0]) == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_filtered_output_still_parses_as_todos_payload():
    """make_tool_awaitable model_dumps the output; extract_task_todos_from_new_vars must
    keep recognizing it (the reflection-skip and staleness stamp depend on this)."""
    result, _ = await _call_tool(PLAN)
    payload = extract_task_todos_from_new_vars({"todos": result.model_dump()})
    assert [i["text"] for i in payload] == [
        "Fetch all items in Amazon cart",
        "Filter items with rating < 4.2",
    ]
