"""Large tool results in function-calling / hybrid mode: save to a file, let a search subagent read it.

Without this, ``tool_exec``'s batch output budget cuts a result at
``execution_output_max_length`` and the agent sees whatever happened to come first.
free-code never truncates: ``utils/toolResultStorage.ts`` writes any
result over ``DEFAULT_MAX_RESULT_SIZE_CHARS`` (50K) to
``<session>/tool-results/<tool_use_id>.txt`` and replies with a ``<persisted-output>``
stub — the path plus a 2KB preview — so the model can Grep/Read the file itself.

Here the same file is written *before* the agent sees anything, and a short-lived
search subagent does the Grep/Read on the agent's behalf (the agent has no file
tools): it gets the file through two tools, ``grep`` and ``read``, searches for what
the current step needs, and only its findings return to the agent's context.

A second loop checks the findings: a separate call compares them with the need and,
when something is missing, the subagent goes back to the file with that gap stated —
up to ``large_output.max_rounds`` rounds.

Later questions about the same result — a later step, a later turn — go through
``query_saved_output(file, question)``, a native tool bound in function-calling and
hybrid modes that runs the same subagent over a file this thread saved.

``large_output.mode = "main_agent"`` is the ablation without a subagent: the agent
itself gets ``grep_saved_output``/``read_saved_output`` from the start of the run and,
when a result is too large, only free-code's reply (path + preview) — it searches the
file itself, the way free-code's model Greps/Reads a persisted result.

Off by default (``large_output.enabled``); any failure falls back to plain truncation.
"""

from __future__ import annotations

import asyncio
import json
import re
import tempfile
import uuid
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from loguru import logger
from pydantic import BaseModel, Field

from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.harness_messages import is_harness_message
from cuga.config import settings

PERSISTED_OUTPUT_TAG = "<persisted-output>"
PERSISTED_OUTPUT_CLOSING_TAG = "</persisted-output>"

GREP_NAME = "grep"
READ_NAME = "read"
QUERY_SAVED_OUTPUT_NAME = "query_saved_output"

QUERY_SAVED_OUTPUT_DESCRIPTION = (
    "Ask a question about a large tool result that was saved to a file (a <persisted-output> reply names "
    "the file). A search subagent reads the file and returns what answers the question. Use it when the "
    "findings in that reply do not cover what you need now, instead of calling the original tool again."
)

GREP_SAVED_NAME = "grep_saved_output"
READ_SAVED_NAME = "read_saved_output"
#: Every tool this module binds to the agent, whatever the mode (tool_exec dispatches them).
SAVED_OUTPUT_TOOL_NAMES = frozenset({QUERY_SAVED_OUTPUT_NAME, GREP_SAVED_NAME, READ_SAVED_NAME})

MODE_SUBAGENT = "subagent"
MODE_MAIN_AGENT = "main_agent"

_WRAP_AT = 4000  # longer lines (minified JSON/HTML) are split so grep/read stay line-oriented
_WRAP_CHUNK = 2000
_MAX_TOOL_OUTPUT_CHARS = 6000
_MAX_LINE_CHARS = 1000  # per displayed line
_MAX_MATCHES = 30
_MAX_CONTEXT = 5
_MAX_READ_LINES = 400
_MAX_NEED_PART_CHARS = 1500

_SUBAGENT_SYSTEM = """\
You are a search subagent. Another agent called a tool whose result was too large for its context. \
The full result is saved to a text file that you can search with two tools:

- grep(pattern, context_lines=2, max_matches=10, ignore_case=true): regex search (a literal search if the \
pattern is not a valid regex). Returns matching lines with their line numbers ("123: ..." for a match, \
"123- ..." for context) and the total match count.
- read(offset=1, limit=100): read lines by 1-based line number.

How to work:
- Search first. Use the most distinctive term from the need (a name, id, code, number), not a common word.
- If nothing matches, try other spellings, synonyms, or a shorter part of the term before concluding.
- When several entries look alike, check every detail of the need against each and keep only the one that \
matches all of them. Read around a match when the matched line alone does not show every detail.
- Never read the whole file: it does not fit. Read narrow ranges around matches.

When you are done, reply with no tool call. Your reply goes to the other agent verbatim, so:
- State the findings directly: exact values, ids, names, numbers and dates as written in the file, and the \
line numbers they came from.
- If something the need asks for is not in the file, say so explicitly.
- If the need requires a computation over every record (count, sum, list all), report what you can and \
say that the full data has to be processed in code.
- No preamble, no restating the question.
"""

