"""What a tool call *does*: paging (partial results) and state changes (writes).

Two failure shapes from the AppWorld hybrid runs, both about the model misjudging
the effect of calls it made:

- **Partial pages.** A native call to a paged tool with its default page size (often 5)
  returns one page; the model then counted or acted on it as if it were everything
  (priority-1 threads: 1 instead of 15; "mark old threads read": nothing marked).
  :func:`page_note` flags a full page so the reply says more probably exist.
- **Nothing done.** On action requests the model sometimes ends with "N/A" having made
  no state-changing call at all. :func:`is_write_tool` classifies registry tools by HTTP
  method so the loop can notice a turn that changed nothing.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.stop_checks import register_stop_check

WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# POSTs that authenticate rather than change user data.
_NON_WRITE_MARKERS = ("login", "logout", "auth", "token", "signup")

PAGE_FIELDS = ("page_index", "page", "offset")
LIMIT_FIELDS = ("page_limit", "limit", "per_page", "page_size")


def _attr(tool: Any, name: str) -> str:
    for target in (tool, getattr(tool, "func", None), getattr(tool, "coroutine", None)):
        value = getattr(target, name, None)
        if isinstance(value, str) and value:
            return value
    return ""


def is_write_tool(tool: Any) -> bool:
    """True for registry tools whose HTTP method changes state (auth endpoints excluded)."""
    method = _attr(tool, "_http_method").upper()
    if method not in WRITE_METHODS:
        return False
    where = f"{getattr(tool, 'name', '')} {_attr(tool, '_http_path')}".lower()
    return not any(marker in where for marker in _NON_WRITE_MARKERS)


def _field_default(schema: Any, name: str) -> Any:
    field = getattr(schema, "model_fields", {}).get(name)
    return getattr(field, "default", None) if field is not None else None


def paging(tool: Any, kwargs: Dict[str, Any]) -> Optional[tuple]:
    """``(page_field, page_value, limit_value)`` for a paged tool, else ``None``."""
    schema = getattr(tool, "args_schema", None)
    fields = getattr(schema, "model_fields", {}) or {}
    page_field = next((f for f in PAGE_FIELDS if f in fields), None)
    limit_field = next((f for f in LIMIT_FIELDS if f in fields), None)
    if not page_field or not limit_field:
        return None
    limit = kwargs.get(limit_field, _field_default(schema, limit_field))
    page = kwargs.get(page_field, _field_default(schema, page_field)) or 0
    try:
        return page_field, int(page), int(limit)
    except (TypeError, ValueError):
        return None


PARTIAL_PAGE_PREFIX = "[Partial result: "


def page_note(tool: Any, kwargs: Dict[str, Any], result: Any, *, hybrid: bool) -> Optional[str]:
    """A warning when a paged call returned a *full* page — there are probably more."""
    info = paging(tool, kwargs)
    if info is None or not isinstance(result, list) or not info[2] or len(result) < info[2]:
        return None
    page_field, page, limit = info
    how = (
        "fetch every page in execute_python (loop until a page comes back empty)"
        if hybrid
        else f"request {page_field}={page + 1} and onwards until a page comes back empty"
    )
    return (
        f"{PARTIAL_PAGE_PREFIX}this is {page_field}={page} with {limit} items per page, and the page is full — "
        f"there are probably more. Before counting, filtering or acting on all of them, {how}.]"
    )


# ── "did this turn change anything?" ─────────────────────────────────────────

NOOP_ANSWER_CORRECTION_KEY = "fc_noop_answer_retry"

NOOP_ANSWER_CORRECTION = (
    "You are finishing with \"{answer}\", but nothing was changed in this turn — no create, update, delete, "
    "send, move, order or post call succeeded. If the request asks you to do something, do it now: find the "
    "tools that perform it (search by the resource name if needed), gather what they need, and call them. "
    "If the request is only a question, answer it with the value instead of \"{answer}\"."
)

_NOOP_ANSWERS = {"", "n/a", "na", "none", "null", "-", "nothing"}


def is_noop_answer(text: str) -> bool:
    return (text or "").strip().strip(".").strip().lower() in _NOOP_ANSWERS


def _role(message: Any) -> str:
    return str(getattr(message, "type", "") or (message.get("type") if isinstance(message, dict) else ""))


def _get(message: Any, key: str) -> Any:
    return message.get(key) if isinstance(message, dict) else getattr(message, key, None)


def current_turn_start(messages: Any) -> int:
    """Index just past the user's latest message (runtime notes do not start a turn)."""
    from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.harness_messages import is_harness_message

    messages = list(messages or [])
    for i in range(len(messages) - 1, -1, -1):
        if _role(messages[i]) == "human" and not is_harness_message(messages[i]):
            return i + 1
    return 0


