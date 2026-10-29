import asyncio
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.graph_nodes import execution_output_text
from cuga.backend.cuga_graph.utils.context_management_utils import (
    DEFAULT_CONTEXT_FRACTION,
    context_fraction_setting,
    history_text_keep_recent,
    messages_to_history_text,
    model_input_budget_chars,
    prepare_reflection_context,
    prepare_verify_context,
    split_latest_execution_output,
    truncate_head_tail,
    truncate_text_for_context,
    truncate_variables_summary,
)

pytestmark = pytest.mark.unit


def _long_run(steps: int = 7, output_chars: int = 6000) -> list:
    """A task, ``steps`` code/output pairs, then a final block whose output is the newest message."""
    msgs = [HumanMessage(content="Send $59 to Jose via Venmo")]
    for i in range(1, steps + 1):
        msgs.append(AIMessage(content=f"step_{i}()"))
        msgs.append(HumanMessage(content=execution_output_text(f"step {i}: " + "x" * output_chars)))
    msgs.append(AIMessage(content="send_money()"))
    msgs.append(HumanMessage(content=execution_output_text("FINAL: {'message': 'Sent money.'}")))
    return msgs


class _FakeModel:
    def __init__(self, name, profile=None, max_tokens=None, params=None):
        self.model_name = name
        self.profile = profile
        self.max_tokens = max_tokens
        if params is not None:
            self.params = params


def test_truncate_text_for_context_noop_when_short():
    text = "hello"
    assert truncate_text_for_context(text, 100) == "hello"


def test_truncate_text_for_context_adds_marker():
    result = truncate_text_for_context("x" * 200, 100, label="Execution output")
    assert result.startswith("x")
    assert "[Execution output trimmed to 100 chars]" in result
    assert len(result) <= 100


def test_messages_to_history_text_formats_roles():
    messages = [
        HumanMessage(content="hi"),
        AIMessage(content="hello"),
    ]
    text = messages_to_history_text(messages)
    assert "User: hi" in text
    assert "Assistant: hello" in text


def test_log_and_track_metrics_records_failure_even_with_empty_error_message():
    """Empty str(exception) must still be detected as a failure shape (issue #563 review)."""
    from unittest.mock import Mock

    from cuga.backend.cuga_graph.utils.context_management_utils import _log_and_track_metrics

    failure_metrics = {
        "error": "",  # str(Exception()) is empty
        "fallback": "kept recent messages only",
        "hard_truncation": True,
        "messages_dropped": 5,
        "messages_kept": 10,
    }
    temp_state = Mock()
    temp_state.last_summarization_metrics = {"chat_messages": failure_metrics}
    tracker = Mock()

    _log_and_track_metrics([Mock()] * 15, [Mock()] * 10, temp_state, tracker)

    tracker.collect_step.assert_called_once()
    step = tracker.collect_step.call_args[0][0]
    assert step.name == "ContextSummarizationFailure"
    assert '"hard_truncation": true' in step.data


# ── history: keep the newest messages (#1001) ─────────────────────────────────


def test_history_keep_recent_returns_full_text_when_it_fits():
    msgs = _long_run(steps=2, output_chars=100)
    assert history_text_keep_recent(msgs, 100_000) == messages_to_history_text(msgs)


def test_history_keep_recent_keeps_task_and_newest_messages():
    msgs = _long_run()  # ~42k chars; the old head cut at 30k dropped FINAL
    history = history_text_keep_recent(msgs, 30_000)

    assert len(history) <= 30_000
    assert history.startswith("User: Send $59 to Jose via Venmo")
    assert "FINAL" in history
    assert "send_money()" in history
    assert "step 1:" not in history, "the oldest steps are the ones dropped"
    assert "earlier messages omitted to fit the context budget]" in history


def test_history_keep_recent_counts_omitted_messages():
    msgs = _long_run()
    history = history_text_keep_recent(msgs, 30_000)
    kept_steps = sum(1 for i in range(1, 8) if f"step {i}: x" in history)
    omitted = 2 * (7 - kept_steps)  # each dropped step is a code and an output message
    assert f"[{omitted} earlier messages omitted to fit the context budget]" in history