_CHECK_SYSTEM = """\
You check a search subagent's findings. The subagent searched a large tool result for what an agent needs. \
Decide whether the findings give the agent everything the need asks for. Findings that clearly state \
something is absent from the result count as an answer for that part. Reply with JSON only: \
{"complete": true|false, "missing": "<what is still missing or ambiguous; empty when complete>"}"""


# ── settings ─────────────────────────────────────────────────────────────────


def _cfg(key: str, default: Any) -> Any:
    section = getattr(settings, "large_output", None)
    value = getattr(section, key, None) if section is not None else None
    return default if value is None else value


def large_output_enabled() -> bool:
    return bool(_cfg("enabled", False))


def large_output_mode() -> str:
    """``subagent`` (a search subagent reads the file) or ``main_agent`` (the agent greps it)."""
    mode = str(_cfg("mode", MODE_SUBAGENT) or MODE_SUBAGENT).strip().lower()
    return mode if mode in (MODE_SUBAGENT, MODE_MAIN_AGENT) else MODE_SUBAGENT


def large_output_threshold() -> int:
    """Characters above which a result is saved and searched instead of truncated."""
    value = int(_cfg("threshold_chars", 0) or 0)
    if value > 0:
        return value
    return int(getattr(settings.advanced_features, "execution_output_max_length", 0) or 0)


# ── the file ─────────────────────────────────────────────────────────────────


def as_searchable_text(result: Any) -> str:
    """Text written to the file: JSON is indented so each field is its own line."""
    if isinstance(result, str):
        text = result
    else:
        try:
            text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
        except Exception:
            text = str(result)
    if not any(len(line) > _WRAP_AT for line in text.splitlines()):
        return text
    lines: List[str] = []
    for line in text.splitlines():
        if len(line) <= _WRAP_AT:
            lines.append(line)
        else:
            lines.extend(line[i : i + _WRAP_CHUNK] for i in range(0, len(line), _WRAP_CHUNK))
    return "\n".join(lines)


_UNSAFE = re.compile(r"[^0-9A-Za-z_.-]+")


def _safe(part: Any, fallback: str) -> str:
    return _UNSAFE.sub("_", str(part or "")).strip("._")[:80] or fallback


def spill_dir(thread_id: Optional[str]) -> Path:
    base = str(_cfg("spill_dir", "") or "").strip()
    root = Path(base).expanduser() if base else Path(tempfile.gettempdir()) / "cuga_large_output"
    return root / _safe(thread_id, "no-thread")


