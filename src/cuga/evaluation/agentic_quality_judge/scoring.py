"""LLM-judge scoring for CUGA agent trajectories: tool_selection_quality and
action_advancement. The judge LLM is any OpenAI-compatible backend (e.g. a
remote vLLM service), deliberately separate from CUGA's own backbone model --
callers build it with judge_client.build_judge() and pass it in. Any
object with a LangChain-style invoke(messages) works.
"""

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import few_shots

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def _strip_code_fence(text):
    """Strip a leading/trailing ```json ... ``` markdown fence some chat
    models (e.g. Gemma) wrap JSON replies in, before json.loads()."""
    if not isinstance(text, str):
        return text
    return _CODE_FENCE_RE.sub("", text.strip()).strip()


_TOOL_SELECTION_QUALITY_SYSTEM_TEMPLATE = """You are a tool-selection evaluator in an agentic setting. Given a user goal, the available tool descriptions, and the agent's trajectory of tool calls, judge whether each tool call was correct, then give an overall verdict.

## What makes a single tool call "Correct"
A tool call is "Correct" only if ALL of these hold:
- Right tool: the most appropriate available tool was chosen; no clearly better-suited tool was left unused.
- Right arguments: every argument is accurate and appropriately derived from the user goal or a prior step's output.
- No missing arguments: all required arguments are present (none omitted or left as placeholders).
- No hallucinated arguments: every value is traceable to the user's request or a prior tool output. A value that appears from nowhere is hallucinated.
- Correct sequencing: the call happens at the right point — no step runs before a prerequisite step has produced the data it depends on.

If a call violates any of these, it is "Incorrect".

## Overall label (derived strictly from the steps)
- "Good": EVERY step has verdict "Correct".
- "Poor": at least one step has verdict "Incorrect".
Derive the label mechanically from the step verdicts. If every step is Correct, the label MUST be "Good" — do not output "Poor" when all steps are Correct. Do not factor in trajectory completeness or any criterion beyond the per-step verdicts.

## Important notes
- Evaluate each step independently, then derive the overall label by the rule above. Do not invent a score.
- The top-level "reason" must summarize the overall verdict: when Poor, name the specific failing step and which condition it violated (wrong tool, wrong/missing/hallucinated argument, or sequencing); when Good, confirm that all steps passed.
- A correct tool with wrong or hallucinated arguments is still Incorrect.
- A wrong tool that happens to return useful output is still Incorrect.
- A more roundabout tool chosen when a simpler, directly-suited tool was available is Incorrect.

## Examples

{examples}

## Output Format
Output ONLY a JSON object, nothing else:
{{{{"label": "Good" | "Poor", "reason": "<one sentence: if Poor, name the specific step and condition that failed; if Good, state that all steps passed>", "steps": [{{{{"step": <number>, "tool": "<tool_name>", "verdict": "Correct" | "Incorrect", "reason": "<one sentence naming the condition that passed or failed>"}}}}]}}}}"""


def get_tool_selection_quality(model, QUESTION, TOOL_CALLS, TOOL_CATALOG):
    system_prompt = _TOOL_SELECTION_QUALITY_SYSTEM_TEMPLATE.format(
        examples=few_shots.TOOL_SELECTION_QUALITY_EXAMPLES
    )

    input_prompt = """

## Input

<user_goal>
{QUESTION}
</user_goal>

<available_tools>
{TOOL_CATALOG}
</available_tools>


<trajectory>
{TOOL_CALLS}
</trajectory>
        """

    tool_calls_str = TOOL_CALLS if isinstance(TOOL_CALLS, str) else json.dumps(TOOL_CALLS, default=str)
    user_prompt = (
        input_prompt.replace("{QUESTION}", QUESTION)
        .replace("{TOOL_CATALOG}", TOOL_CATALOG or "")
        .replace("{TOOL_CALLS}", tool_calls_str)
    )

    response = model.invoke([("system", system_prompt), ("user", user_prompt)])
    content = getattr(response, "content", response)
    try:
        return json.loads(_strip_code_fence(content))
    except Exception as exp:
        return {"label": "NA", "reason": f"Error in parsing response. Exception: {exp}", "steps": []}


def process_single_input_tool_selection_quality(record, model):
    try:
        if record["tool_calls"] in ("", "[]", []):
            return {"label": "NA", "steps": [], "reason": "No tool calls"}
        full_query = f"User Question: {record['question']}"
        return get_tool_selection_quality(
            model, full_query, record["tool_calls"], record.get("tool_catalog", "")
        )
    except ValueError as e:
        return {"label": "NA", "steps": [], "reason": f"Error: ValueError: {str(e)}"}
    except Exception as e:
        return {"label": "NA", "steps": [], "reason": f"Error: {type(e).__name__}: {str(e)}"}