def test_history_keep_recent_cuts_oversized_newest_message_head_and_tail():
    msgs = [
        HumanMessage(content="task"),
        AIMessage(content="old()"),
        HumanMessage(content="START " + "y" * 50_000 + " TRACEBACK-END"),
    ]
    history = history_text_keep_recent(msgs, 5_000)

    assert len(history) <= 5_000
    assert "User: task" in history
    assert "START" in history and "TRACEBACK-END" in history
    assert "chars of latest message omitted" in history


# ── single long texts: keep head and tail ─────────────────────────────────────


def test_truncate_head_tail_keeps_traceback_at_end():
    text = "rows: " + "r" * 20_000 + "\nTraceback: HTTP 409 user does not exist"
    out = truncate_head_tail(text, 3_000, label="Execution output")

    assert len(out) <= 3_000
    assert out.startswith("rows: ")
    assert out.endswith("HTTP 409 user does not exist")
    assert "chars of Execution output omitted" in out


def test_truncate_head_tail_noop_when_short():
    assert truncate_head_tail("  short  ", 100) == "short"


# ── variables: drop whole variables from the end ──────────────────────────────


def test_truncate_variables_summary_keeps_whole_leading_variables():
    sections = [f"## var_{i}\n- Value Preview: {'v' * 900}\n" for i in range(1, 9)]
    summary = "# Variables Summary\n\n" + "".join(sections)

    out = truncate_variables_summary(summary, 3_000)

    assert len(out) <= 3_000
    assert "## var_1\n" in out and "## var_2\n" in out
    assert "## var_8" not in out
    assert out.count("- Value Preview:") == out.count("## var_"), "no variable is cut in half"
    assert "more variables omitted to fit the context budget]" in out


# ── budget from the model's context window ────────────────────────────────────


def test_model_input_budget_follows_context_window():
    small = _FakeModel("unit-test-32k-model", profile={"max_input_tokens": 32_000}, max_tokens=4_000)
    large = _FakeModel("gpt-oss-120b", max_tokens=16_000)  # 131,072 tokens in MODEL_CONTEXT_SIZES

    # (32,000 × 0.70 − 4,000 completion) × 3 chars per token
    assert model_input_budget_chars(small, 0, context_fraction=0.70) == 55_200
    # (131,072 × 0.70 − 16,000) × 3 − 20,000 fixed prompt chars
    assert model_input_budget_chars(large, 20_000, context_fraction=0.70) == 207_250


def test_model_input_budget_reserves_the_completion_limit_sent_to_the_provider():
    from langchain_openai import ChatOpenAI

    model = ChatOpenAI(
        model="unit-test-32k-model",
        api_key="test",
        max_tokens=16_000,
        profile={"max_input_tokens": 32_000},
    )
    budget = model_input_budget_chars(model, 0, context_fraction=1.0)
    payload = model._get_request_payload([HumanMessage(content="test")])
    completion = payload.get("max_completion_tokens", payload.get("max_tokens"))
    assert completion == 16_000
    assert budget == 48_000
    assert budget // 3 + completion == 32_000


def test_model_input_budget_uses_bound_completion_kwargs_before_model_fields():
    from langchain_openai import ChatOpenAI

    model = ChatOpenAI(
        model="unit-test-32k-model",
        api_key="test",
        max_tokens=1_000,
        profile={"max_input_tokens": 32_000},
    ).bind(max_tokens=6_000)
    assert model_input_budget_chars(model, 0, context_fraction=1.0) == 78_000


def test_model_input_budget_covers_conflicting_bound_completion_aliases():
    from langchain_openai import ChatOpenAI

    model = (
        ChatOpenAI(
            model="unit-test-32k-model",
            api_key="test",
            max_tokens=1_000,
            profile={"max_input_tokens": 32_000},
        )
        .bind(max_tokens=6_000)
        .bind(max_completion_tokens=2_000)
    )
    payload = model.bound._get_request_payload([HumanMessage(content="test")], **model.kwargs)
    assert payload["max_completion_tokens"] == 6_000
    budget = model_input_budget_chars(model, 0, context_fraction=1.0)
    assert budget // 3 + payload["max_completion_tokens"] <= 32_000