def spill_to_file(text: str, *, thread_id: Optional[str], call_id: Optional[str]) -> Path:
    """Write ``text`` to ``<spill_dir>/<thread>/<call_id>.txt`` (free-code's ``tool-results/<id>``).

    A call id is unique per invocation, so an existing file is the same content
    (a replayed turn) and is kept rather than rewritten.
    """
    directory = spill_dir(thread_id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{_safe(call_id, uuid.uuid4().hex)}.txt"
    try:
        with open(path, "x", encoding="utf-8") as handle:
            handle.write(text)
    except FileExistsError:
        pass
    return path


def preview(text: str, max_chars: int) -> tuple[str, bool]:
    """free-code's ``generatePreview``: cut at a newline when one is reasonably close."""
    if len(text) <= max_chars:
        return text, False
    head = text[:max_chars]
    newline = head.rfind("\n")
    return (head[:newline] if newline > max_chars * 0.5 else head), True


# ── the subagent's tools ─────────────────────────────────────────────────────


def _clip_line(line: str) -> str:
    return line if len(line) <= _MAX_LINE_CHARS else line[:_MAX_LINE_CHARS] + " …[line cut]"


def _clip(text: str, note: str) -> str:
    if len(text) <= _MAX_TOOL_OUTPUT_CHARS:
        return text
    return text[:_MAX_TOOL_OUTPUT_CHARS] + f"\n…[{note}]"


class _GrepInput(BaseModel):
    pattern: str = Field(..., description="Regex (or literal text) to search for.")
    context_lines: int = Field(2, description=f"Lines of context around each match (max {_MAX_CONTEXT}).")
    max_matches: int = Field(10, description=f"Matches to show (max {_MAX_MATCHES}).")
    ignore_case: bool = Field(True, description="Case-insensitive search.")


class _ReadInput(BaseModel):
    offset: int = Field(1, description="1-based line number to start at.")
    limit: int = Field(100, description=f"Number of lines to read (max {_MAX_READ_LINES}).")


class OutputFile:
    """grep/read over one saved result, streaming the file on every call."""

    def __init__(self, path: Path, total_lines: Optional[int] = None):
        self.path = Path(path)
        if total_lines is None:
            with open(self.path, encoding="utf-8", errors="replace") as handle:
                total_lines = sum(1 for _ in handle)
        self.total_lines = total_lines

    def _lines(self):
        with open(self.path, encoding="utf-8", errors="replace") as handle:
            for number, line in enumerate(handle, start=1):
                yield number, line.rstrip("\n")

    def grep(
        self, pattern: str, context_lines: int = 2, max_matches: int = 10, ignore_case: bool = True
    ) -> str:
        flags = re.IGNORECASE if ignore_case else 0
        try:
            regex = re.compile(pattern, flags)
        except re.error:
            regex = re.compile(re.escape(pattern), flags)
        ctx = max(0, min(int(context_lines), _MAX_CONTEXT))
        cap = max(1, min(int(max_matches), _MAX_MATCHES))

        blocks: List[List[str]] = []
        before: deque = deque(maxlen=ctx)
        after_left = 0
        last_shown = 0
        total = 0
        for number, line in self._lines():
            if regex.search(line):
                total += 1
                if total <= cap:
                    if not blocks or number - len(before) > last_shown + 1:
                        blocks.append([])
                    for n, prev in before:
                        if n > last_shown:
                            blocks[-1].append(f"{n}- {_clip_line(prev)}")
                    blocks[-1].append(f"{number}: {_clip_line(line)}")
                    last_shown = number
                    after_left = ctx
                    before.clear()
                    continue
            elif after_left and total <= cap:
                blocks[-1].append(f"{number}- {_clip_line(line)}")
                last_shown = number
                after_left -= 1
                continue
            before.append((number, line))

        if not total:
            return f"No matches for {pattern!r} ({self.total_lines:,} lines searched)."
        out = "\n--\n".join("\n".join(block) for block in blocks)
        header = f"{total} match(es) for {pattern!r}"
        if total > cap:
            header += (
                f"; showing the first {cap} — narrow the pattern or raise max_matches (max {_MAX_MATCHES})"
            )
        return _clip(f"{header}\n{out}", "output cut — narrow the pattern or lower context_lines")

    def read(self, offset: int = 1, limit: int = 100) -> str:
        start = max(1, int(offset))
        count = max(1, min(int(limit), _MAX_READ_LINES))
        if start > self.total_lines:
            return f"offset {start} is past the end ({self.total_lines:,} lines)."
        end = min(self.total_lines, start + count - 1)
        body = [f"{n}: {_clip_line(line)}" for n, line in self._lines() if start <= n <= end]
        text = _clip("\n".join(body), "output cut — read fewer lines")
        return f"{text}\n[lines {start}-{end} of {self.total_lines:,}]"

    def tools(self) -> List[StructuredTool]:
        return [
            StructuredTool.from_function(
                func=self.grep,
                name=GREP_NAME,
                description="Search the saved result for a regex (literal if invalid); returns numbered lines.",
                args_schema=_GrepInput,
            ),
            StructuredTool.from_function(
                func=self.read,
                name=READ_NAME,
                description="Read lines of the saved result by 1-based line number.",
                args_schema=_ReadInput,
            ),
        ]


# ── what the agent needs ─────────────────────────────────────────────────────


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
            if not isinstance(part, dict) or part.get("type") in (None, "text")
        )
    return str(content or "")


