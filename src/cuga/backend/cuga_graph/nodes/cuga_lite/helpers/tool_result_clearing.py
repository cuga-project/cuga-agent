"""Clear old tool results from the outbound transcript (Claude Code's microcompact).

A native tool result can be up to ``execution_output_max_length`` (70K) characters, and it
stays in the history — resent on every model call — until summarization fires at 70% of
the context window. Most of it is never read again: the model used it on the step after
it arrived. free-code (``services/compact/microCompact.ts``) replaces old results of
compactable tools with a stub and keeps only the most recent few.

Hybrid mode makes this lossless where free-code's is not: every native result is also
saved as a variable (``save_native_result``), so the stub names the variable and
``execute_python`` can still read the full data. ``execute_python`` output is printed
text, not saved, but the variables the code created persist.

Only the outbound copy changes; persisted history keeps every result (summarization,
eval traces and replay still see them). A cleared message stays cleared on every later
call, so the provider's prompt cache is only invalidated at the boundary. Never cleared:
results the model has not answered yet (everything after the last assistant turn), the
``keep_recent`` newest results, ``tool_search`` results (small, and they carry the loaded
signatures), and anything shorter than ``min_chars``. Old error replies are not cleared but
shortened to their opening and last line (the model must still know what failed). A cleared
``execute_python`` reply names the variables the block created and their shapes.
"""

from __future__ import annotations

import re
from typing import Callable, List, Optional

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.execute_python import EXECUTE_PYTHON_NAME
from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.large_output import saved_file_hint
from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.tool_search import TOOL_SEARCH_NAME

CLEARED_SAVED = (
    "[Earlier result of {name} cleared from context ({chars} chars). The full result is saved as variable "
    "`{var}` — read it with execute_python if you need it again.]"
)
CLEARED_CODE = (
    "[Earlier execute_python output cleared from context ({chars} chars). Variables that code created "
    "still exist — print them from execute_python if you need them again.]"
)
CLEARED_CODE_VARS = (
    "[Earlier execute_python output cleared from context ({chars} chars). The code created: {variables} — "
    "they still exist; print them from execute_python if you need them again.]"
)
SHORTENED_ERROR = "[Earlier error shortened ({chars} chars)] {head} ...{tail}"

#: An old error reply longer than this is cut to its first and last lines.
ERROR_KEEP_CHARS = 800
CLEARED_FILE = (
    "[Earlier result of {name} cleared from context ({chars} chars). The full result is saved as file "
    "`{file}`. {hint}]"
)
CLEARED_PLAIN = (
    "[Earlier result of {name} cleared from context ({chars} chars). Call the tool again if you need it.]"
)

_SAVED_AS = re.compile(r"\[Result saved as variable `([^`]+)`")


def saved_variable(message: ToolMessage) -> Optional[str]:
    """The variable a native result was saved as: the artifact, else the reply's own note."""
    artifact = message.artifact if isinstance(message.artifact, dict) else {}
    name = artifact.get("saved_as")
    if isinstance(name, str) and name:
        return name
    content = message.content if isinstance(message.content, str) else ""
    match = _SAVED_AS.search(content)
    return match.group(1) if match else None


def _stub(message: ToolMessage, var_exists: Callable[[str], bool]) -> str:
    chars = len(message.content)
    name = message.name or "tool"
    artifact = message.artifact if isinstance(message.artifact, dict) else {}
    if name == EXECUTE_PYTHON_NAME:
        variables = artifact.get("variables")
        if isinstance(variables, dict) and variables:
            listed = ", ".join(f"`{k}` ({v})" for k, v in variables.items())
            return CLEARED_CODE_VARS.format(chars=chars, variables=listed)
        return CLEARED_CODE.format(chars=chars)
    saved_file = artifact.get("saved_file")
    if isinstance(saved_file, str) and saved_file:
        return CLEARED_FILE.format(name=name, chars=chars, file=saved_file, hint=saved_file_hint(saved_file))
    var = saved_variable(message)
    if var and var_exists(var):
        return CLEARED_SAVED.format(name=name, chars=chars, var=var)
    return CLEARED_PLAIN.format(name=name, chars=chars)


def _shortened_error(message: ToolMessage) -> Optional[str]:
    """An old error keeps its opening (what failed) and its last line (the exception), not the middle.

    Errors are never cleared outright — the model needs to remember what already failed —
    but a long traceback or a dumped payload is only worth its first and last lines once
    the model has moved on.
    """
    content = message.content if isinstance(message.content, str) else ""
    if len(content) <= ERROR_KEEP_CHARS:
        return None
    lines = [line for line in content.strip().splitlines() if line.strip()]
    head = content.strip()[:300].rstrip()
    tail = lines[-1].strip()[:300] if lines else ""
    if tail and tail in head:
        tail = ""
    return SHORTENED_ERROR.format(chars=len(content), head=head, tail=f" {tail}" if tail else "")


def clear_old_tool_results(
    messages: List[BaseMessage],
    *,
    keep_recent: int,
    min_chars: int,
    var_exists: Optional[Callable[[str], bool]] = None,
) -> List[BaseMessage]:
    """A copy of ``messages`` with old, large tool results replaced by a short stub.

    ``keep_recent <= 0`` turns clearing off. ``var_exists`` checks that a saved variable is
    still in the store before the stub points at it (default: trust the note).
    """
    if keep_recent <= 0:
        return list(messages)
    var_exists = var_exists or (lambda _name: True)

    last_ai = max((i for i, m in enumerate(messages) if isinstance(m, AIMessage)), default=-1)
    tool_positions = [i for i, m in enumerate(messages) if isinstance(m, ToolMessage)]
    protected = set(tool_positions[-keep_recent:]) | {i for i in tool_positions if i > last_ai}

    out: List[BaseMessage] = list(messages)
    for i in tool_positions:
        m = out[i]
        if i in protected or m.name == TOOL_SEARCH_NAME:
            continue
        failed_code = m.name == EXECUTE_PYTHON_NAME and "Error during execution" in str(m.content)
        if m.status == "error" or failed_code:
            # A failed block reports as normal output; it is an error all the same.
            shortened = _shortened_error(m)
            if shortened is not None:
                out[i] = m.model_copy(update={"content": shortened})
            continue
        if not isinstance(m.content, str) or len(m.content) < max(min_chars, 1):
            continue
        out[i] = m.model_copy(update={"content": _stub(m, var_exists)})
    return out
