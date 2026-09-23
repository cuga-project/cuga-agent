"""Deterministic post-block pagination audit (#750).

Models read a *full* page (``len(items) == page_limit``) as "everything fits
in one page" and stop — the prompt says the opposite, and is ignored 3/3 on
some models. Prompt-only mitigation is unreliable, so this module makes the
signal observable: after each code block, list calls that returned exactly
``page_limit`` items without the next page ever being requested are flagged in
the execution output the model sees next turn.

Two pieces, both pure functions over tool-call records:

* :func:`describe_pagination` — computed once per call by the tracker and
  stored on the record as ``record["pagination"]``. Only the facts the audit
  needs (page index/limit, result length, a scope key) — never the payload — so
  it is cheap enough to record on every call, including timings-only mode.
* :func:`audit_pagination` — run by the sandbox node over the block's records;
  returns one note per unresolved full page.

The data returned to the code is never touched: a list response has to stay
a list for the generated code that iterates it. The note is appended to the
observation instead.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

# Recognized pagination argument names, most specific first. Bare ``limit`` /
# ``size`` are ambiguous on their own but harmless here: the audit is advisory
# and only fires when a response happened to have exactly that many items.
PAGE_INDEX_KEYS = ("page_index", "page_number", "page", "offset")
PAGE_LIMIT_KEYS = ("page_limit", "page_size", "per_page", "limit", "size")
# Cursor tokens are opaque and unordered: a cursor listing is followed by call
# order instead of by index, and there is no notion of a skipped page.
CURSOR_KEYS = ("cursor", "page_token", "page_cursor", "next_token", "after", "starting_after")

# Dict responses that wrap the list under a conventional key.
_LIST_WRAPPER_KEYS = ("items", "results", "data", "records", "entries", "values")

# Keys whose falsy value marks the last page explicitly (``has_more: false``,
# ``next_page: null``). Only an explicit terminal claim suppresses the audit; a
# missing key says nothing.
_TERMINAL_KEYS = ("has_more", "next_page", "next_cursor", "next_page_token", "next")

# Argument keys excluded from the scope key: they can rotate between calls
# (token refresh) without making the listing logically different.
_SCOPE_IGNORED_KEYS = frozenset({"access_token"})

_PREVIEW_MAX_CHARS = 160

NOTE_FOOTER = (
    "A full page is never proof of completeness. If this listing must be complete, keep "
    "requesting the next page (page_index + 1) until a page returns fewer than page_limit "
    "items or is empty, then combine all pages. If you only needed the first items "
    "(e.g. a sorted top-N), ignore this note."
)


def _as_int(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _first_int_key(args: Dict[str, Any], keys: tuple) -> Optional[str]:
    for key in keys:
        if key in args and _as_int(args[key]) is not None:
            return key
    return None


def list_length(result: Any) -> Optional[int]:
    """Length of a list-shaped result, or None when the result is not a listing.

    Accepts a bare list, a dict wrapping the list under a conventional key
    (``items``, ``results``, …) or with exactly one list-valued field, and a
    JSON string of a list. Error-shaped dicts are not listings.
    """
    if isinstance(result, str):
        text = result.lstrip()
        if not text.startswith(("[", "{")):
            return None
        try:
            result = json.loads(text)
        except (ValueError, TypeError):
            return None
    if isinstance(result, (list, tuple)):
        return len(result)
    if isinstance(result, dict):
        if result.get("status") == "exception" or "error" in result:
            return None
        for key in _LIST_WRAPPER_KEYS:
            if isinstance(result.get(key), list):
                return len(result[key])
        list_fields = [v for v in result.values() if isinstance(v, list)]
        if len(list_fields) == 1:
            return len(list_fields[0])
    return None


def _explicit_terminal(result: Any) -> bool:
    """True when the response itself says this is the last page."""
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except (ValueError, TypeError):
            return False
    if not isinstance(result, dict):
        return False
    return any(key in result and not result[key] for key in _TERMINAL_KEYS)


def _args_preview(args: Dict[str, Any], redact_values: bool) -> str:
    parts = []
    for key, value in args.items():
        if key in _SCOPE_IGNORED_KEYS:
            continue
        if (
            redact_values
            and key not in PAGE_INDEX_KEYS
            and key not in PAGE_LIMIT_KEYS
            and key not in CURSOR_KEYS
        ):
            parts.append(f"{key}=…")
        else:
            parts.append(f"{key}={value!r}")
    preview = ", ".join(parts)
    if len(preview) > _PREVIEW_MAX_CHARS:
        preview = preview[: _PREVIEW_MAX_CHARS - 1] + "…"
    return preview


def describe_pagination(
    arguments: Optional[Dict[str, Any]],
    result: Any,
    arg_defaults: Optional[Dict[str, Any]] = None,
    redact_values: bool = False,
) -> Optional[Dict[str, Any]]:
    """Compact pagination facts for one call, or None when it has no page size.

    ``arg_defaults`` supplies schema defaults for arguments the model omitted
    (AppWorld's ``page_limit`` defaults to 5): an omitted page size is still a
    page size. ``redact_values`` keeps non-pagination argument values out of
    the stored preview (timings-only tracking must not capture payloads).
    """
    if not isinstance(arguments, dict):
        return None
    args = {**(arg_defaults or {}), **arguments}
    limit_key = _first_int_key(args, PAGE_LIMIT_KEYS)
    if limit_key is None:
        return None
    limit = _as_int(args[limit_key])
    if limit is None or limit <= 0:
        return None
    index_key = _first_int_key(args, PAGE_INDEX_KEYS)
    cursor_key = next((k for k in CURSOR_KEYS if k in args), None)
    page_keys = {limit_key, index_key, cursor_key} - {None}
    if index_key:
        index: Optional[int] = _as_int(args[index_key])
    elif cursor_key:
        index_key, index = cursor_key, None  # ordered by call sequence at audit time
    else:
        index = 0
    scope_args = {k: v for k, v in args.items() if k not in page_keys and k not in _SCOPE_IGNORED_KEYS}
    # The scope is only a grouping key, so it is stored as a fingerprint: the
    # record is persisted in timings-only mode too, which must not carry
    # argument values.
    scope = hashlib.sha256(json.dumps(scope_args, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    return {
        "limit_key": limit_key,
        "limit": limit,
        "index_key": index_key or "page_index",
        "index": index,
        "scope": scope,
        "result_len": list_length(result),
        "terminal": _explicit_terminal(result),
        "args_preview": _args_preview(args, redact_values),
    }


def audit_pagination(tool_calls: List[Dict[str, Any]]) -> List[str]:
    """Return one note per listing left on a full page with no next page requested.

    A *listing* is (app, tool, every argument except the page ones). It is
    flagged when some call returned exactly ``limit`` items and, within these
    records, no call went to a higher page, no call saw a short page and no
    response declared itself the last page (``has_more: false``). A page that
    was skipped on the way to a higher one is flagged as well.
    """
    groups: Dict[tuple, List[Dict[str, Any]]] = {}
    for call in tool_calls or []:
        info = call.get("pagination")
        if not info or call.get("error"):
            continue
        key = (call.get("app_name"), call.get("name"), info["scope"])
        groups.setdefault(key, []).append(info)

    notes: List[str] = []
    for (_app, tool_name, _scope), infos in groups.items():
        # Cursor listings carry no index: their order is the call order.
        by_cursor = any(i["index"] is None for i in infos)
        if by_cursor:
            infos = [dict(i, index=n) for n, i in enumerate(infos)]
        full = [i for i in infos if i["result_len"] is not None and i["result_len"] == i["limit"]]
        if not full:
            continue
        if any(i.get("terminal") for i in infos):
            continue  # the API said so explicitly (has_more: false / next_page: null)
        last_full = max(full, key=lambda i: i["index"])
        skipped = None if by_cursor else _skipped_page(infos, last_full)
        if skipped is None:
            if any(i["result_len"] is not None and i["result_len"] < i["limit"] for i in infos):
                continue  # a short page was seen: the listing was (or is being) exhausted
            if any(i["index"] > last_full["index"] for i in infos):
                continue  # the model did advance past the full page
        repeats = sum(1 for i in full if i["index"] == last_full["index"])
        step = last_full["limit"] if last_full["index_key"] == "offset" else 1
        if by_cursor:
            next_page = f"the next {last_full['index_key']}"
            repeats = sum(1 for i in full if i["args_preview"] == last_full["args_preview"])
        else:
            next_page = (
                f"{last_full['index_key']}={skipped if skipped is not None else last_full['index'] + step}"
            )
        note = (
            f"`{tool_name}({last_full['args_preview']})` returned exactly {last_full['limit']} items "
            f"(a full page) and {next_page} was never requested."
        )
        if repeats > 1:
            note += f" The same full page was fetched {repeats} times without advancing."
        notes.append(note)
    return notes


def _skipped_page(infos: List[Dict[str, Any]], last_full: Dict[str, Any]) -> Optional[int]:
    """First page index that was jumped over after a full page, if any.

    Page-number keys advance by 1; ``offset`` advances by the page size, so a
    gap is only judged there when every call used the same limit.
    """
    step = 1
    if last_full["index_key"] == "offset":
        limits = {i["limit"] for i in infos}
        if len(limits) != 1:
            return None
        step = last_full["limit"]
    indices = {i["index"] for i in infos}
    expected = last_full["index"] + step
    while expected < max(indices):
        if expected not in indices:
            return expected
        expected += step
    return None


def render_pagination_notes(notes: List[str]) -> str:
    """Format audit notes as the block appended to the execution output."""
    if not notes:
        return ""
    lines = ["⚠ Pagination check:"] + [f"- {note}" for note in notes] + [NOTE_FOOTER]
    return "\n".join(lines)