def _cut(text: str, limit: int = _MAX_NEED_PART_CHARS) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + " …"


def describe_need(messages: Sequence[BaseMessage], tool_name: str, args: Any) -> str:
    """What the agent wanted from this call: the user's request, its own words, the call."""
    request = ""
    for message in reversed(list(messages)):
        if isinstance(message, HumanMessage) and not is_harness_message(message):
            request = _text_of(message.content)
            break
    last = messages[-1] if messages else None
    intent = ""
    if isinstance(last, AIMessage):
        reasoning = (last.additional_kwargs or {}).get("reasoning_content")
        intent = _text_of(last.content).strip() or (reasoning if isinstance(reasoning, str) else "")
    try:
        call = f"{tool_name}({json.dumps(args, ensure_ascii=False, default=str)})"
    except Exception:
        call = f"{tool_name}({args!r})"

    parts = [f"User request:\n{_cut(request) or '(unavailable)'}"]
    if intent.strip():
        parts.append(f"The agent's note when making the call:\n{_cut(intent)}")
    parts.append(f"The call: {_cut(call, 500)}")
    return "\n\n".join(parts)


# ── the subagent ─────────────────────────────────────────────────────────────


@dataclass
class Research:
    answer: str
    rounds: int
    tool_calls: int
    complete: Optional[bool]  # None when no check ran (max_rounds = 1)
    missing: str = ""


def _task(need: str, total_chars: int, total_lines: int) -> str:
    return (
        f"{need}\n\nThe result of that call is {total_chars:,} characters ({total_lines:,} lines). "
        "Find what the agent needs from it to continue with the user's request."
    )


def _parse_check(text: str) -> tuple[Optional[bool], str]:
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not match:
        return None, ""
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None, ""
    complete = data.get("complete")
    return (complete if isinstance(complete, bool) else None), str(data.get("missing") or "").strip()


async def _check(
    model: Any, need: str, answer: str, invoke_config: Dict[str, Any]
) -> tuple[Optional[bool], str]:
    """The second loop's judge: a fresh call that sees only the need and the findings."""
    try:
        reply = await model.ainvoke(
            [
                SystemMessage(content=_CHECK_SYSTEM),
                HumanMessage(content=f"Need:\n{need}\n\nFindings:\n{answer}"),
            ],
            config=invoke_config,
        )
    except Exception as exc:
        logger.warning(f"large_output: findings check failed: {exc}")
        return None, ""
    return _parse_check(_text_of(getattr(reply, "content", "")))


async def _round(
    bound: Any,
    messages: List[BaseMessage],
    tools: Dict[str, StructuredTool],
    max_steps: int,
    invoke_config: dict,
) -> tuple[str, int]:
    """One research round: model -> tool calls -> ... -> a reply with no tool call."""
    calls = 0
    for _ in range(max_steps + 1):
        reply = await bound.ainvoke(messages, config=invoke_config)
        messages.append(reply)
        tool_calls = list(getattr(reply, "tool_calls", None) or [])
        if not tool_calls:
            return _text_of(reply.content).strip(), calls
        for call in tool_calls:
            # Every call gets a reply, run or not, so the transcript stays valid for
            # providers that check tool_call / tool-message pairing.
            tool = tools.get(call.get("name") or "")
            if calls >= max_steps:
                output = "Not run: the search limit is reached."
            elif tool is None:
                output = f"Unknown tool {call.get('name')!r}; use {GREP_NAME} or {READ_NAME}."
            else:
                calls += 1
                try:
                    output = await asyncio.to_thread(tool.invoke, call.get("args") or {})
                except Exception as exc:
                    output = f"{tool.name} failed: {exc}"
            messages.append(
                ToolMessage(content=str(output), tool_call_id=call.get("id") or "", name=call.get("name"))
            )
        if calls >= max_steps:
            break
    # Out of steps: answer with what was found.
    messages.append(
        HumanMessage(
            content="You have used all your searches. Reply now, with no tool call, with what you found."
        )
    )
    reply = await bound.ainvoke(messages, config=invoke_config)
    messages.append(reply)
    return _text_of(reply.content).strip(), calls


