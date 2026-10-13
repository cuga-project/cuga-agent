"""The agentic_quality LLM judge: prompts built from a trajectory, labels parsed
from the judge's reply, and labels aggregated into per-run scores."""

import json
import threading

import pytest

from cuga.evaluation.agentic_quality_judge.scoring import (
    _strip_code_fence,
    extract_tool_results,
    get_metrics,
    score_agentic_quality,
)

pytestmark = pytest.mark.unit


class _FakeJudge:
    """Answers like a chat model: one canned reply per judge, chosen by system prompt."""

    def __init__(self, tool_selection="Good", action_advancement="Advanced", raw=None, error=None):
        self.replies = {
            "tool-selection evaluator": raw
            or json.dumps({"label": tool_selection, "reason": "r", "steps": []}),
            "goal-advancement evaluator": raw or json.dumps({"label": action_advancement, "reason": "r"}),
        }
        self.error = error
        self.prompts = []
        self._lock = threading.Lock()

    def invoke(self, messages):
        (_, system), (_, user) = messages
        with self._lock:
            self.prompts.append((system, user))
        if self.error:
            raise self.error
        return next(reply for marker, reply in self.replies.items() if marker in system)


def _record(question="Get account 1", tool_calls=None, answer="Account 1 is Acme."):
    if tool_calls is None:
        tool_calls = [{"tool": "crm_get_account", "arguments": {"id": 1}, "result": {"name": "Acme"}}]
    return {"question": question, "tool_calls": tool_calls, "answer": answer}


def test_scores_each_record_with_both_judges():
    judge = _FakeJudge(tool_selection="Poor", action_advancement="Partially Advanced")

    per_record, aggregate = score_agentic_quality(judge, [_record()], tool_catalog="# Available Tools")

    assert per_record[0]["tool_selection_quality"]["label"] == "Poor"
    assert per_record[0]["action_advancement"]["label"] == "Partially Advanced"
    assert aggregate == {"tool_selection_quality": 0.0, "action_advancement": 0.5}

    by_judge = {("tool-selection" in system): user for system, user in judge.prompts}
    tool_selection_prompt, action_advancement_prompt = by_judge[True], by_judge[False]
    assert "Get account 1" in tool_selection_prompt
    assert "# Available Tools" in tool_selection_prompt
    assert "crm_get_account" in tool_selection_prompt
    assert '{"name": "Acme"}' in action_advancement_prompt
    assert "Account 1 is Acme." in action_advancement_prompt


def test_code_fenced_reply_is_parsed():
    judge = _FakeJudge(raw='```json\n{"label": "Good", "reason": "r", "steps": []}\n```')
    per_record, _ = score_agentic_quality(judge, [_record()])
    assert per_record[0]["tool_selection_quality"]["label"] == "Good"


@pytest.mark.parametrize(
    "judge, records, reason",
    [
        (_FakeJudge(), [_record(tool_calls=[])], "No tool calls"),
        (_FakeJudge(raw="not json"), [_record()], "Error in parsing response"),
        (_FakeJudge(error=TimeoutError("judge timed out")), [_record()], "TimeoutError: judge timed out"),
    ],
)
def test_unscorable_records_are_labeled_na_and_excluded(judge, records, reason):
    per_record, aggregate = score_agentic_quality(judge, records)

    for metric in ("tool_selection_quality", "action_advancement"):
        assert per_record[0][metric]["label"] == "NA"
        assert reason in per_record[0][metric]["reason"]
    assert aggregate == {"tool_selection_quality": None, "action_advancement": None}


def test_no_records():
    assert score_agentic_quality(_FakeJudge(), []) == (
        [],
        {"tool_selection_quality": None, "action_advancement": None},
    )


def test_get_metrics_averages_known_labels_only():
    results = [
        {"tool_selection_quality": {"label": "Good"}, "action_advancement": {"label": "Advanced"}},
        {"tool_selection_quality": {"label": "poor"}, "action_advancement": {"label": "Did Not Advance"}},
        {"tool_selection_quality": {"label": "NA"}, "action_advancement": {"label": "Partially Advanced"}},
    ]
    assert get_metrics(results) == {"tool_selection_quality": 0.5, "action_advancement": 0.5}


def test_extract_tool_results():
    assert extract_tool_results([{"tool": "a", "result": 1}, {"tool": "b"}, "junk"]) == [1]
    assert extract_tool_results({"result": 2}) == [2]
    assert extract_tool_results({"a": {"result": 3}, "b": [{"result": 4}]}) == [3, 4]


def test_strip_code_fence():
    assert _strip_code_fence('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert _strip_code_fence('{"a": 1}') == '{"a": 1}'
