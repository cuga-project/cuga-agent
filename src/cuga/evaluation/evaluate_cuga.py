from cuga.backend.activity_tracker.tracker import ActivityTracker
from cuga.backend.cuga_graph.utils.controller import AgentRunner, ExperimentResult
from cuga.config import settings
from cuga.evaluation.agentic_quality_judge import (
    JudgeConfig,
    build_judge,
    enable_tool_call_tracking,
    extract_tool_calls_with_results,
    fetch_tool_catalog,
    score_agentic_quality,
)
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


def _run_test_case_hook(hook_file: Optional[str], test_case_name: Optional[str]) -> None:
    """Write the about-to-run (or, with None, the just-finished) test case's
    name to `hook_file`, if set. Lets an external test double (e.g. a mock
    MCP server replaying recorded fixtures) scope its own per-call lookups to
    THIS test case instead of falling through to whichever test case's data
    happens to be recorded first for a given tool -- a plain file rather than
    an HTTP call, since a mock registered as a real MCP server only serves
    the MCP/SSE protocol on its port, not arbitrary side-channel routes.
    Best-effort and non-fatal: a run should proceed even if the path isn't
    writable, just without test-case-scoped replay."""
    if not hook_file:
        return
    try:
        Path(hook_file).write_text(test_case_name or "", encoding="utf-8")
    except OSError as e:
        print(f"  WARNING: could not write test-case hook file {hook_file!r}: {e}")


def _corrected_tool_calls_for_scoring(agentic_tool_calls) -> List[ToolCall]:
    """Rebuild the actual-call ToolCall list from extract_tool_calls_with_results()'s
    tracking-based output, for tool_call_score's exact-match comparison --
    replaces the broken "api_call"-step-based extraction in parse_test_results()
    (CugaLite's fast-execution path doesn't emit steps named that way, so that
    extraction always finds zero calls; confirmed directly against a call that
    genuinely succeeded). Strips the f"{app_name}_" prefix CUGA's registry adds
    to callable names (e.g. "petstore_getpetbyid"), since
    expected_output.tool_calls uses bare OpenAPI operationIds
    ("getPetById") -- score_tool_calls_exact only sanitizes (lowercases)
    the expected side, so the actual side must already be in that bare form
    to have any chance of matching."""
    calls = []
    for c in agentic_tool_calls:
        name = c.get("tool", "")
        app_name = c.get("app_name", "")
        if app_name and name.startswith(app_name + "_"):
            name = name[len(app_name) + 1 :]
        calls.append(ToolCall(name=name, args=c.get("arguments") or {}))
    return calls