async def research(
    model: Any,
    file: OutputFile,
    need: str,
    *,
    total_chars: int,
    invoke_config: Optional[Dict[str, Any]] = None,
    max_steps: Optional[int] = None,
    max_rounds: Optional[int] = None,
) -> Optional[Research]:
    """Run the search subagent over ``file``; ``None`` when it produced nothing usable."""
    invoke_config = dict(invoke_config or {})
    invoke_config.setdefault("run_name", "large_output_search")
    steps = int(max_steps if max_steps is not None else _cfg("search_max_steps", 8))
    rounds_cap = max(1, int(max_rounds if max_rounds is not None else _cfg("max_rounds", 2)))
    tools = file.tools()
    bound = model.bind_tools(tools)
    messages: List[BaseMessage] = [
        SystemMessage(content=_SUBAGENT_SYSTEM),
        HumanMessage(content=_task(need, total_chars, file.total_lines)),
    ]

    answer, total_calls = "", 0
    complete: Optional[bool] = None
    missing = ""
    rounds = 0
    for rounds in range(1, rounds_cap + 1):
        answer, calls = await _round(bound, messages, {t.name: t for t in tools}, steps, invoke_config)
        total_calls += calls
        if rounds == rounds_cap or not answer:
            break
        complete, missing = await _check(model, need, answer, invoke_config)
        logger.info("[large_output] round {} complete={} missing={!r}", rounds, complete, missing[:200])
        if complete is not False:
            break
        # Second loop: back to the file with the gap stated, keeping what was found.
        messages.append(
            HumanMessage(
                content=(
                    f"A reviewer says this is still missing or ambiguous: {missing or 'unspecified'}\n"
                    "Search the file again for exactly that, then reply with the complete findings "
                    "(include what you already found)."
                )
            )
        )
    if not answer:
        return None
    return Research(answer=answer, rounds=rounds, tool_calls=total_calls, complete=complete, missing=missing)


