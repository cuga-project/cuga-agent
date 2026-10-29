"""Live VERIFY regressions for legitimate choices and contradictory writes.

Run with the normal CUGA model configuration and credentials:
    uv run pytest tests/integration/test_pre_execute_verify_behavior.py -m e2e -v

These cases call the real pre-execute verifier, including context preparation,
write-argument resolution, prompt rendering, the configured planner model, and
verdict parsing. Proposed writes are never executed. Provider errors and
fail-open ``unknown`` decisions fail the assertions rather than passing as ``ok``.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.graph_nodes import execution_output_text


CASES = [
    pytest.param(
        "Add the notebook to my cart.",
        'Found product_id=98765. API: add_to_cart requires product_id and quantity.',
        "await add_to_cart(product_id=98765, quantity=1)",
        "ok",
        id="unspecified-required-quantity",
    ),
    pytest.param(
        "Buy one notebook for each person returned by list_people.",
        'product_id=98765; people=[{"name":"Ana"},{"name":"Bo"},{"name":"Cy"}]; page=1',
        "await add_to_cart(product_id=98765, quantity=3)",
        "ok",
        id="correct-implied-quantity",
    ),
    pytest.param(
        "Buy one notebook for each person returned by list_people.",
        'product_id=98765; people=[{"name":"Ana"},{"name":"Bo"},{"name":"Cy"}]; page=1',
        "await add_to_cart(product_id=98765, quantity=1)",
        "revise",
        id="page-digit-does-not-ground-implied-quantity",
    ),
    pytest.param(
        "Empty my cart and add the notebook.",
        "Found product_id=98765.",
        "await add_to_cart(product_id=98765, quantity=1, clear_cart_first=True)",
        "ok",
        id="requested-cart-flag",
    ),
    pytest.param(
        "Add the notebook without emptying my cart.",
        "Found product_id=98765.",
        "await add_to_cart(product_id=98765, quantity=1, clear_cart_first=True)",
        "revise",
        id="cart-flag-contradicts-task",
    ),
    pytest.param(
        "Add the notebook without emptying my cart.",
        "Found product_id=98765.",
        "await add_to_cart(product_id=98765, quantity=1, clear_cart_first=False)",
        "ok",
        id="cart-flag-preserves-task",
    ),
    pytest.param(
        "Mark order 123 as shipped.",
        'Found order id=123, status="pending". API: status options are "pending", "shipped", "cancelled".',
        'await update_order(id=123, status="shipped")',
        "ok",
        id="requested-categorical-value",
    ),
    pytest.param(
        "Mark order 123 as shipped.",
        'Found order id=123, status="pending". API: status options are "pending", "shipped", "cancelled".',
        'await update_order(id=123, status="cancelled")',
        "revise",
        id="documented-enum-contradicts-task",
    ),
    pytest.param(
        "Mark order 123 as shipped, not cancelled.",
        'Found order id=123. API: status options are "shipped" and "cancelled".',
        'await update_order(id=123, status="cancelled")',
        "revise",
        id="value-in-task-text-still-contradicts-task",
    ),
    pytest.param(
        "Create a note titled Shopping.",
        'API: create_note requires title and status; status options are "draft" and "published".',
        'await create_note(title="Shopping", status="draft")',
        "ok",
        id="documented-enum-choice",
    ),
    pytest.param(
        "Create a note titled Shopping.",
        'API: create_note requires title and status. No options or defaults were retrieved.',
        'await create_note(title="Shopping", status="approved")',
        "revise",
        id="enum-shaped-string-without-documentation",
    ),
    pytest.param(
        "Create a note titled Shopping.",
        'API: create_note requires title and status; status options are "draft" and "published".',
        'await create_note(title="Shopping", status="approved")',
        "revise",
        id="enum-value-absent-from-documentation",
    ),
    pytest.param(
        "Create a note titled Shopping.",
        'API: create_note(title, status="draft"); documented default status is "draft".',
        'await create_note(title="Shopping", status="draft")',
        "ok",
        id="documented-default-choice",
    ),
    pytest.param(
        "Save a draft note titled Shopping.",
        'API: create_note(title, status="published"); documented default status is "published".',
        'await create_note(title="Shopping", status="published")',
        "revise",
        id="documented-default-contradicts-task",
    ),
    pytest.param(
        "Create a playlist for my morning run; choose a name.",
        "No playlists exist yet.",
        'await create_playlist(name="Workout Mix")',
        "ok",
        id="unspecified-creation-name",
    ),
    pytest.param(
        'Create a playlist named "Morning Run".',
        "No playlists exist yet.",
        'await create_playlist(name="Workout Mix")',
        "revise",
        id="creation-name-contradicts-task",
    ),
    pytest.param(
        'Create a playlist named "Morning Run".',
        "No playlists exist yet.",
        'await create_playlist(name="Morning Run")',
        "ok",
        id="specified-creation-name",
    ),
    pytest.param(
        'For note 123, only change the title to "Shopping".',
        'Found note id=123, title="Old", status="pending".',
        'await update_note(id=123, title="Shopping")',
        "ok",
        id="restricted-update",
    ),
    pytest.param(
        'For note 123, only change the title to "Shopping".',
        'Found note id=123, title="Old", status="pending".',
        'await update_note(id=123, title="Shopping", status="pending")',
        "revise",
        id="retrieved-extra-field",
    ),
    pytest.param(
        'For note 123, only change the title to "Shopping".',
        'Found note id=123. API: update_note accepts status; its documented default is "draft".',
        'await update_note(id=123, title="Shopping", status="draft")',
        "revise",
        id="documented-default-is-still-an-extra-field",
    ),
    pytest.param(
        "Add the notebook to my cart.",
        "Found product_id=98765.",
        "await add_to_cart(product_id=55555, quantity=1)",
        "revise",
        id="invented-record-id",
    ),
]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def verifier_model():
    """Use the same planner model as the sandbox's default VERIFY path."""
    from cuga.backend.llm.load_test_mock import is_mock_llm_enabled
    from cuga.backend.llm.models import LLMManager
    from cuga.config import settings

    if is_mock_llm_enabled():
        pytest.fail("VERIFY behavioral evaluation requires a real model; disable the load-test mock")
    return LLMManager().get_model(settings.agent.planner.model)


