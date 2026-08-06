import json
from collections import UserDict

import pytest
from pydantic import BaseModel

from cuga.backend.activity_tracker.tracker import (
    ActivityTracker,
    Step,
    decode_step_data,
    to_json_safe,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def isolate_activity_tracker_state():
    tracker = ActivityTracker()
    original_base_dir = tracker._base_dir
    original_experiment_folder = tracker.experiment_folder
    original_task_id = tracker.task_id
    original_steps = list(tracker.steps)

    tracker._base_dir = ""
    tracker.experiment_folder = ""
    tracker.task_id = ""
    tracker.steps = []

    try:
        yield
    finally:
        tracker._base_dir = original_base_dir
        tracker.experiment_folder = original_experiment_folder
        tracker.task_id = original_task_id
        tracker.steps = original_steps


# ---------------------------------------------------------------------------
# Step 1 – widened Step.data contract
# ---------------------------------------------------------------------------


def test_step_accepts_native_structured_data_and_preserves_json_text():
    payload = {"result": {"id": 1}, "count": 2}
    assert Step(name="structured", data=payload).data == payload
    assert Step(name="text", data='{"result": 1}').data == '{"result": 1}'


# ---------------------------------------------------------------------------
# Step 3 – to_json_safe and decode_step_data
# ---------------------------------------------------------------------------


class PayloadModel(BaseModel):
    item: dict[str, int]


class Unsupported:
    def __str__(self) -> str:
        return "unsupported-value"


def test_to_json_safe_recurses_without_mutating_source():
    source = {1: PayloadModel(item={"id": 1}), "items": (Unsupported(),)}
    assert to_json_safe(source) == {
        "1": {"item": {"id": 1}},
        "items": ["unsupported-value"],
    }
    assert isinstance(source[1], PayloadModel)


def test_to_json_safe_preserves_json_looking_string():
    assert to_json_safe('{"id": 1}') == '{"id": 1}'


def test_to_json_safe_handles_cycle_deterministically():
    value = []
    value.append(value)
    assert to_json_safe(value) == ["<cyclic reference>"]


def test_to_json_safe_normalizes_mapping_and_sequence_types():
    value = UserDict({"items": range(3)})
    assert to_json_safe(value) == {"items": [0, 1, 2]}


def test_decode_step_data_accepts_native_and_legacy_mapping():
    payload = {"id": 1}
    assert decode_step_data(payload, expected_type=dict) is payload
    assert decode_step_data('{"id": 1}', expected_type=dict) == payload


def test_decode_step_data_rejects_malformed_or_wrong_shape():
    with pytest.raises(ValueError, match="valid JSON"):
        decode_step_data("not-json", expected_type=dict)
    with pytest.raises(TypeError, match="dict"):
        decode_step_data("[1, 2]", expected_type=dict)


# ---------------------------------------------------------------------------
# Step 6 – persistence regressions
# ---------------------------------------------------------------------------


def test_to_file_writes_native_data_after_one_parse(tmp_path):
    tracker = ActivityTracker()
    tracker._base_dir = str(tmp_path)
    tracker.experiment_folder = "experiment"
    tracker.task_id = "task"
    tracker.steps = [
        Step(name="structured", data={"result": {"id": 1}}),
        Step(name="text", data='{"result": 1}'),
    ]
    tracker.to_file()
    payload = json.loads((tmp_path / "experiment" / "task.json").read_text())
    assert payload["steps"][0]["data"] == {"result": {"id": 1}}
    assert payload["steps"][1]["data"] == '{"result": 1}'


def test_to_file_writes_supported_mapping_and_sequence_values(tmp_path):
    tracker = ActivityTracker()
    tracker._base_dir = str(tmp_path)
    tracker.experiment_folder = "experiment"
    tracker.task_id = "task"
    tracker.steps = [Step(name="structured", data=UserDict({"items": range(3)}))]

    tracker.to_file()

    payload = json.loads((tmp_path / "experiment" / "task.json").read_text())
    assert payload["steps"][0]["data"] == {"items": [0, 1, 2]}


def test_to_file_external_append_writes_native_step_and_preserves_legacy(tmp_path):
    """Seed file has a legacy string step; appending a native dict step works."""
    full_path = str(tmp_path / "recording.json")

    # Seed the file with a legacy string-data step
    seed_data = {
        "intent": "",
        "dataset_name": "",
        "actions_count": 0,
        "task_id": "t1",
        "eval": None,
        "steps": [
            {
                "name": "legacy",
                "data": '{"result": 1}',
                "plan": "",
                "prompts": [],
                "task_decomposition": "",
                "current_url": "",
                "action_formatted": "",
                "action_type": "",
                "action_args": "",
                "observation_before": "",
                "image_before": "",
            }
        ],
        "score": 0.0,
    }
    with open(full_path, "w", encoding="utf-8") as fh:
        json.dump(seed_data, fh)

    tracker = ActivityTracker()
    tracker.task_id = "t1"
    native_step = Step(name="native", data={"result": {"id": 1}})
    tracker._to_file_external_append(full_path, native_step)

    result = json.loads(open(full_path, encoding="utf-8").read())
    assert result["steps"][0]["data"] == '{"result": 1}'  # legacy unchanged
    assert result["steps"][1]["data"] == {"result": {"id": 1}}  # native dict