def test_model_input_budget_reads_watsonx_style_params():
    model = _FakeModel(
        "unit-test-32k-model", profile={"max_input_tokens": 32_000}, params={"max_new_tokens": 2_000}
    )
    assert model_input_budget_chars(model, 0, context_fraction=0.70) == (22_400 - 2_000) * 3


def test_model_input_budget_unknown_model_uses_default_context():
    unknown = _FakeModel("no-such-model-anywhere", max_tokens=16_000)
    known = _FakeModel("gpt-oss-120b", max_tokens=16_000)
    assert model_input_budget_chars(unknown, 0, context_fraction=0.70) == model_input_budget_chars(
        known, 0, context_fraction=0.70
    )


def test_model_input_budget_nameless_model_uses_default_context_not_gpt4():
    from unittest.mock import MagicMock

    nameless = MagicMock(spec=[])
    known = _FakeModel("gpt-oss-120b")  # 131,072 tokens, same as DEFAULT_CONTEXT_SIZE
    assert model_input_budget_chars(nameless, 0, context_fraction=0.70) == model_input_budget_chars(
        known, 0, context_fraction=0.70
    )


def test_model_input_budget_lower_fraction_gives_smaller_budget():
    model = _FakeModel("gpt-oss-120b", max_tokens=16_000)
    assert model_input_budget_chars(model, 0, context_fraction=0.35) < model_input_budget_chars(
        model, 0, context_fraction=0.70
    )


def test_model_input_budget_does_not_allocate_more_than_remaining_space():
    tiny = _FakeModel("unit-test-4k-model", profile={"max_input_tokens": 4_000}, max_tokens=1_000)
    assert model_input_budget_chars(tiny, 8_500, context_fraction=1.0) == 500


def test_model_input_budget_rejects_fixed_text_and_completion_that_leave_no_space():
    tiny = _FakeModel("unit-test-4k-model", profile={"max_input_tokens": 4_000}, max_tokens=1_000)
    with pytest.raises(ValueError, match="No context space remains"):
        model_input_budget_chars(tiny, 8_500, context_fraction=0.70)


@pytest.mark.parametrize("budget", [0, 1, 10, 100, 250])
def test_truncation_and_shared_fields_obey_even_tiny_budgets(budget):
    messages = [HumanMessage(content="task"), AIMessage(content="x" * 1_000)]
    variables = "# Variables\n\n## first\n" + "v" * 1_000
    assert len(truncate_text_for_context("s" * 1_000, budget)) <= budget
    assert len(truncate_head_tail("s" * 1_000, budget)) <= budget
    assert len(truncate_variables_summary(variables, budget)) <= budget
    assert len(history_text_keep_recent(messages, budget)) <= budget
    fields = prepare_verify_context(messages, variables, "code" * 1_000, max_chars=budget)
    assert sum(map(len, fields)) <= budget


def test_empty_history_retains_the_sentinel_within_its_budget():
    sentinel = "No previous conversation history"
    assert history_text_keep_recent([], 100) == sentinel
    assert history_text_keep_recent([], 1) == sentinel[:1]
    assert history_text_keep_recent([], 0) == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("budget", [0, 1, 100, 500])
async def test_reflection_fields_share_a_strict_budget_including_markers(budget):
    from unittest.mock import AsyncMock

    messages = [HumanMessage(content="task"), AIMessage(content="x" * 1_000)]
    with patch(
        "cuga.backend.cuga_graph.utils.context_management_utils.apply_context_summarization",
        AsyncMock(return_value=messages),
    ):
        fields = await prepare_reflection_context(
            messages, "output" * 1_000, None, budget_chars=budget, skills_prompt_section="skill" * 1_000
        )
    assert sum(map(len, fields)) <= budget


