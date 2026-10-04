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
signatures), error replies, and anything shorter than ``min_chars``.
"""

from __future__ import annotations

import re
from typing import Callable, List, Optional

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.execute_python import EXECUTE_PYTHON_NAME
from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.tool_search import TOOL_SEARCH_NAME

CLEARED_SAVED = (
    "[Earlier result of {name} cleared from context ({chars} chars). The full result is saved as variable "
    "`{var}` — read it with execute_python if you need it again.]"
)
CLEARED_CODE = (
    "[Earlier execute_python output cleared from context ({chars} chars). Variables that code created "
    "still exist — print them from execute_python if you need them again.]"
)
CLEARED_FILE = (
    "[Earlier result of {name} cleared from context ({chars} chars). The full result is saved as file "
    '`{file}` — ask about it with query_saved_output(file="{file}", question=...).]'
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
    if name == EXECUTE_PYTHON_NAME:
        return CLEARED_CODE.format(chars=chars)
    artifact = message.artifact if isinstance(message.artifact, dict) else {}
    saved_file = artifact.get("saved_file")
    if isinstance(saved_file, str) and saved_file:
        return CLEARED_FILE.format(name=name, chars=chars, file=saved_file)
    var = saved_variable(message)
    if var and var_exists(var):
        return CLEARED_SAVED.format(name=name, chars=chars, var=var)
    return CLEARED_PLAIN.format(name=name, chars=chars)


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
        if i in protected or m.name == TOOL_SEARCH_NAME or m.status == "error":
            continue
        if not isinstance(m.content, str) or len(m.content) < max(min_chars, 1):
            continue
        out[i] = m.model_copy(update={"content": _stub(m, var_exists)})
    return out
