"""Repeated native calls: notice a loop the model is running one round-trip at a time.

The shape: the same tool, called again and again, with arguments that differ in at most
one field — ``get_order(order_id=1)``, ``get_order(order_id=2)``, … or ``list_threads(
page_index=0)``, ``(page_index=1)``, … — or with identical arguments, which returns the
same result every time. In hybrid mode that work belongs in one ``execute_python`` call;
in plain function calling a bulk endpoint or a different approach usually exists.

Claude Code has no equivalent (its only backstop is ``maxTurns``); this is the runtime
escalation observation from ``feature/hybrid-cuga-agent`` (``runtime_escalation.py``),
ported to the native loop. It nudges, never switches: the model decides. One nudge per
streak, keyed by the streak's first call id — the ported version compared lengths, so a
growing streak was re-nudged on every extra call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from cuga.backend.cuga_graph.nodes.cuga_agent_core.execution.todos import TODOS_TOOL_NAME
from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.execute_python import EXECUTE_PYTHON_NAME
from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.tool_search import TOOL_SEARCH_NAME

REPEAT_NUDGE_KEY = "fc_repeat_nudged"

REPEAT_NUDGE_HYBRID = (
    "You have called {tool} {length} times in a row with only `{arg}` changing — a loop over a collection, "
    "one round-trip per item. Do the remaining items in ONE execute_python call: loop over them in code "
    "(every tool is an async function there, and earlier results are saved as variables) and print only "
    "what you need."
)

REPEAT_NUDGE_FC = (
    "You have called {tool} {length} times in a row with only `{arg}` changing. If you are stepping through "
    "a collection, look for a tool that returns it in one call (a list, search or bulk endpoint, or a larger "
    "page size) before continuing one item at a time."
)

REPEAT_NUDGE_IDENTICAL = (
    "You have called {tool} {length} times in a row with the same arguments; the result will not change. "
    "Use the result you already have, or change the arguments or the tool."
)

_BREAK = object()  # an execute_python call ends any native streak


@dataclass(frozen=True)
class RepeatStreak:
    tool_name: str
    length: int
    first_call_id: str
    #: The argument that changes between calls; ``None`` when the calls are identical.
    varying_arg: Optional[str]


def _field(message: Any, key: str) -> Any:
    return message.get(key) if isinstance(message, dict) else getattr(message, key, None)


def _calls(messages: Sequence[Any]) -> List[Any]:
    """Native calls in order, as ``(name, args, id)``; ``tool_search`` and
    ``create_update_todos`` are skipped (they are not work — a plan update between two
    item lookups must not hide the loop), ``execute_python`` is a break."""
    out: List[Any] = []
    for message in messages:
        for index, call in enumerate(_field(message, "tool_calls") or []):
            if not isinstance(call, dict):
                continue
            name = str(call.get("name") or "")
            if not name or name in (TOOL_SEARCH_NAME, TODOS_TOOL_NAME):
                continue
            if name == EXECUTE_PYTHON_NAME:
                out.append(_BREAK)
                continue
            args = call.get("args")
            out.append((name, args if isinstance(args, dict) else {}, str(call.get("id") or f"call_{index}")))
    return out


def _difference(a: Dict[str, Any], b: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """``(similar, varying_key)``: same keys, and at most one value differs."""
    if set(a) != set(b):
        return False, None
    changed = [key for key in a if a[key] != b[key]]
    if len(changed) > 1:
        return False, None
    return True, (changed[0] if changed else None)


def detect_repeat_streak(messages: Sequence[Any]) -> Optional[RepeatStreak]:
    """The run of similar calls to one tool that ends at the most recent native call.

    Calls in a run either all repeat the same arguments or all vary the same single
    argument; mixing the two, or varying a different field, ends the run.
    """
    calls = _calls(messages)
    if len(calls) < 2 or calls[-1] is _BREAK:
        return None
    name, args, first_id = calls[-1]
    length, varying = 1, None
    index = len(calls) - 1
    while index > 0:
        prev = calls[index - 1]
        if prev is _BREAK or prev[0] != name:
            break
        similar, key = _difference(prev[1], args)
        if not similar or (length > 1 and key != varying):
            break
        varying = key
        length += 1
        args, first_id = prev[1], prev[2]
        index -= 1
    if length < 2:
        return None
    return RepeatStreak(tool_name=name, length=length, first_call_id=first_id, varying_arg=varying)


def repeat_nudge_text(streak: RepeatStreak, *, hybrid: bool) -> str:
    if streak.varying_arg is None:
        return REPEAT_NUDGE_IDENTICAL.format(tool=streak.tool_name, length=streak.length)
    template = REPEAT_NUDGE_HYBRID if hybrid else REPEAT_NUDGE_FC
    return template.format(tool=streak.tool_name, length=streak.length, arg=streak.varying_arg)