async def run_cuga(
    test_file_path: str,
    result_file_path: str,
    compute_agentic_quality: bool = False,
    test_case_hook_file: Optional[str] = None,
    judge_config: Optional[JudgeConfig] = None,
) -> (List[TestCase], List[ExperimentResult]):
    # Resolve and health-check the judge backend before running any test case,
    # so a bad --judge-url fails the run immediately instead of after every
    # agent task has already run (and every judge label has come back "NA").
    judge_model = None
    if compute_agentic_quality:
        judge_model = build_judge(judge_config or JudgeConfig.from_env())
        print(f"agentic_quality judge: {judge_model.model_name} @ {judge_model.openai_api_base}")

    test_cases = parse_test_cases(test_file_path)
    print(f"test cases: {len(test_cases)}\napps: {list(test_cases.keys())}")

    # Must happen before any AgentRunner is constructed -- it monkeypatches
    # AgentLoop.__init__ (the class, once, process-wide) so every graph
    # invocation carries real tool-call results, which score_agentic_quality's
    # judge needs (the "api_call" tracker steps used below for tool_call_score
    # carry name+args only).
    if compute_agentic_quality:
        enable_tool_call_tracking()

    # Legacy default (compute_agentic_quality=False): one AgentRunner shared
    # across every task, exactly as upstream always did -- untouched, so
    # `cuga evaluate` without --agentic-quality behaves identically to before
    # this flag existed. Only under --agentic-quality do we give each task
    # its own AgentRunner (see per-task construction below) -- shared-runner
    # reuse across a long run was observed to make the SAME test case's tool
    # calls degrade (e.g. dropped arguments) depending on how many prior
    # tasks had already run through it, even though each task already gets
    # a fresh compiled graph/checkpointer either way. Root cause unconfirmed;
    # giving --agentic-quality runs a fresh AgentRunner + unique thread_id
    # per task removes the shared-identity surface regardless of the exact
    # mechanism. Scoped to --agentic-quality only so the default path's
    # behavior/cost is never affected by this.
    shared_agent_runner = None if compute_agentic_quality else AgentRunner(browser_enabled=False)

    tool_catalog = ""
    if compute_agentic_quality:
        # Registry is already up by the time `cuga evaluate` invokes this
        # script (see cli/main.py::evaluate) -- fetch the tool catalog once,
        # it's the same for every test case in this run.
        tool_catalog = await fetch_tool_catalog(settings.server_ports.registry)

    results = []
    for app in test_cases:
        task_ids = [f"{app}_{str(i)}" for i in enumerate(test_cases[app])]
        tracker.start_experiment(task_ids=task_ids, experiment_name=app, description="")
        for i, task in enumerate(test_cases[app]):
            try:
                agent_runner = shared_agent_runner or AgentRunner(
                    browser_enabled=False, thread_id=f"{app}_{i}"
                )
                _run_test_case_hook(test_case_hook_file, task.name)
                tracker.reset(intent=task.intent, task_id=f"{app}_{str(i)}")
                result = await agent_runner.run_task_generic(
                    eval_mode=False, goal=task.intent, current_datetime=tracker.current_date
                )
                # Reset variables after task completion using the current state
                state = agent_runner.get_current_state()
                agentic_tool_calls = extract_tool_calls_with_results(state) if compute_agentic_quality else []
                if os.environ.get("CUGA_EVAL_DEBUG_AGENTIC"):
                    print(f"DEBUG[{task.name}] raw state.tool_calls = {state.tool_calls!r}")
                    print(f"DEBUG[{task.name}] extracted agentic_tool_calls = {agentic_tool_calls!r}")
                state.variables_manager.reset()
                results.append(result)
                parsed_results = parse_test_results([task], [result])
                if compute_agentic_quality:
                    corrected_tool_calls = _corrected_tool_calls_for_scoring(agentic_tool_calls)
                    corrected_score, corrected_details = evaluate_test_and_details(
                        task.expected_output.keywords,
                        corrected_tool_calls,
                        task.expected_output.tool_calls,
                        result.answer or "",
                        task.expected_output.response,
                    )
                    parsed_results[0].score.tool_call_score = corrected_score.tool_call_score
                    parsed_results[0].details.tool_call_mismatches = corrected_details.tool_call_mismatches
                    if os.environ.get("CUGA_EVAL_DEBUG_AGENTIC"):
                        print(
                            f"DEBUG[{task.name}] corrected tool_call_score = {corrected_score.tool_call_score!r}, "
                            f"mismatches = {corrected_details.tool_call_mismatches!r}"
                        )
                    judge_results, _aggregate = score_agentic_quality(
                        judge_model,
                        [
                            {
                                "question": task.intent,
                                "tool_calls": agentic_tool_calls,
                                "answer": result.answer or "",
                            }
                        ],
                        tool_catalog,
                    )
                    if os.environ.get("CUGA_EVAL_DEBUG_AGENTIC"):
                        print(f"DEBUG[{task.name}] judge_results = {judge_results!r}")
                    parsed_results[0].score.tool_selection_quality = judge_results[0].get(
                        "tool_selection_quality"
                    )
                    parsed_results[0].score.action_advancement = judge_results[0].get("action_advancement")
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
    _run_test_case_hook(test_case_hook_file, None)
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
                # Only populated when `cuga evaluate --agentic-quality` ran the judge;
                # None otherwise (see TestScore in calculate_test_score.py). The full
                # per-step judge output (reasons, per-step verdicts) is JSON-only --
                # too verbose for a CSV column, kept here as just the top-level label.
                "tool_selection_quality_label": (r.score.tool_selection_quality or {}).get("label"),
                "action_advancement_label": (r.score.action_advancement or {}).get("label"),
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

    settings.update({"ADVANCED_FEATURES": {"TRACKER_ENABLED": True}}, merge=True)
    parser = argparse.ArgumentParser(description="Run tests and save results.")
    parser.add_argument("-t", "--test-file-path", required=True, help="Path to the test file")
    parser.add_argument("-r", "--result-file-path", required=True, help="Path to the result file")
    parser.add_argument(
        "-a",
        "--agentic-quality",
        action="store_true",
        help="Also score tool_selection_quality/action_advancement with an LLM judge "
        "over real tool-call trajectories (adds judge-model latency/cost per test case).",
    )
    parser.add_argument(
        "--test-case-hook-file",
        default=None,
        help="Path to write the current test case's name to before each task, and clear "
        "afterward -- lets an external test double (e.g. a mock MCP server) scope its own "
        "replay to this test case. Omit for no hook.",
    )

    parser.add_argument(
        "--judge-url",
        default=None,
        help="OpenAI-compatible API root of the agentic_quality judge, used as-is (vLLM, OpenAI, "
        "LiteLLM, Ollama, ..., e.g. http://vllm-judge:8000/v1). Defaults to $CUGA_JUDGE_BASE_URL. "
        "Separate from CUGA's agent model.",
    )
    parser.add_argument(
        "--judge-model",
        default=None,
        help="Judge model name. Defaults to $CUGA_JUDGE_MODEL, else the first model the server lists.",
    )

    args = parser.parse_args()
    judge_config = (
        JudgeConfig.from_env(base_url=args.judge_url, model=args.judge_model)
        if args.agentic_quality
        else None
    )
    tasks, results = asyncio.run(
        run_cuga(
            args.test_file_path,
            args.result_file_path,
            compute_agentic_quality=args.agentic_quality,
            test_case_hook_file=args.test_case_hook_file,
            judge_config=judge_config,
        )
    )
