# This process drives the agent graph directly via AgentRunner (not through
# cuga.sdk), so nothing else here would otherwise trigger init_traceloop()
# before a graph call happens. Mirrors main.py's own import-time init.
import cuga.backend.observability.traceloop_init as _traceloop_init  # noqa: F401

from cuga.backend.activity_tracker.tracker import ActivityTracker
from cuga.backend.cuga_graph.utils.controller import AgentRunner, ExperimentResult
from cuga.evaluation.langfuse.get_langfuse_data import LangfuseTraceHandler

from loguru import logger
import traceback
from pydantic import BaseModel
from typing import List, Dict, Iterable, Any, Optional
import json
import csv
from calculate_test_score import evaluate_test_and_details, TestScore, TestScoreDetails, ToolCall
from statistics import mean
from pathlib import Path
import os

tracker = ActivityTracker()


class ExpectedOutput(BaseModel):
    """
    The expected output a test case
    """

    response: str
    keywords: List[str]
    tool_calls: List[ToolCall]


class TestCase(BaseModel):
    """
    This is the model for your test cases, i.e. the input you give the evaluation loop
    """

    app: str
    name: str
    description: str
    intent: str
    expected_output: ExpectedOutput


class TestResult(BaseModel):
    """
    The evaluation loop output of a run on a single test case
    """

    app: str
    index: int
    test_name: str
    score: TestScore
    details: TestScoreDetails


def dict_subset_with_reason(sup: Dict, sub: Dict, path="") -> List[str]:
    """Return list of reasons why sub is not a subset of sup."""
    reasons = []
    for k, v in sub.items():
        if k not in sup:
            reasons.append(f"Missing key '{path + k}'")
        else:
            sv = sup[k]
            if isinstance(v, dict) and isinstance(sv, dict):
                reasons.extend(dict_subset_with_reason(sv, v, path + k + "."))
            elif sv != v:
                reasons.append(f"Value mismatch at '{path + k}': expected {v}, got {sv}")
    return reasons


def compare_toolcalls(a_list: Iterable[ToolCall], b_list: Iterable[ToolCall]) -> List[str]:
    all_reasons = []
    for a in a_list:
        matched = False
        for b in b_list:
            if b.name in a.name:
                reasons = dict_subset_with_reason(a.args, b.args)
                if not reasons:  # perfect match
                    matched = True
                    break
        if not matched:
            if not any(b.name in a.name for b in b_list):
                all_reasons.append(f"No ToolCall in B has name substring matching '{a.name}'")
            else:
                all_reasons.append(f"Args mismatch for ToolCall '{a.name}'")
                for b in b_list:
                    if b.name in a.name:
                        mismatch = dict_subset_with_reason(a.args, b.args)
                        if mismatch:
                            all_reasons.extend([f"  vs B({b.name}): {r}" for r in mismatch])
    return all_reasons


def parse_test_cases(json_file_path: str) -> dict[Any, list[Any]]:
    """Parse JSON test cases into TestCase objects."""

    # Resolve path: use absolute paths as-is, resolve relative paths from user's terminal location
    path = Path(json_file_path)
    if not path.is_absolute():
        path = Path.cwd() / path

    with open(path, 'r') as f:
        data = json.load(f)

    test_cases = {}
    for app in data:
        for test_case_data in app['test_cases']:
            # Extract user input as intent (first user input)
            intent = test_case_data['intent'] if test_case_data['intent'] else ""

            # Parse tool calls
            tool_calls = [
                ToolCall(name=call['name'], args=call['args'])
                for call in test_case_data['expected_output']['tool_calls']
            ]

            # Parse expected output
            expected_output = ExpectedOutput(
                response=test_case_data['expected_output']['response'],
                keywords=test_case_data['expected_output']['keywords'],
                tool_calls=tool_calls,
            )

            # Create TestCase object
            test_case = TestCase(
                app=app['name'],
                name=test_case_data['name'],
                description=test_case_data['description'],
                intent=intent,
                expected_output=expected_output,
            )
            if app['name'] not in test_cases:
                test_cases[app['name']] = []
            test_cases[app['name']].append(test_case)

    return test_cases