_ACTION_ADVANCEMENT_SYSTEM_TEMPLATE = """You are a goal-advancement evaluator. Given a user message, an assistant response, and optional tool outputs, assign one label for how well the assistant advanced the user's goal.

## Decision Rule
Return exactly one label, deciding in this order:

- "Did Not Advance": The response is factually wrong, off-topic, misleading, or contradicts the tool outputs. Accuracy is the gate — if the response is incorrect or inconsistent with the tools, it does not advance the goal regardless of how helpful it sounds.

- "Advanced": The response is accurate and tool-consistent, AND it directly and usefully moves the user toward completing their goal — a complete answer, a correct action confirmation, or a correct result drawn from the tool outputs.

- "Partially Advanced": The response is accurate but does not fully complete the goal — it gives a correct partial answer, or asks a necessary and useful clarifying question that the goal genuinely depends on.

Key rules:
- Accuracy is checked first. A response that is factually wrong or contradicts the tool outputs is always "Did Not Advance", even if it is on-topic and well-phrased.
- The deciding question between Advanced and Partially Advanced is: does the response fully move the user to their goal? If it completes the goal accurately → Advanced. If it only moves part of the way (partial answer or a necessary clarification) → Partially Advanced.
- A clarifying question is "Partially Advanced" only if it is genuinely necessary to proceed. A clarification the assistant did not actually need is not advancement.
- If tool outputs are provided, the response must faithfully reflect them. Any mismatch is "Did Not Advance".
- Judge advancement of the goal, not length or politeness.

## Examples

{examples}

## Output Format
Output ONLY a JSON object, nothing else:
{{{{"label": "Advanced" | "Partially Advanced" | "Did Not Advance", "reason": "<one short sentence>"}}}}"""


def get_action_advancement(model, QUESTION, TOOL_RESULTS, ANSWER):
    system_prompt = _ACTION_ADVANCEMENT_SYSTEM_TEMPLATE.format(examples=few_shots.ACTION_ADVANCEMENT_EXAMPLES)

    input_prompt = """<user_message>
{QUESTION}
</user_message>

<tool_outputs>
{TOOL_RESULTS}
</tool_outputs>

<assistant_response>
{ANSWER}
</assistant_response>"""

    tool_results_str = (
        TOOL_RESULTS if isinstance(TOOL_RESULTS, str) else json.dumps(TOOL_RESULTS, default=str)
    )
    user_prompt = (
        input_prompt.replace("{QUESTION}", QUESTION)
        .replace("{TOOL_RESULTS}", tool_results_str)
        .replace("{ANSWER}", ANSWER)
    )

    response = model.invoke([("system", system_prompt), ("user", user_prompt)])
    content = getattr(response, "content", response)
    try:
        return json.loads(_strip_code_fence(content))
    except Exception as exp:
        return {"label": "NA", "reason": f"Error in parsing response. Exception: {exp}"}


def process_single_input_action_advancement(record, model):
    try:
        if record["tool_calls"] in ("", "[]", []):
            return {"label": "NA", "reason": "No tool calls"}
        full_query = f"User Question: {record['question']}"
        return get_action_advancement(model, full_query, record["tool_results"], record["answer"])
    except ValueError as e:
        return {"label": "NA", "reason": f"Error: ValueError: {str(e)}"}
    except Exception as e:
        return {"label": "NA", "reason": f"Error: {type(e).__name__}: {str(e)}"}


def extract_tool_results(tool_calls):
    results = []
    if isinstance(tool_calls, list):
        for item in tool_calls:
            if isinstance(item, dict) and "result" in item:
                results.append(item["result"])
    elif isinstance(tool_calls, dict):
        if "result" in tool_calls:
            results.append(tool_calls["result"])
        else:
            for v in tool_calls.values():
                if isinstance(v, dict) and "result" in v:
                    results.append(v["result"])
                elif isinstance(v, list):
                    for item in v:
                        if isinstance(item, dict) and "result" in item:
                            results.append(item["result"])
    return results


def get_metrics(agentic_quality_results):
    """Aggregate per-record judge labels into mean scores per metric."""
    score_mapping = {
        "advanced": 1.0,
        "did not advance": 0.0,
        "partially advanced": 0.5,
        "good": 1.0,
        "poor": 0.0,
    }

    def _avg(metric_name):
        scores = []
        for r in agentic_quality_results:
            entry = r.get(metric_name, {})
            if not isinstance(entry, dict):
                continue
            label = str(entry.get("label", "")).strip().lower()
            if label in score_mapping:
                scores.append(score_mapping[label])
        return round(sum(scores) / len(scores), 4) if scores else None

    return {
        "tool_selection_quality": _avg("tool_selection_quality"),
        "action_advancement": _avg("action_advancement"),
    }


def score_agentic_quality(model, records, tool_catalog=""):
    """records: list of {"question": str, "tool_calls": list[{"tool","result"}], "answer": str}
    tool_catalog: markdown <available_tools> doc (same for every record in a run).

    Returns (per_record_results, aggregate_metrics) where per_record_results is a
    list of {"tool_selection_quality": {...}, "action_advancement": {...}} aligned
    by index with `records`, and aggregate_metrics is {"tool_selection_quality": avg,
    "action_advancement": avg}.
    """
    inputs = []
    for record in records:
        tool_calls = record.get("tool_calls", [])
        inputs.append(
            {
                "question": record.get("question", ""),
                "tool_calls": tool_calls,
                "tool_results": extract_tool_results(tool_calls),
                "answer": record.get("answer", ""),
                "tool_catalog": tool_catalog,
            }
        )

    agentic_quality_results = [{} for _ in range(len(inputs))]
    if inputs:
        with ThreadPoolExecutor(max_workers=3) as executor:
            future_map = {}
            for idx, inp in enumerate(inputs):
                future_map[executor.submit(process_single_input_tool_selection_quality, inp, model)] = (
                    idx,
                    "tool_selection_quality",
                )
                future_map[executor.submit(process_single_input_action_advancement, inp, model)] = (
                    idx,
                    "action_advancement",
                )
            for future in as_completed(future_map):
                idx, metric = future_map[future]
                agentic_quality_results[idx][metric] = future.result()

    aggregate = get_metrics(agentic_quality_results)
    return agentic_quality_results, aggregate
