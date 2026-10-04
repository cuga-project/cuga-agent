from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Literal, Mapping, Optional, Sequence, Tuple

# -----------------------------
# General helpers
# -----------------------------


def _ensure_list(x: Any) -> List[Any]:
    return x if isinstance(x, list) else ([] if x is None else [x])


def read_domain_file(path: Path) -> List[Dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"Expected list at {path}, got {type(data)}")
    return data


def pair_dialogues_by_uuid(
    gt_list: List[Dict[str, Any]],
    pred_list: List[Dict[str, Any]],
) -> Tuple[List[Tuple[Dict[str, Any], Dict[str, Any]]], List[str], List[str]]:
    gt_map = {str(d.get("uuid")): d for d in gt_list if d.get("uuid") is not None}
    pr_map = {str(d.get("uuid")): d for d in pred_list if d.get("uuid") is not None}

    missing_pred = sorted([u for u in gt_map.keys() if u not in pr_map])
    extra_pred = sorted([u for u in pr_map.keys() if u not in gt_map])

    paired: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    for u in gt_map.keys():
        if u in pr_map:
            paired.append((gt_map[u], pr_map[u]))
    return paired, missing_pred, extra_pred


# -----------------------------
# Data model
# -----------------------------

Role = Literal["system", "user", "assistant", "tool"]


@dataclass(frozen=True)
class Turn:
    role: Role
    content: str


@dataclass(frozen=True)
class ToolCall:
    tool_name: str
    arguments: Dict[str, Any]
    output: Optional[Any] = None

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> "ToolCall":
        if "name" not in d:
            raise ValueError(f"ToolCall missing 'tool_name': {d}")
        return ToolCall(
            tool_name=str(d["name"]),
            arguments=dict(d.get("arguments", {})),
            output=d.get("output"),
        )


@dataclass(frozen=True)
class Example:
    id: str
    capability: str
    domain: str
    turns: List[Turn]
    trajectory: List[ToolCall]
    final_answer: str

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> "Example":
        if "id" not in d:
            raise ValueError(f"Example missing 'id': {d}")
        turns_raw = d.get("turns", [])
        turns: List[Turn] = []
        for t in turns_raw:
            if "role" not in t or "content" not in t:
                raise ValueError(f"Turn missing role/content: {t}")
            turns.append(Turn(role=t["role"], content=str(t["content"])))

        traj_raw = d.get("tool_trajectory", [])
        traj = [ToolCall.from_dict(x) for x in traj_raw]

        return Example(
            id=str(d["id"]),
            capability=str(d.get("capability", "")),
            domain=str(d.get("domain", "")),
            turns=turns,
            trajectory=traj,
            final_answer=str(d.get("final_answer", "")),
        )


@dataclass(frozen=True)
class PairedExample:
    """Ground truth + prediction matched by example ID."""

    gt: Example
    pred: Example


# -----------------------------
# Metrics / results
# -----------------------------


@dataclass
class ScalarMetric:
    name: str
    value: float
    count: int
    details: Optional[Dict[str, Any]] = None


@dataclass
class ExampleResult:
    example_id: str
    answer_score: Optional[float]
    answer_label: Optional[str]
    trajectory_score: Optional[float]
    trajectory_details: Dict[str, Any]
    meta: Dict[str, Any]


@dataclass
class EvalSliceResult:
    capability: str
    domain: str
    n_examples_gt: int
    n_examples_pred: int
    n_paired: int
    missing_pred_ids: List[str]
    extra_pred_ids: List[str]
    metrics: List[ScalarMetric]
    per_example: List[ExampleResult]


@dataclass
class EvalReport:
    benchmark_name: str
    slices: List[EvalSliceResult]
    aggregated: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


# -----------------------------
# LLM Judge for correctness and groundedness
# -----------------------------


@dataclass(frozen=True)
class JudgeInput:
    capability: str
    domain: str
    query: str
    additional_instructions: str
    gt_answer: str
    pred_answer: str
    gt_tool_calls: Sequence[ToolCall]
    pred_tool_calls: Sequence[ToolCall]
    gt_tool_responses: List[str]
    pred_tool_responses: List[str]


@dataclass(frozen=True)
class JudgeOutput:
    # score should be normalized either 1 or 0
    score: float
    explanation: Optional[str] = None