def test_rendered_prompt_fallback_is_visible_at_warning_level():
    from unittest.mock import Mock
    from cuga.backend.cuga_graph.utils.context_management_utils import rendered_prompt_chars

    prompt = Mock()
    prompt.format_messages.side_effect = KeyError("new_template_variable")
    with patch("cuga.backend.cuga_graph.utils.context_management_utils.logger.warning") as warning:
        assert rendered_prompt_chars(prompt, {"current_task": "task"}) == 4
    warning.assert_called_once()


@pytest.mark.parametrize("value", [0, -0.5, 1.5, "abc", True, float("nan")])
def test_context_fraction_setting_falls_back_on_invalid_values(value):
    with patch("cuga.config.settings.advanced_features.reflection_context_fraction", value, create=True):
        assert context_fraction_setting("reflection_context_fraction") == DEFAULT_CONTEXT_FRACTION


def test_context_fraction_setting_reads_valid_value():
    with patch("cuga.config.settings.advanced_features.reflection_context_fraction", 0.4, create=True):
        assert context_fraction_setting("reflection_context_fraction") == 0.4


# ── prepare_*: one budget shared by the fields ────────────────────────────────


def test_prepare_reflection_context_keeps_newest_history_beyond_30k_chars():
    """Regression for #1001: at 30,000 chars the head cut kept step 1-4 and dropped FINAL."""
    msgs = _long_run()
    history, output, skills = asyncio.run(
        prepare_reflection_context(msgs, "{'message': 'Sent money.'}", None, budget_chars=200_000)
    )
    assert history == messages_to_history_text(msgs), "the whole run fits a 128K-window budget"
    assert output == "{'message': 'Sent money.'}"
    assert skills == ""


def test_prepare_reflection_context_gives_unused_output_share_to_history():
    msgs = _long_run()
    history, _output, _skills = asyncio.run(prepare_reflection_context(msgs, "ok", None, budget_chars=20_000))
    assert len(history) > 0.9 * 20_000, "output and skills are tiny, so history gets nearly all of it"
    assert "FINAL" in history


def test_prepare_reflection_context_cuts_large_output_to_its_share():
    output = "head " + "o" * 50_000 + " ERROR-AT-END"
    _history, kept, _skills = asyncio.run(
        prepare_reflection_context(_long_run(steps=1), output, None, budget_chars=10_000)
    )
    assert len(kept) <= 4_000
    assert kept.startswith("head ") and kept.endswith("ERROR-AT-END")


def test_prepare_verify_context_keeps_newest_history_and_leading_variables():
    sections = [f"## var_{i}\n- Value Preview: {'v' * 900}\n" for i in range(1, 9)]
    snapshot = "# Variables Summary\n\n" + "".join(sections)

    history, latest, variables, code = prepare_verify_context(
        _long_run(), snapshot, "await pay(amount=59)", max_chars=10_000
    )

    assert "FINAL" in latest
    assert "FINAL" not in history
    assert "step 7: x" in history
    assert "## var_1\n" in variables and "## var_8" not in variables
    assert code == "await pay(amount=59)"
    assert len(history) + len(latest) + len(variables) + len(code) <= 10_000


@pytest.mark.parametrize("budget", [0, 1, 10, 100, 250, 1_000, 10_000])
def test_verify_latest_output_shares_budget_and_keeps_result_tail(budget):
    messages = _long_run()
    messages[-1] = HumanMessage(content=execution_output_text("START " + "r" * 20_000 + " RESULT-AT-END"))
    snapshot = "# Variables Summary\n\n## rows\n- Value Preview: " + "v" * 20_000
    code = "# START\n" + "# c\n" * 5_000 + "await pay(amount=59)"

    history, latest, variables, proposed = prepare_verify_context(messages, snapshot, code, max_chars=budget)

    assert sum(map(len, (history, latest, variables, proposed))) <= budget
    if budget >= 1_000:
        assert latest.startswith("START ")
        assert latest.endswith("RESULT-AT-END")
        assert "RESULT-AT-END" not in history
        assert "chars of Most recent execution output omitted" in latest