async def run_cuga(test_file_path: str, result_file_path: str) -> (List[TestCase], List[ExperimentResult]):
    test_cases = parse_test_cases(test_file_path)
    print(f"test cases: {len(test_cases)}\napps: {list(test_cases.keys())}")
    agent_runner = AgentRunner(browser_enabled=False)
    results = []
    for app in test_cases:
        task_ids = [f"{app}_{str(i)}" for i in enumerate(test_cases[app])]
        tracker.start_experiment(task_ids=task_ids, experiment_name=app, description="")
        for i, task in enumerate(test_cases[app]):
            try:
                tracker.reset(intent=task.intent, task_id=f"{app}_{str(i)}")
                result = await agent_runner.run_task_generic(
                    eval_mode=False, goal=task.intent, current_datetime=tracker.current_date
                )
                # Reset variables after task completion using the current state
                state = agent_runner.get_current_state()
                state.variables_manager.reset()
                results.append(result)
                parsed_results = parse_test_results([task], [result])
                save_test_results(parsed_results, result_file_path)
                # Extract langfuse trace ID (applicable only if `langfuse_tracing=true` in settings)
                langfuse_trace_id = agent_runner.agent_loop_obj.get_langfuse_trace_id()
                langfuse_handler = LangfuseTraceHandler(langfuse_trace_id)
                langfuse_data = await langfuse_handler.get_langfuse_data()
                tracker.finish_task(
                    intent=task.intent,
                    site="",
                    task_id=f"{app}_{str(i)}",
                    eval="",
                    score=mean(
                        [
                            parsed_results[0].score.keyword_score,
                            parsed_results[0].score.response_score,
                            parsed_results[0].score.tool_call_score,
                        ]
                    ),
                    agent_answer=result.answer,
                    exception=False,
                    agent_v="",
                    total_llm_calls=langfuse_data.total_llm_calls if langfuse_data else None,
                    total_tokens=langfuse_data.total_tokens if langfuse_data else None,
                    total_cost=langfuse_data.total_cost if langfuse_data else None,
                    total_cache_input_tokens=langfuse_data.total_cache_input_tokens
                    if langfuse_data
                    else None,
                )
            except Exception as e:
                results.append(ExperimentResult(answer=f"Error {e}", score=0, messages=[], steps=[]))
                tracker.finish_task(
                    intent=task.intent,
                    site="",
                    task_id=f"{app}_{str(i)}",
                    eval="",
                    score=0,
                    agent_answer=f"Error: {e}",
                    exception=True,
                    agent_v="",
                )
                logger.error(traceback.format_exc())
                logger.error(e)
    return test_cases, results


def parse_test_results(
    test_cases: List[TestCase], experiment_results: List[ExperimentResult]
) -> List[TestResult]:
    if len(test_cases) != len(experiment_results):
        raise ValueError(f"Mismatch: {len(test_cases)} test cases vs {len(experiment_results)} results")

    results = []

    for i, (test_case, experiment_result) in enumerate(zip(test_cases, experiment_results)):
        # Get answer text (handle None case)
        answer = experiment_result.answer or ""

        keywords = test_case.expected_output.keywords
        expected_tools = [tool for tool in test_case.expected_output.tool_calls]
        tool_calls = []
        for call in [step for step in experiment_result.steps if "api_call" in step.name]:
            call_json = json.loads(call.data)
            tool_calls.append(ToolCall(name=call_json['function_name'], args=call_json['args']))
        test_score, test_score_details = evaluate_test_and_details(
            keywords, tool_calls, expected_tools, answer, test_case.expected_output.response
        )

        result = TestResult(
            app=test_case.app,
            index=i,
            test_name=test_case.name,
            score=test_score,
            details=test_score_details,
        )

        results.append(result)

    return results


def save_test_results(
    results: List["TestResult"],
    json_path: str = "test_results.json",
    csv_path: Optional[str] = None,
) -> None:
    """
    Save test results to JSON (as a list) and CSV (append rows, no duplicate headers).
    """
    if csv_path is None:
        csv_path = json_path[:-5] + ".csv" if json_path.endswith(".json") else json_path + ".csv"

    # ---- JSON ----
    # Load existing results (list), append, then overwrite
    if os.path.exists(json_path) and os.path.getsize(json_path) > 0:
        with open(json_path, "r", encoding="utf-8") as f:
            try:
                existing = json.load(f)
                if not isinstance(existing, list):
                    existing = []
            except json.JSONDecodeError:
                existing = []
    else:
        existing = []

    existing.extend(r.model_dump() for r in results)

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2, ensure_ascii=False)

    # ---- CSV ----
    def j(obj):
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))

    rows = []
    for r in results:
        rows.append(
            {
                "app": r.app,
                "index": r.index,
                "test_name": r.test_name,
                "keyword_score": r.score.keyword_score,
                "tool_call_score": r.score.tool_call_score,
                "response_score": r.score.response_score,
                "expected_keywords": j(r.details.expected_keywords),
                "missing_keywords": j(r.details.missing_keywords),
                "tool_call_mismatches": j([m.model_dump() for m in r.details.tool_call_mismatches]),
                "response_expected": r.details.response_expected,
                "response_actual": r.details.response_actual,
            }
        )

    write_header = not os.path.exists(csv_path) or os.path.getsize(csv_path) == 0

    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        if write_header:
            writer.writeheader()
        writer.writerows(rows)

    print(f"Saved {len(results)} results → JSON: {json_path} | CSV: {csv_path}")


if __name__ == "__main__":
    import asyncio
    import argparse
    from cuga.config import settings

    settings.update({"ADVANCED_FEATURES": {"TRACKER_ENABLED": True}}, merge=True)
    parser = argparse.ArgumentParser(description="Run tests and save results.")
    parser.add_argument("-t", "--test-file-path", required=True, help="Path to the test file")
    parser.add_argument("-r", "--result-file-path", required=True, help="Path to the result file")

    args = parser.parse_args()
    tasks, results = asyncio.run(run_cuga(args.test_file_path, args.result_file_path))