async def _research_with_timeout(
    model: Any, file: OutputFile, need: str, *, invoke_config: Optional[Dict[str, Any]], label: str
) -> Optional[Research]:
    timeout = float(_cfg("subagent_timeout", 120))
    try:
        return await asyncio.wait_for(
            research(model, file, need, total_chars=file.path.stat().st_size, invoke_config=invoke_config),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        logger.warning(f"large_output: search subagent timed out after {timeout}s on {label}")
    except Exception as exc:
        logger.warning(f"large_output: search subagent failed on {label}: {exc}")
    return None


def _cut_answer(answer: str) -> str:
    max_answer = int(_cfg("max_answer_chars", 4000))
    return answer if len(answer) <= max_answer else answer[:max_answer] + " …[findings cut]"


# ── the reply the agent sees ─────────────────────────────────────────────────


def saved_file_hint(file_name: str) -> str:
    """How the agent gets more out of a saved file, in the current mode."""
    if large_output_mode() == MODE_MAIN_AGENT:
        return (
            f'Search it with {GREP_SAVED_NAME}(file="{file_name}", pattern="...") and read around a match with '
            f'{READ_SAVED_NAME}(file="{file_name}", offset=..., limit=...). Search for the most distinctive '
            "term first; never read the whole file."
        )
    return f'Ask more about it with {QUERY_SAVED_OUTPUT_NAME}(file="{file_name}", question="...").'


def persisted_message(
    *,
    tool_name: str,
    path: Path,
    text: str,
    total_lines: int,
    findings: Optional[Research],
    saved_as: Optional[str] = None,
) -> str:
    """``<persisted-output>`` stub (free-code's shape) with the subagent's findings on top."""
    head, more = preview(text, int(_cfg("preview_chars", 2000)))
    lines = [
        PERSISTED_OUTPUT_TAG,
        f"Output of {tool_name} too large ({len(text):,} chars, {total_lines:,} lines). "
        f"Full output saved to: {path}",
    ]
    if findings is not None:
        lines += [
            "",
            "A search subagent read the saved output for what this step needs. Findings:",
            _cut_answer(findings.answer),
        ]
        if findings.complete is False and findings.missing:
            lines.append(f"(Still unresolved after {findings.rounds} round(s): {findings.missing})")
    if saved_as:
        lines += [
            "",
            f"The full result is in variable `{saved_as}` — search or process it in execute_python for "
            "anything the findings do not cover; do not print it whole.",
        ]
    lines += [
        "",
        saved_file_hint(path.name),
        "",
        f"Preview (first {len(head):,} chars):",
        head + ("\n..." if more else ""),
        PERSISTED_OUTPUT_CLOSING_TAG,
    ]
    return "\n".join(lines)


@dataclass
class Persisted:
    text: str  # the reply for the agent
    path: Path  # the saved file


async def handle_large_result(
    result: Any,
    *,
    tool_name: str,
    call_id: Optional[str],
    thread_id: Optional[str],
    messages: Sequence[BaseMessage],
    args: Any,
    model: Any,
    invoke_config: Optional[Dict[str, Any]] = None,
    saved_as: Optional[str] = None,
) -> Optional[Persisted]:
    """Save ``result`` to a file and return the reply for the agent; ``None`` = use the old path.

    The file is written first, so even when the subagent fails or times out the
    agent still gets the path and a preview rather than a silent cut.
    """
    text = as_searchable_text(result)
    try:
        path = spill_to_file(text, thread_id=thread_id, call_id=call_id)
        file = OutputFile(path, total_lines=text.count("\n") + 1)
    except Exception as exc:
        logger.warning(f"large_output: could not save {tool_name} output: {exc}")
        return None

    findings: Optional[Research] = None
    if model is not None and large_output_mode() == MODE_SUBAGENT:
        need = describe_need(messages, tool_name, args)
        findings = await _research_with_timeout(
            model, file, need, invoke_config=invoke_config, label=tool_name
        )
    logger.info(
        "[large_output] {} -> {} ({} chars); findings={} rounds={} calls={}",
        tool_name,
        file.path,
        len(text),
        findings is not None,
        findings.rounds if findings else 0,
        findings.tool_calls if findings else 0,
    )
    reply = persisted_message(
        tool_name=tool_name,
        path=file.path,
        text=text,
        total_lines=file.total_lines,
        findings=findings,
        saved_as=saved_as,
    )
    return Persisted(text=reply, path=file.path)


# ── follow-up questions: query_saved_output ──────────────────────────────────


class QuerySavedOutputInput(BaseModel):
    file: str = Field(
        ..., description="The saved file's name (or full path) from the <persisted-output> reply."
    )
    question: str = Field(..., description="What you need from that result now.")


async def _query_placeholder(**_: Any) -> str:  # pragma: no cover - tool_exec dispatches by name
    raise RuntimeError("query_saved_output is dispatched by tool_exec, not called directly")


def create_query_saved_output_tool() -> StructuredTool:
    """The bindable tool (its execution lives in :func:`query_saved_output`)."""
    return StructuredTool.from_function(
        coroutine=_query_placeholder,
        name=QUERY_SAVED_OUTPUT_NAME,
        description=QUERY_SAVED_OUTPUT_DESCRIPTION,
        args_schema=QuerySavedOutputInput,
    )


def saved_files(thread_id: Optional[str]) -> List[Path]:
    directory = spill_dir(thread_id)
    if not directory.is_dir():
        return []
    return sorted(directory.glob("*.txt"), key=lambda p: p.stat().st_mtime)


def resolve_saved_file(ref: str, thread_id: Optional[str]) -> Optional[Path]:
    """The saved file ``ref`` names — only ever one this thread saved, never an arbitrary path."""
    name = Path(str(ref or "").strip()).name
    if not name:
        return None
    candidates = [name] if name.endswith(".txt") else [name, f"{name}.txt"]
    by_name = {p.name: p for p in saved_files(thread_id)}
    for candidate in candidates:
        if candidate in by_name:
            return by_name[candidate]
    return None


async def query_saved_output(
    ref: str,
    question: str,
    *,
    thread_id: Optional[str],
    messages: Sequence[BaseMessage],
    model: Any,
    invoke_config: Optional[Dict[str, Any]] = None,
) -> tuple[str, bool]:
    """Answer ``question`` from a file this thread saved; returns ``(text, ok)``."""
    path = resolve_saved_file(ref, thread_id)
    if path is None:
        names = [p.name for p in saved_files(thread_id)]
        known = ", ".join(names[-10:]) if names else "none yet"
        return f"No saved output named {ref!r} in this conversation. Saved files: {known}.", False
    if not str(question or "").strip():
        return f"{QUERY_SAVED_OUTPUT_NAME} needs a non-empty question.", False
    if model is None:
        return f"{QUERY_SAVED_OUTPUT_NAME} is unavailable: no model is configured.", False

    request = ""
    for message in reversed(list(messages)):
        if isinstance(message, HumanMessage) and not is_harness_message(message):
            request = _text_of(message.content)
            break
    need = f"Question about the saved result:\n{_cut(question)}\n\nUser request:\n{_cut(request) or '(unavailable)'}"
    file = OutputFile(path)
    findings = await _research_with_timeout(
        model, file, need, invoke_config=invoke_config, label=f"{QUERY_SAVED_OUTPUT_NAME}({path.name})"
    )
    logger.info(
        "[large_output] query {} -> findings={} rounds={} calls={}",
        path.name,
        findings is not None,
        findings.rounds if findings else 0,
        findings.tool_calls if findings else 0,
    )
    if findings is None:
        return f"The search subagent could not answer from {path.name}. Try a more specific question.", False
    text = f"Findings from {path.name} ({file.total_lines:,} lines):\n{_cut_answer(findings.answer)}"
    if findings.complete is False and findings.missing:
        text += f"\n(Still unresolved after {findings.rounds} round(s): {findings.missing})"
    return text, True


# ── main_agent mode: the agent's own grep/read over saved files ──────────────


class GrepSavedInput(_GrepInput):
    file: str = Field(
        ..., description="The saved file's name (or full path) from the <persisted-output> reply."
    )


class ReadSavedInput(_ReadInput):
    file: str = Field(
        ..., description="The saved file's name (or full path) from the <persisted-output> reply."
    )


async def _saved_tool_placeholder(**_: Any) -> str:  # pragma: no cover - tool_exec dispatches by name
    raise RuntimeError("saved-output tools are dispatched by tool_exec, not called directly")


def create_saved_output_tools() -> List[StructuredTool]:
    """What the agent gets for saved results in the current mode (bound from the start of a run)."""
    if large_output_mode() == MODE_MAIN_AGENT:
        return [
            StructuredTool.from_function(
                coroutine=_saved_tool_placeholder,
                name=GREP_SAVED_NAME,
                description=(
                    "Search a large tool result that was saved to a file (a <persisted-output> reply names the "
                    "file) for a regex, literal if the regex is invalid. Returns matching lines with line numbers "
                    '("123: ..." for a match, "123- ..." for context) and the total match count.'
                ),
                args_schema=GrepSavedInput,
            ),
            StructuredTool.from_function(
                coroutine=_saved_tool_placeholder,
                name=READ_SAVED_NAME,
                description=(
                    "Read lines of a large tool result that was saved to a file, by 1-based line number. "
                    f"Use it around a {GREP_SAVED_NAME} match, not to read the whole file."
                ),
                args_schema=ReadSavedInput,
            ),
        ]
    return [create_query_saved_output_tool()]


def run_saved_file_tool(name: str, kwargs: Dict[str, Any], *, thread_id: Optional[str]) -> tuple[str, bool]:
    """``grep_saved_output`` / ``read_saved_output`` on a file this thread saved; ``(text, ok)``."""
    ref = str(kwargs.get("file") or "")
    path = resolve_saved_file(ref, thread_id)
    if path is None:
        names = [p.name for p in saved_files(thread_id)]
        known = ", ".join(names[-10:]) if names else "none yet"
        return f"No saved output named {ref!r} in this conversation. Saved files: {known}.", False
    args = {k: v for k, v in kwargs.items() if k != "file"}
    file = OutputFile(path)
    try:
        if name == GREP_SAVED_NAME:
            return file.grep(**args), True
        return file.read(**args), True
    except TypeError as exc:
        return f"{name} rejected these arguments: {exc}", False
