"""Manual smoke test for DYNACONF_LARGE_OUTPUT__ENABLED.

On this branch the probe drives the function-calling agent: hybrid mode by default
(native tool calls + execute_python), or LARGE_OUTPUT_PROBE_EXECUTION_MODE=function_calling.
There, large_output saves an oversized tool result to a file and a search subagent
greps/reads it (helpers/large_output.py) instead of the batch output budget cutting it.

Not run in CI (it makes real model calls). Answers a narrow question: does
the large-output subagent-digestion path actually retrieve a fact buried in
an oversized *unstructured* tool result, instead of the model just seeing
truncated/garbled text?

Background: most real tool outputs return structured JSON (lists of DB
records), which always takes large_output_dispatch.py's cheap "guidance"
branch, never the subagent-digestion branch — see
build_haystack.py's module docstring. This probe exercises a single tool,
`get_archive_document`, that returns one large (~148k char) unstructured text
blob with 5 synthetic facts spliced into it (data/haystack.txt +
data/questions.json, built by build_haystack.py). Each question is asked in
its own fresh agent so the 5 facts can't leak between runs via chat history.

Prerequisites (same model/API env this repo's other real-LLM e2e tests use,
e.g. `tests/e2e/skills/`, plus):
    DYNACONF_LARGE_OUTPUT__ENABLED=true
    DYNACONF_ADVANCED_FEATURES__LITE_MODE=true   (large_output only fires in
        the CugaLite/CodeAct executor path; see large_output_dispatch.py)

Run:
    DYNACONF_LARGE_OUTPUT__ENABLED=true \
    uv run python tests/e2e/large_output_probe/run_probe.py

Set LARGE_OUTPUT_PROBE_FIXTURE=hard to run against the harder fixture instead
(data/haystack_hard.txt, ~1.7M chars / ~12x bigger, 3 novels concatenated,
plus 2 near-duplicate decoy notes per real needle — see `build_haystack.py hard`).
Set LARGE_OUTPUT_PROBE_FIXTURE=extreme for the most extreme fixture
(data/haystack_extreme.txt, ~8.6M chars / ~5x hard / ~59x the base fixture, 7
different novels concatenated, plus 4 near-duplicate decoy notes per real
needle — see `build_haystack.py extreme`). Default is "easy" (data/haystack.txt,
~145k chars, no decoys).

Set LARGE_OUTPUT_PROBE_MODE=convo to ask all of a fixture's questions to a
single CugaAgent over one conversation thread (same thread_id across turns)
instead of the default "fresh" mode's one-fresh-agent-per-question. This
answers a different question than the fixture tiers do: not just "can it find
one needle," but "does it still find each needle turn after turn as its own
chat history — prior questions, prior digested answers, prior raw tool-output
previews — accumulates in context." Per-turn token counts (via
`InvokeResult.receipt`, which is scoped to that turn even in convo mode — see
`CugaAgent.invoke`'s prior_tool_calls_count/resume_prior_tool_calls snapshotting)
are summed into a running conversation total, the number to compare against
fresh mode's same-fixture total: convo mode's total should exceed fresh mode's
by roughly the cost of re-reading each prior turn's growing chat history, on
top of whatever the digestion path itself costs per turn.

Set LARGE_OUTPUT_PROBE_MODE=redigest for a third mode, orthogonal to
LARGE_OUTPUT_PROBE_FIXTURE (ignored in this mode): a fixed 5-turn conversation
against *two* documents — the "easy" fixture's haystack.txt (document A, reused
as-is) plus a second, topically unrelated document (document B,
data/haystack_redigest_b.txt, built by `build_haystack.py redigest_b`) —
introduced via two separate tools, `get_document_a`/`get_document_b`. The turn
order is A, A, B, B, A: turns 1-2 establish a normal single-document
conversation (same as convo mode); turn 3 switches to document B, which is
brand new to the sandbox's persisted variables, so — unlike re-accessing
document A on a later turn — the large-output dispatch gate is *guaranteed* to
fire again on turn 3 (a genuinely new oversized variable, not a maybe-cached
one). That is the one condition convo mode's own fixtures never manufacture
(see results.md's v6 section): before the current-turn-context fix, the
digesting subagent's task always used the thread's *first* message — turn 1's
document-A question — as "the user's overall request," even on turn 3 while
actually looking for a document-B fact with zero shared vocabulary. Turn 4
repeats on document B (does the parent reuse it correctly, unconfused by A);
turn 5 switches back to A (does switching away and back still work). Compare
`DYNACONF_LARGE_OUTPUT__SUBAGENT_TASK_CONTEXT_MODE=first_message_only` (the
pre-fix behavior) against the default `current_turn` to measure the fix's own
effect on this fixture directly, instead of arguing about it.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import unicodedata
import uuid
from pathlib import Path
from typing import Optional

from cuga.config import settings
from cuga.sdk import CugaAgent
from langchain_core.tools import tool

DATA_DIR = Path(__file__).parent / "data"
_FIXTURE = os.environ.get("LARGE_OUTPUT_PROBE_FIXTURE", "easy").strip().lower()
if _FIXTURE == "extreme":
    HAYSTACK_PATH = DATA_DIR / "haystack_extreme.txt"
    QUESTIONS_PATH = DATA_DIR / "questions_extreme.json"
elif _FIXTURE == "hard":
    HAYSTACK_PATH = DATA_DIR / "haystack_hard.txt"
    QUESTIONS_PATH = DATA_DIR / "questions_hard.json"
elif _FIXTURE == "easy":
    HAYSTACK_PATH = DATA_DIR / "haystack.txt"
    QUESTIONS_PATH = DATA_DIR / "questions.json"
else:
    raise SystemExit(
        f"Unknown LARGE_OUTPUT_PROBE_FIXTURE={_FIXTURE!r} — expected 'easy', 'hard', or 'extreme'"
    )
_EXECUTION_MODE = os.environ.get("LARGE_OUTPUT_PROBE_EXECUTION_MODE", "hybrid").strip().lower()
if _EXECUTION_MODE not in ("hybrid", "function_calling", "codeact"):
    raise SystemExit(
        f"Unknown LARGE_OUTPUT_PROBE_EXECUTION_MODE={_EXECUTION_MODE!r} — expected 'hybrid', "
        "'function_calling', or 'codeact'"
    )
_MODE = os.environ.get("LARGE_OUTPUT_PROBE_MODE", "fresh").strip().lower()
if _MODE not in ("fresh", "convo", "redigest"):
    raise SystemExit(f"Unknown LARGE_OUTPUT_PROBE_MODE={_MODE!r} — expected 'fresh', 'convo', or 'redigest'")
EXECUTION_OUTPUT_MAX_LENGTH = settings.advanced_features.execution_output_max_length

# LLM-as-judge fallback, vendored from cuga-eval's CorrectnessJudge evaluator
# (judge.py/utils.py/prompt.py in this same directory) rather than imported
# across repos — judge.py's own `from prompt import ...` / `from utils import
# ...` resolve because this script's directory is on sys.path when run
# directly (`python run_probe.py`).
from judge import CorrectnessJudge  # noqa: E402
from utils import JudgeInput  # noqa: E402

_ARCHIVE_NOTE_TAG_RE = re.compile(r"^\[ARCHIVE NOTE \d+\]\s*")
_correctness_judge: CorrectnessJudge | None = None


def _judge_answer(question: str, expected: str, answer: str) -> tuple[Optional[bool], str]:
    """LLM-as-judge fallback for when the strict substring check fails.

    Catches correct answers phrased differently than the fixture text (e.g.
    "15 Sep" vs "15 September") that substring matching would wrongly fail.
    Returns (None, reason) if the judge itself couldn't be reached, so the
    caller can fall back to the substring verdict rather than crash the probe.
    """
    global _correctness_judge
    try:
        if _correctness_judge is None:
            _correctness_judge = CorrectnessJudge(config={})
        result = _correctness_judge.judge(
            JudgeInput(
                capability="large_output_probe",
                domain="large_output_probe",
                query=question,
                additional_instructions="",
                gt_answer=expected,
                pred_answer=answer,
                gt_tool_calls=[],
                pred_tool_calls=[],
                gt_tool_responses=[],
                pred_tool_responses=[],
            )
        )
        return result.score == 1, result.explanation or ""
    except Exception as e:
        return None, f"judge unavailable: {e}"


def _check_prereqs() -> None:
    print(f"execution mode: {settings.advanced_features.cuga_lite_execution_mode}")
    if _EXECUTION_MODE == "codeact":
        print(
            "codeact: large_output does not run in the sandbox path on this branch (truncation baseline).\n"
        )
    elif not getattr(settings.large_output, "enabled", False):
        print(
            "settings.large_output.enabled is False — running the legacy-truncation baseline "
            "(DYNACONF_LARGE_OUTPUT__ENABLED=true for the save-to-file + search-subagent path).\n"
        )
    else:
        print(
            f"large_output: mode {settings.large_output.mode}, "
            f"threshold {settings.large_output.threshold_chars:,} chars, "
            f"max_rounds {settings.large_output.max_rounds}, search_max_steps {settings.large_output.search_max_steps}\n"
        )


# Model answers routinely use typographic dashes (‑ U+2011, – U+2013, — U+2014)
# where the fixture uses a plain ASCII hyphen; normalize before substring matching
# so that's not scored as a miss.
_DASH_CHARS = {"‐", "‑", "‒", "–", "—", "―"}


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    for dash in _DASH_CHARS:
        text = text.replace(dash, "-")
    return text


def _contains_token(haystack: str, needle: str) -> bool:
    """Word-boundary match, not a bare substring check — needed because some
    `required_substrings` alternatives are short digit strings (e.g. needle_3's
    "0" for "zero violations", needle_2's "17" for "seventeen crates"). A plain
    `needle in haystack` check would false-positive on any unrelated number
    containing that digit sequence, e.g. a hallucinated year like "2026" or
    badge number contains "0" too — "0" would then wrongly count as a match for
    "zero violations" regardless of what the answer actually said.
    """
    return re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack) is not None


def _find_missing(answer: str, required_substrings: list) -> list:
    """`required_substrings` is a list of alternative-sets (AND across sets, OR
    within a set) — e.g. [["KL-90231"], ["seventeen", "17"]] needs KL-90231 AND
    (seventeen OR 17). Returns the alternative-sets with no match in `answer`.
    """
    normalized_answer = _normalize(answer)
    missing = []
    for alternatives in required_substrings:
        if not any(_contains_token(normalized_answer, _normalize(alt)) for alt in alternatives):
            missing.append(alternatives)
    return missing


def _make_archive_tool(document_text: str):
    @tool
    def get_archive_document() -> str:
        """Fetch the full text of the requested archive document. Returns a large
        block of unstructured text — read it to find the information you need."""
        return document_text

    return get_archive_document


def _make_dual_archive_tools(document_a: str, document_b: str):
    """Two separate tools for redigest mode — the model must pick the right one per
    question, and fetching each returns a distinct large text blob (see module
    docstring). Docstrings hint at each document's domain (without giving away
    answers) so tool selection is a reasonable task, not a coin flip.
    """

    @tool
    def get_document_a() -> str:
        """Fetch the full text of Archive Document A. Returns a large block of
        unstructured text covering facility/logistics records: generator servicing,
        shipments, safety inspections, elevator parts, visitor badges."""
        return document_a

    @tool
    def get_document_b() -> str:
        """Fetch the full text of Archive Document B. Returns a large block of
        unstructured text covering IT-operations/finance records: database backup
        jobs, budget approval codes."""
        return document_b

    return [get_document_a, get_document_b]


def _grade(question: dict, answer: str) -> dict:
    """Substring check + judge fallback, shared by fresh and convo modes."""
    missing = _find_missing(answer, question["required_substrings"])
    substring_passed = not missing

    # Only call the judge when the strict check fails — it's meant to rescue
    # answers that got the fact right but phrased it differently, not to
    # re-check answers substring matching already confirmed.
    judge_passed: Optional[bool] = None
    judge_explanation = ""
    if not substring_passed:
        expected = _ARCHIVE_NOTE_TAG_RE.sub("", question["needle_text"])
        judge_passed, judge_explanation = _judge_answer(question["question"], expected, answer)

    return {
        "passed": substring_passed or bool(judge_passed),
        "substring_passed": substring_passed,
        "missing_substrings": missing,
        "judge_passed": judge_passed,
        "judge_explanation": judge_explanation,
    }


async def _run_one(document_text: str, question: dict) -> dict:
    agent = CugaAgent(tools=[_make_archive_tool(document_text)])
    result = await agent.invoke(question["question"], track_tool_calls=True)

    tool_call = next((c for c in result.tool_calls if c.get("name") == "get_archive_document"), None)
    raw_len = len(str(tool_call["result"])) if tool_call else None

    answer = result.answer or ""
    grade = _grade(question, answer)

    receipt = result.receipt
    return {
        "id": question["id"],
        "question": question["question"],
        "answer": answer,
        **grade,
        "raw_tool_output_chars": raw_len,
        "exceeded_threshold": (raw_len or 0) > EXECUTION_OUTPUT_MAX_LENGTH,
        "error": result.error,
        "input_tokens": receipt.input_tokens if receipt else None,
        "output_tokens": receipt.output_tokens if receipt else None,
        "total_tokens": receipt.total_tokens if receipt else None,
    }


async def _run_conversation(document_text: str, questions: list) -> list:
    """Ask every question for this fixture to a single CugaAgent over one
    conversation thread, instead of a fresh agent per question — see the
    LARGE_OUTPUT_PROBE_MODE=convo note in the module docstring.

    `InvokeResult.tool_calls` accumulates across the whole thread (per
    cuga/sdk.py's `CugaAgent.invoke`), so each turn's *new* tool calls are the
    slice past the previous turn's count — that's how we tell whether a turn
    re-fetched the archive document or answered from chat history alone.
    `InvokeResult.receipt`, in contrast, is already scoped to just that turn,
    so summing it turn over turn gives the true conversation-wide token total.
    """
    agent = CugaAgent(tools=[_make_archive_tool(document_text)])
    thread_id = f"large_output_probe_convo_{uuid.uuid4().hex[:8]}"

    results = []
    prior_tool_call_count = 0
    cumulative_tokens = 0
    for turn_index, question in enumerate(questions, start=1):
        result = await agent.invoke(question["question"], thread_id=thread_id, track_tool_calls=True)

        new_tool_calls = result.tool_calls[prior_tool_call_count:]
        prior_tool_call_count = len(result.tool_calls)
        tool_call = next((c for c in new_tool_calls if c.get("name") == "get_archive_document"), None)
        raw_len = len(str(tool_call["result"])) if tool_call else None

        answer = result.answer or ""
        grade = _grade(question, answer)

        receipt = result.receipt
        turn_total = receipt.total_tokens if receipt else None
        if turn_total is not None:
            cumulative_tokens += turn_total

        results.append(
            {
                "id": question["id"],
                "question": question["question"],
                "answer": answer,
                **grade,
                "turn_index": turn_index,
                "raw_tool_output_chars": raw_len,
                "tool_refetched": tool_call is not None,
                "exceeded_threshold": (raw_len or 0) > EXECUTION_OUTPUT_MAX_LENGTH
                if raw_len is not None
                else None,
                "error": result.error,
                "input_tokens": receipt.input_tokens if receipt else None,
                "output_tokens": receipt.output_tokens if receipt else None,
                "total_tokens": receipt.total_tokens if receipt else None,
                "cumulative_total_tokens": cumulative_tokens,
            }
        )
    return results


def _load_redigest_turns() -> list:
    """Fixed A,A,B,B,A turn order for LARGE_OUTPUT_PROBE_MODE=redigest — see module
    docstring. Document A's questions are the "easy" fixture's own needle_1/needle_2/
    needle_3 (data/questions.json); document B's are redigest_b1/redigest_b2
    (data/questions_redigest_b.json, built by `build_haystack.py redigest_b`).
    """
    doc_a_questions = {
        q["id"]: q for q in json.loads((DATA_DIR / "questions.json").read_text(encoding="utf-8"))
    }
    b_path = DATA_DIR / "questions_redigest_b.json"
    if not b_path.exists():
        raise SystemExit(
            f"Missing {b_path} — run: uv run python {Path(__file__).parent / 'build_haystack.py'} redigest_b"
        )
    doc_b_questions = {q["id"]: q for q in json.loads(b_path.read_text(encoding="utf-8"))}

    order = [
        ("a", "needle_1"),
        ("a", "needle_2"),
        ("b", "redigest_b1"),
        ("b", "redigest_b2"),
        ("a", "needle_3"),
    ]
    turns = []
    for doc, qid in order:
        q = (doc_a_questions if doc == "a" else doc_b_questions)[qid]
        turns.append({"doc": doc, **q})
    return turns


async def _run_redigest_conversation(document_a: str, document_b: str, turns: list) -> list:
    """Like `_run_conversation`, but against two documents via two separate tools —
    see LARGE_OUTPUT_PROBE_MODE=redigest in the module docstring. Tracks which of the
    two tools (if either) was newly called each turn, the redigest-mode analogue of
    `_run_conversation`'s single `tool_refetched` flag.
    """
    agent = CugaAgent(tools=_make_dual_archive_tools(document_a, document_b))
    thread_id = f"large_output_probe_redigest_{uuid.uuid4().hex[:8]}"

    results = []
    prior_tool_call_count = 0
    cumulative_tokens = 0
    for turn_index, question in enumerate(turns, start=1):
        result = await agent.invoke(question["question"], thread_id=thread_id, track_tool_calls=True)

        new_tool_calls = result.tool_calls[prior_tool_call_count:]
        prior_tool_call_count = len(result.tool_calls)
        fetched_a = any(c.get("name") == "get_document_a" for c in new_tool_calls)
        fetched_b = any(c.get("name") == "get_document_b" for c in new_tool_calls)

        answer = result.answer or ""
        grade = _grade(question, answer)

        receipt = result.receipt
        turn_total = receipt.total_tokens if receipt else None
        if turn_total is not None:
            cumulative_tokens += turn_total

        results.append(
            {
                "id": question["id"],
                "doc": question["doc"],
                "question": question["question"],
                "answer": answer,
                **grade,
                "turn_index": turn_index,
                "fetched_a": fetched_a,
                "fetched_b": fetched_b,
                "error": result.error,
                "input_tokens": receipt.input_tokens if receipt else None,
                "output_tokens": receipt.output_tokens if receipt else None,
                "total_tokens": receipt.total_tokens if receipt else None,
                "cumulative_total_tokens": cumulative_tokens,
            }
        )
    return results


def _print_redigest_result(result: dict) -> None:
    status = "PASS" if result["passed"] else "FAIL"
    print(
        f"[{status}] turn {result['turn_index']} (doc {result['doc']}): {result['id']}: {result['question']}"
    )
    print(f"       fetched doc A: {result['fetched_a']}, fetched doc B: {result['fetched_b']}")
    print(f"       answer: {result['answer'][:300]!r}")
    if result["missing_substrings"]:
        print(f"       missing (substring check): {result['missing_substrings']}")
        print(
            f"       judge verdict: "
            f"{'MATCH' if result['judge_passed'] else 'NO MATCH' if result['judge_passed'] is False else 'unavailable'}"
            f" — {result['judge_explanation']}"
        )
    if result["input_tokens"] is not None:
        print(
            f"       tokens this turn: {result['input_tokens']:,} in / {result['output_tokens']:,} out "
            f"({result['total_tokens']:,} total)"
        )
        print(f"       cumulative conversation tokens: {result['cumulative_total_tokens']:,}")
    else:
        print("       tokens: n/a (advanced_features.run_receipt not enabled)")
    if result["error"]:
        print(f"       error: {result['error']}")
    print()


def _print_result(result: dict, *, convo: bool) -> None:
    status = "PASS" if result["passed"] else "FAIL"
    prefix = f"turn {result['turn_index']}: " if convo else ""
    print(f"[{status}] {prefix}{result['id']}: {result['question']}")
    if convo:
        print(
            f"       archive doc re-fetched this turn: {result['tool_refetched']} "
            f"(raw tool output: {result['raw_tool_output_chars']} chars)"
        )
    else:
        print(
            f"       raw tool output: {result['raw_tool_output_chars']} chars "
            f"(over threshold: {result['exceeded_threshold']})"
        )
    print(f"       answer: {result['answer'][:300]!r}")
    if result["missing_substrings"]:
        print(f"       missing (substring check): {result['missing_substrings']}")
        print(
            f"       judge verdict: "
            f"{'MATCH' if result['judge_passed'] else 'NO MATCH' if result['judge_passed'] is False else 'unavailable'}"
            f" — {result['judge_explanation']}"
        )
    if result["input_tokens"] is not None:
        tokens_line = (
            f"       tokens this turn: {result['input_tokens']:,} in / {result['output_tokens']:,} out "
            f"({result['total_tokens']:,} total)"
            if convo
            else f"       tokens: {result['input_tokens']:,} in / {result['output_tokens']:,} out "
            f"({result['total_tokens']:,} total)"
        )
        print(tokens_line)
        if convo:
            print(f"       cumulative conversation tokens: {result['cumulative_total_tokens']:,}")
    else:
        print("       tokens: n/a (advanced_features.run_receipt not enabled)")
    if result["error"]:
        print(f"       error: {result['error']}")
    print()


async def _run_redigest_mode() -> None:
    _check_prereqs()

    a_path = DATA_DIR / "haystack.txt"
    b_path = DATA_DIR / "haystack_redigest_b.txt"
    if not a_path.exists():
        builder = Path(__file__).parent / "build_haystack.py"
        raise SystemExit(f"Missing {a_path}. Run: uv run python {builder} easy")
    if not b_path.exists():
        builder = Path(__file__).parent / "build_haystack.py"
        raise SystemExit(f"Missing {b_path}. Run: uv run python {builder} redigest_b")

    document_a = a_path.read_text(encoding="utf-8")
    document_b = b_path.read_text(encoding="utf-8")
    turns = _load_redigest_turns()

    print(
        f"Document A: {len(document_a):,} chars, Document B: {len(document_b):,} chars "
        f"(threshold is {EXECUTION_OUTPUT_MAX_LENGTH:,} chars) — {len(turns)} turns — mode: redigest\n"
    )

    results = await _run_redigest_conversation(document_a, document_b, turns)
    for result in results:
        _print_redigest_result(result)

    passed = sum(r["passed"] for r in results)
    print(f"{passed}/{len(results)} passed")

    known_tokens = [r for r in results if r["input_tokens"] is not None]
    if known_tokens:
        total_in = sum(r["input_tokens"] for r in known_tokens)
        total_out = sum(r["output_tokens"] for r in known_tokens)
        total_all = sum(r["total_tokens"] for r in known_tokens)
        print(
            f"tokens across {len(known_tokens)} conversation(s): "
            f"{total_in:,} in / {total_out:,} out ({total_all:,} total)"
        )

    if passed < len(results):
        raise SystemExit(1)


async def main() -> None:
    if _MODE == "redigest":
        await _run_redigest_mode()
        return

    _check_prereqs()

    if not HAYSTACK_PATH.exists() or not QUESTIONS_PATH.exists():
        builder = Path(__file__).parent / "build_haystack.py"
        raise SystemExit(f"Missing fixture data. Run: uv run python {builder} {_FIXTURE}")

    document_text = HAYSTACK_PATH.read_text(encoding="utf-8")
    questions = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))
    convo = _MODE == "convo"

    print(
        f"Document: {len(document_text):,} chars "
        f"(threshold is {EXECUTION_OUTPUT_MAX_LENGTH:,} chars) — {len(questions)} questions "
        f"— mode: {_MODE}\n"
    )

    if convo:
        results = await _run_conversation(document_text, questions)
        for result in results:
            _print_result(result, convo=True)
    else:
        results = []
        for question in questions:
            result = await _run_one(document_text, question)
            results.append(result)
            _print_result(result, convo=False)

    passed = sum(r["passed"] for r in results)
    print(f"{passed}/{len(results)} passed")

    known_tokens = [r for r in results if r["input_tokens"] is not None]
    if known_tokens:
        total_in = sum(r["input_tokens"] for r in known_tokens)
        total_out = sum(r["output_tokens"] for r in known_tokens)
        total_all = sum(r["total_tokens"] for r in known_tokens)
        label = "conversation" if convo else "question"
        print(
            f"tokens across {len(known_tokens)} {label}(s): "
            f"{total_in:,} in / {total_out:,} out ({total_all:,} total)"
        )

    if passed < len(results):
        raise SystemExit(1)


if __name__ == "__main__":
    os.environ.setdefault("DYNACONF_ADVANCED_FEATURES__FORCE_AUTONOMOUS_MODE", "true")
    # NOT an os.environ.setdefault: `settings` already did its first (eager, whole-tree)
    # Dynaconf load at import time above (EXECUTION_OUTPUT_MAX_LENGTH = settings...),
    # so an env var set here would arrive too late to be picked up. Set the box attribute
    # directly instead — same pattern cuga-agent's own tests use to toggle this flag.
    settings.advanced_features.run_receipt = True
    settings.advanced_features.cuga_lite_execution_mode = _EXECUTION_MODE
    asyncio.run(main())