def test_verify_latest_output_keeps_a_mid_output_id_when_history_is_short():
    """A fixed 25% output share cut this 19k listing to 7,500 chars and lost the id."""
    rows = "\n".join(f"{{'song_id': {1000 + i}, 'title': 'Song {i}'}}" for i in range(500))
    listing = rows.replace("'song_id': 1250,", "'song_id': 98765,")
    assert 15_000 < len(listing) < 25_000 and listing.index("98765") > len(listing) // 3
    messages = [
        HumanMessage(content="Add the song with id 98765 to my playlist"),
        AIMessage(content="print(await list_songs())"),
        HumanMessage(content=execution_output_text(listing)),
    ]

    history, latest, _variables, _code = prepare_verify_context(
        messages, "", "await add_song(song_id=98765)", max_chars=30_000
    )

    assert latest == listing
    assert "Add the song with id 98765" in history


def test_verify_long_history_keeps_its_floor_when_the_output_is_huge():
    messages = _long_run()
    messages[-1] = HumanMessage(content=execution_output_text("START " + "r" * 100_000 + " RESULT-AT-END"))

    history, latest, variables, code = prepare_verify_context(
        messages, "", "await pay(amount=59)", max_chars=30_000
    )

    assert history.startswith("User: Send $59 to Jose via Venmo")
    assert "send_money()" in history
    assert latest.startswith("START ") and latest.endswith("RESULT-AT-END")
    # The output takes the rest, but leaves history a fifth of the budget.
    assert 0.6 * 30_000 < len(latest) <= 30_000 - len(code) - len(variables) - 0.2 * 30_000
    assert sum(map(len, (history, latest, variables, code))) <= 30_000


# ── split_latest_execution_output: which message is the latest output ─────────


def test_split_takes_the_latest_codeact_output_not_a_later_user_request():
    """On a later thread turn the new request must stay in history, not pose as output."""
    messages = [
        HumanMessage(content="Find the song Blue"),
        AIMessage(content="print(await search(q='Blue'))"),
        HumanMessage(content=execution_output_text("song_id=42")),
        AIMessage(content="Found Blue (42)."),
        HumanMessage(content="Great, add it to my cart and email the receipt to bob@example.com."),
    ]

    earlier, latest = split_latest_execution_output(messages)

    assert latest == "song_id=42"
    assert earlier == messages[:2] + messages[3:]


def test_split_keeps_a_retry_correction_in_history():
    correction = HumanMessage(content="Your previous reply was empty. Reply with code or an answer.")
    messages = [
        HumanMessage(content="task"),
        AIMessage(content="print(1)"),
        HumanMessage(content=execution_output_text("id=7")),
        AIMessage(content=""),
        correction,
    ]

    earlier, latest = split_latest_execution_output(messages)

    assert latest == "id=7"
    assert earlier[-1] is correction


def test_split_takes_the_trailing_tool_message_group_in_function_calling_mode():
    messages = [
        HumanMessage(content="Add the notebook to my cart"),
        AIMessage(content="", tool_calls=[{"name": "search", "args": {"q": "notebook"}, "id": "c1"}]),
        ToolMessage(content="old result", tool_call_id="c1", name="search"),
        AIMessage(
            content="",
            tool_calls=[
                {"name": "search", "args": {"q": "notebook"}, "id": "c2"},
                {"name": "get_cart", "args": {}, "id": "c3"},
            ],
        ),
        ToolMessage(content="product_id=98765", tool_call_id="c2", name="search"),
        ToolMessage(content="404 no cart", tool_call_id="c3", name="get_cart", status="error"),
    ]

    earlier, latest = split_latest_execution_output(messages)

    assert "product_id=98765" in latest and "search" in latest
    assert "404 no cart" in latest and "get_cart" in latest
    assert "old result" not in latest
    assert earlier == messages[:4]


def test_split_finds_no_output_before_any_block_ran():
    messages = [HumanMessage(content="task"), AIMessage(content="thinking"), HumanMessage(content="go on")]
    assert split_latest_execution_output(messages) == (messages, "")
