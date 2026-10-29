"""Pre-execute VERIFY prompt scope, context sections, and the blocked-rewrite message."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.graph_nodes import (
    REFLECTION_SUMMARY_SEPARATOR,
    execution_output_text,
)


# ── VERIFY rule 1 scope ────────────────────────────────────────────────────
#
# Rule 1 was written for invented facts (an id or amount the agent made up) but
# read as "any literal". On AppWorld hard it revised quantity=1,
# clear_cart_first=True, snooze_minutes=0 and a regex pattern — choices and code
# constants, not claims about the world — and the model complied, which cost
# two tasks outright (a 7-item order, a dropped repeat_days).


def _verify_prompt_text() -> str:
    """The system prompt with whitespace collapsed, so a re-wrapped line is not a failure."""
    from pathlib import Path

    text = (
        Path(__file__).resolve().parents[1] / "reflection" / "prompts" / "verify_system.jinja2"
    ).read_text(encoding="utf-8")
    return " ".join(text.split())


def _rendered_user_prompt(**fields) -> str:
    from cuga.backend.cuga_graph.nodes.cuga_lite.reflection.verify import verify_prompt

    inputs = {
        "current_task": "task",
        "agent_history": "",
        "latest_output": "",
        "variables_snapshot": "",
        "proposed_code": "",
        "write_arguments": "",
        **fields,
    }
    return verify_prompt().format_messages(**inputs)[-1].content


async def _captured_verify_inputs(messages, script, *, max_chars=30_000, task="task"):
    from cuga.backend.cuga_graph.nodes.cuga_lite.reflection.pre_execute import decide_pre_execute_verify

    captured = {}
    chain = MagicMock()

    async def _ainvoke(payload, config=None):
        captured.update(payload)
        return SimpleNamespace(content="GATE: ok")

    chain.ainvoke = _ainvoke
    with patch(
        "cuga.backend.cuga_graph.nodes.cuga_lite.reflection.pre_execute.verify_task",
        return_value=chain,
    ):
        await decide_pre_execute_verify(
            enabled=True,
            streak=0,
            script=script,
            chat_messages=messages,
            variables_snapshot="",
            current_task=task,
            model=MagicMock(spec=[]),
            config={},
            max_chars=max_chars,
        )
    return captured


@pytest.mark.unit
def test_rule_one_is_scoped_to_identifying_or_quantitative_values():
    text = _verify_prompt_text()
    assert "identifies a record or" in text
    assert "states a quantity" in text


@pytest.mark.unit
def test_choices_and_code_constants_are_listed_as_do_not_flag():
    text = _verify_prompt_text().split("Do NOT flag:", 1)[1]
    for phrase in ("booleans and flags", "documented default", "regular expressions"):
        assert phrase in text, f"missing carve-out: {phrase}"


@pytest.mark.unit
def test_latest_output_grounds_values_of_the_same_kind_only():
    rule_one = _verify_prompt_text().split("2. Contradictions", 1)[0]
    assert "the Most recent execution output" in rule_one
    # A bare digit such as "page": 1 must not ground quantity=1 for a task that
    # implies a different count.
    assert "does not ground a count the Current Task" in rule_one


@pytest.mark.unit
def test_user_prompt_presents_the_latest_output_as_what_the_last_block_printed():
    rendered = _rendered_user_prompt(latest_output="product_id=98765")
    assert "**Most recent execution output** (what the last block printed" in rendered
    assert "product_id=98765" in rendered


@pytest.mark.unit
def test_user_prompt_claims_no_output_when_no_block_ran():
    rendered = _rendered_user_prompt(latest_output="")
    assert "what the last block printed" not in rendered
    assert "no block has run yet" in rendered


# Latest output keeps its own section within the shared VERIFY context budget,
# even when earlier history is trimmed.


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verify_keeps_the_latest_output_when_history_is_trimmed():
    from cuga.backend.cuga_graph.nodes.cuga_lite.reflection.pre_execute import (
        decide_pre_execute_verify,
    )

    captured = {}
    chain = MagicMock()

    async def _ainvoke(payload, config=None):
        captured.update(payload)
        return SimpleNamespace(content="GATE: ok")

    chain.ainvoke = _ainvoke
    messages = [HumanMessage(content="order the cheapest pad")]
    for i in range(20):
        messages += [
            AIMessage(content=f"block {i} " + "x" * 50),
            HumanMessage(content=execution_output_text(f"out {i} " + "y" * 50)),
        ]
    messages += [
        AIMessage(content="search"),
        HumanMessage(content=execution_output_text("found product_id 98765")),
    ]
    with patch(
        "cuga.backend.cuga_graph.nodes.cuga_lite.reflection.pre_execute.verify_task",
        return_value=chain,
    ):
        await decide_pre_execute_verify(
            enabled=True,
            streak=0,
            script="await add_to_cart(product_id=98765)",
            chat_messages=messages,
            variables_snapshot="",
            current_task="order the cheapest pad",
            model=MagicMock(spec=[]),
            config={},
            max_chars=300,
        )
    assert "omitted" in captured["agent_history"]
    assert "98765" in captured["latest_output"]
    assert "98765" not in captured["agent_history"]
    assert (
        sum(
            len(captured[key])
            for key in ("agent_history", "latest_output", "variables_snapshot", "proposed_code")
        )
        <= 300
    )


@pytest.mark.unit
def test_verify_context_has_no_latest_output_before_any_block_ran():
    from cuga.backend.cuga_graph.utils.context_management_utils import prepare_verify_context

    history, latest, _, _ = prepare_verify_context(
        [HumanMessage(content="order the cheapest pad")], "", "", max_chars=1000
    )
    assert "order the cheapest pad" in history
    assert latest == ""


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verify_sees_function_calling_results_as_the_latest_output():
    messages = [
        HumanMessage(content="Add the notebook to my cart"),
        AIMessage(content="", tool_calls=[{"name": "search", "args": {"q": "notebook"}, "id": "c1"}]),
        ToolMessage(content="product_id=98765", tool_call_id="c1", name="search"),
    ]

    captured = await _captured_verify_inputs(messages, "await add_to_cart(product_id=98765)")

    assert "product_id=98765" in captured["latest_output"]
    assert "product_id=98765" not in captured["agent_history"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_blocked_function_calling_batch_is_not_the_latest_output():
    """A revised batch never ran. Its deferred reply is not output, so the earlier result stays latest."""
    from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.fc_actions import DEFERRED_CALL_MESSAGE
    from cuga.backend.cuga_graph.nodes.cuga_lite.reflection.pre_execute import verify_blocked_message

    blocked = verify_blocked_message("product_id=55555 is ungrounded")
    messages = [
        HumanMessage(content="Add the notebook to my cart"),
        AIMessage(content="", tool_calls=[{"name": "search", "args": {"q": "notebook"}, "id": "c1"}]),
        ToolMessage(content="product_id=98765", tool_call_id="c1", name="search"),
        AIMessage(
            content="",
            tool_calls=[
                {"name": "add_to_cart", "args": {"product_id": 55555}, "id": "c2"},
                {"name": "get_cart", "args": {}, "id": "c3"},
            ],
        ),
        ToolMessage(content=blocked, tool_call_id="c2", name="add_to_cart", status="error"),
        ToolMessage(content=DEFERRED_CALL_MESSAGE, tool_call_id="c3", name="get_cart", status="error"),
    ]

    captured = await _captured_verify_inputs(messages, "await add_to_cart(product_id=98765)")

    assert "product_id=98765" in captured["latest_output"]
    assert "Deferred" not in captured["latest_output"]
    assert "Deferred" not in captured["agent_history"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_later_user_request_stays_in_history_not_in_latest_output():
    messages = [
        HumanMessage(content="Find the song Blue"),
        AIMessage(content="print(await search(q='Blue'))"),
        HumanMessage(content=execution_output_text("song_id=42")),
        AIMessage(content="Found Blue (42)."),
        HumanMessage(content="Great, add it to my cart and email the receipt to bob@example.com."),
    ]

    captured = await _captured_verify_inputs(messages, "await add_to_cart(song_id=42)")

    assert captured["latest_output"] == "song_id=42"
    assert "bob@example.com" in captured["agent_history"]
    assert "what the last block printed" in _rendered_user_prompt(**captured)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_reflection_summary_is_not_shown_as_what_the_last_block_printed():
    """With reflection on, sandbox_node appends a model-written summary to the output."""
    messages = [
        HumanMessage(content="Add the notebook to my cart"),
        AIMessage(content="print(await get_cart())"),
        HumanMessage(
            content=execution_output_text("cart_id=11")
            + REFLECTION_SUMMARY_SEPARATOR
            + "The cart is cart_id=99; add the notebook to it."
        ),
    ]

    captured = await _captured_verify_inputs(messages, "await add_to_cart(cart_id=99, product_id=98765)")

    assert captured["latest_output"] == "cart_id=11"
    assert "cart_id=99" not in captured["agent_history"]


# Judged against ground truth, 883 recorded revises in the categories this
# change touches held four correct catches in tasks that then passed. Three
# came from excusing "a name or path the agent is creating": an unrequested
# "Test Playlist", an unrequested report file (each breaks a model-change
# assertion) and ./backups where the task named ~/backups. The fourth was an
# invented zip code, which the narrowed rule 1 no longer named.


@pytest.mark.unit
def test_a_name_or_path_the_task_specifies_or_never_asked_for_stays_in_scope():
    text = _verify_prompt_text()
    assert "a name or path the agent is creating" not in text
    carve_outs = text.split("Do NOT flag:", 1)[1]
    assert "task *does* specify must" in carve_outs
    assert "an object the task never asked for" in carve_outs
    rule_one = text.split("2. Contradictions", 1)[0]
    for kind in ("a path", "an address"):
        assert kind in rule_one, f"rule 1 must name {kind}"


@pytest.mark.unit
def test_verify_blocked_message_limits_the_rewrite_to_the_flagged_value():
    from cuga.backend.cuga_graph.nodes.cuga_lite.reflection.pre_execute import (
        verify_blocked_message,
    )

    # The blanket "ground each write argument" made the model drop needed flags
    # (clear_cart_first, repeat_days) while fixing an unrelated value.
    msg = verify_blocked_message("quantity=1 is ungrounded")
    assert "each write argument is grounded" not in msg
    assert "quantity=1 is ungrounded" in msg
    assert "Fix the flagged value" in msg
    assert "flags and defaults" in msg


@pytest.mark.unit
def test_verify_blocked_message_says_to_remove_a_flagged_extra_field():
    """Rule 4 flags a field the task did not ask to change; grounding it again cannot fix that."""
    from cuga.backend.cuga_graph.nodes.cuga_lite.reflection.pre_execute import (
        verify_blocked_message,
    )

    msg = verify_blocked_message('status="pending" is an extra field; the task only changes the title')
    assert "remove the flagged field" in msg
    assert "come from retrieved data" not in msg


@pytest.mark.unit
def test_verify_blocked_message_without_an_alert_still_says_what_to_check():
    from cuga.backend.cuga_graph.nodes.cuga_lite.reflection.pre_execute import (
        VERIFY_BLOCKED_PREFIX,
        verify_blocked_message,
    )

    msg = verify_blocked_message("")
    assert msg.startswith(VERIFY_BLOCKED_PREFIX)
    assert "did not name" in msg
    assert "Current Task" in msg and "field" in msg
    assert "flags and defaults" in msg


@pytest.mark.unit
def test_alert_format_lets_verify_name_an_extra_field():
    output_format = _verify_prompt_text().split("Output EXACTLY in this format", 1)[1]
    assert "extra field" in output_format


@pytest.mark.unit
def test_rule_one_explains_the_marker_for_a_value_bound_from_a_call():
    rule_one = _verify_prompt_text().split("2. Contradictions", 1)[0]
    assert "from the result of <call> earlier in this block" in rule_one
    assert "not another call" in rule_one


@pytest.mark.unit
def test_extra_field_rule_requires_an_explicit_restriction():
    text = _verify_prompt_text()
    assert "explicitly restricts which" in text
    assert "it does not authorize writing an extra field" in text


# Rule 1 names "an amount" while the Do NOT flag list excludes choices, and
# quantity=1 satisfies both. Measured across 1,234 real rule-1 revises, the
# model resolved that conflict by flagging: 148 revises over 53 tasks named a
# quantity, the largest false-revise family. A blanket carve-out would be wrong
# too -- quantity=12 derived from "9 friends + 3 roommates" is a correct catch --
# so the prompt has to separate a number the task implies from one it does not.


@pytest.mark.unit
def test_an_unspecified_quantity_is_a_choice_not_a_fact():
    text = _verify_prompt_text()
    assert "neither states nor implies" in text
    assert "quantity=1" in text


@pytest.mark.unit
def test_a_quantity_the_task_implies_stays_in_scope():
    carve_outs = _verify_prompt_text().split("Do NOT flag:", 1)[1]
    assert "does* imply stays in scope" in carve_outs
    assert "count of people, items, or shares" in carve_outs