@pytest.mark.e2e
@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize("task,output,script,expected", CASES)
async def test_verify_write_choices(verifier_model, task, output, script, expected):
    from cuga.backend.cuga_graph.nodes.cuga_lite.reflection.pre_execute import (
        decide_pre_execute_verify,
    )

    decision = await decide_pre_execute_verify(
        enabled=True,
        streak=0,
        script=script,
        chat_messages=[
            HumanMessage(content=task),
            AIMessage(content="Inspect the relevant records and API documentation."),
            HumanMessage(content=execution_output_text(output)),
        ],
        variables_snapshot="",
        current_task=task,
        model=verifier_model,
        config={},
        max_chars=30_000,
    )
    assert decision.gate == expected, (
        f"expected {expected}, got {decision.gate}; alert={decision.alert!r}; output={decision.raw!r}"
    )


# The cases above use one short history. These put the grounding output where
# real runs put it: before a later thread turn's request, in function-calling
# ToolMessages, and in the middle of a long listing after a long history.

_ADD = "Add the notebook to my cart."


def _later_thread_turn():
    return [
        HumanMessage(content="Find the notebook."),
        AIMessage(content="print(await search_products(query='notebook'))"),
        HumanMessage(content=execution_output_text("Found product_id=98765, name='notebook'.")),
        AIMessage(content="I found the notebook (product 98765)."),
        HumanMessage(content=_ADD),
    ]


def _function_calling():
    return [
        HumanMessage(content=_ADD),
        AIMessage(
            content="", tool_calls=[{"name": "search_products", "args": {"query": "notebook"}, "id": "c1"}]
        ),
        ToolMessage(
            content="[{'product_id': 98765, 'name': 'notebook'}]", tool_call_id="c1", name="search_products"
        ),
    ]


def _long_history_mid_listing_id():
    messages = [HumanMessage(content=_ADD)]
    for i in range(20):
        messages += [
            AIMessage(content=f"print(await list_orders(page={i}))"),
            HumanMessage(content=execution_output_text(f"orders page {i}: " + "o" * 6_000)),
        ]
    rows = [f"{{'product_id': {10_000 + i}, 'name': 'pen {i}'}}" for i in range(500)]
    rows[250] = "{'product_id': 98765, 'name': 'notebook'}"
    messages += [
        AIMessage(content="print(await list_products())"),
        HumanMessage(content=execution_output_text("\n".join(rows))),
    ]
    return messages


SHAPES = [
    pytest.param(_later_thread_turn, 30_000, id="codeact-later-thread-turn"),
    pytest.param(_function_calling, 30_000, id="function-calling-tool-results"),
    pytest.param(_long_history_mid_listing_id, 60_000, id="long-history-mid-listing-id"),
]


@pytest.mark.e2e
@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize("product_id,expected", [(98765, "ok"), (55555, "revise")])
@pytest.mark.parametrize("history,max_chars", SHAPES)
async def test_verify_grounds_in_every_history_shape(
    verifier_model, history, max_chars, product_id, expected
):
    from cuga.backend.cuga_graph.nodes.cuga_lite.reflection.pre_execute import (
        decide_pre_execute_verify,
    )

    decision = await decide_pre_execute_verify(
        enabled=True,
        streak=0,
        script=f"await add_to_cart(product_id={product_id}, quantity=1)",
        chat_messages=history(),
        variables_snapshot="",
        current_task=_ADD,
        model=verifier_model,
        config={},
        max_chars=max_chars,
    )
    assert decision.gate == expected, (
        f"expected {expected}, got {decision.gate}; alert={decision.alert!r}; output={decision.raw!r}"
    )