def turn_made_a_write(messages: Any, turn_start: int, tools_by_name: Dict[str, Any]) -> bool:
    """True if a state-changing tool ran this turn — natively or from ``execute_python`` code."""
    for message in list(messages)[turn_start:]:
        if _role(message) != "tool" or _get(message, "status") == "error":
            continue
        names = [_get(message, "name")]
        artifact = _get(message, "artifact")
        if isinstance(artifact, dict):
            names += list(artifact.get("code_calls") or [])
        if any(is_write_tool(tools_by_name.get(n)) for n in names if n):
            return True
    return False


def has_write_tools(tools_by_name: Dict[str, Any]) -> bool:
    return any(is_write_tool(t) for t in tools_by_name.values())


# ── stop check: a full page was the last one read ────────────────────────────

UNFINISHED_PAGING = (
    "the last page you read of {tool} ({page_field}={page}) was full, and you did not fetch the next one, "
    "so there are probably more items. If your answer depends on all of them — counting, filtering, finding "
    "a particular one, or acting on every one — fetch the remaining pages first{how}. If that page was enough "
    "(for example you only needed the most recent item), give your answer as it is."
)


def unfinished_paging(ctx: Any) -> Optional[str]:
    """Stop check: a native paged read in this turn returned a full page and nothing read further.

    "Further" means a later native call to the same tool with a higher page and otherwise the
    same arguments, or an ``execute_python`` block that called the tool (code paginates itself).
    Full pages are the ones ``page_note`` flagged. Native calls only; CodeAct turns have none.
    """
    from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.execute_python import EXECUTE_PYTHON_NAME

    messages = list(ctx.messages or [])
    start = current_turn_start(messages)
    calls: Dict[str, tuple] = {}
    events: list = []  # (kind, name, args, page) in order
    for message in messages[start:]:
        for call in _get(message, "tool_calls") or []:
            if isinstance(call, dict) and call.get("id"):
                args = call.get("args") if isinstance(call.get("args"), dict) else {}
                calls[call["id"]] = (str(call.get("name") or ""), args)
        if _role(message) != "tool" or _get(message, "status") == "error":
            continue
        name, args = calls.get(_get(message, "tool_call_id"), (_get(message, "name") or "", {}))
        if name == EXECUTE_PYTHON_NAME:
            artifact = _get(message, "artifact")
            for called in (artifact.get("code_calls") or []) if isinstance(artifact, dict) else []:
                events.append(("code", called, {}, None))
            continue
        info = paging(ctx.tools_by_name.get(name), args)
        if info is None:
            continue
        content = _get(message, "content")
        full = isinstance(content, str) and PARTIAL_PAGE_PREFIX in content
        events.append(("page", name, args, (info, full)))

    hybrid = ctx.execution_mode == "hybrid"
    for index, (kind, name, args, detail) in enumerate(events):
        if kind != "page" or not detail[1]:
            continue
        (page_field, page, _limit) = detail[0]
        rest = {k: v for k, v in args.items() if k not in PAGE_FIELDS + LIMIT_FIELDS}
        continued = False
        for later_kind, later_name, later_args, later_detail in events[index + 1 :]:
            if later_name != name:
                continue
            if later_kind == "code":
                continued = True
                break
            later_rest = {k: v for k, v in later_args.items() if k not in PAGE_FIELDS + LIMIT_FIELDS}
            if later_rest == rest and later_detail[0][1] > page:
                continued = True
                break
        if not continued:
            how = (
                " in execute_python (loop until a page comes back empty)"
                if hybrid
                else f" ({page_field}={page + 1} and onwards until a page comes back empty)"
            )
            return UNFINISHED_PAGING.format(tool=name, page_field=page_field, page=page, how=how)
    return None


register_stop_check("unfinished_paging", unfinished_paging)
