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
from langchain_core.messages import AIMessage, HumanMessage


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
            HumanMessage(content=output),
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
