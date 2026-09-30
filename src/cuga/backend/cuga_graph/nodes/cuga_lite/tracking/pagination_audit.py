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
  returns one note per unresolved listing.

The audit only speaks about tools that demonstrably paginate: the schema
declares a page-index or cursor parameter, the call passed one, or the
response carries a continuation / end-of-list field. A bare ``limit`` or
``size`` on its own (``search(limit=10)``, ``sample(size=20)``) is not a
pagination contract and is never audited, so a note can only ever name a
parameter the tool actually has. Explicit continuation and end signals take
precedence over page length.

The data returned to the code is never touched: a list response has to stay
a list for the generated code that iterates it. The note is appended to the
observation instead.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

# Recognized pagination argument names, most specific first.
PAGE_INDEX_KEYS = ("page_index", "page_number", "page", "offset")
PAGE_LIMIT_KEYS = ("page_limit", "page_size", "per_page", "limit", "size")
# Cursor tokens are opaque and unordered: a cursor listing is followed by call
# order instead of by index, and there is no notion of a skipped page.
CURSOR_KEYS = ("cursor", "page_token", "page_cursor", "next_token", "after", "starting_after")

# Dict responses that wrap the list under a conventional key.
_LIST_WRAPPER_KEYS = ("items", "results", "data", "records", "entries", "values")

# Response fields that state whether more pages exist. Booleans say it
# directly; token fields say "more" when truthy and "end" when empty/null.
_MORE_BOOL_KEYS = ("has_more", "hasMore", "has_next_page", "hasNextPage", "has_next")
_MORE_TOKEN_KEYS = (
    "next_cursor",
    "nextCursor",
    "next_page_token",
    "nextPageToken",
    "next_token",
    "end_cursor",
    "endCursor",
    "next_page",
    "next",
    "cursor",
)
# Containers pagination metadata is commonly nested under (Slack's
# ``response_metadata.next_cursor``, GraphQL's ``pageInfo.hasNextPage``).
_META_CONTAINER_KEYS = (
    "response_metadata",
    "metadata",
    "meta",
    "paging",
    "pagination",
    "page_info",
    "pageInfo",
)

# Argument keys excluded from the scope key: they can rotate between calls
# (token refresh) without making the listing logically different.
_SCOPE_IGNORED_KEYS = frozenset({"access_token"})

_PREVIEW_MAX_CHARS = 160

NOTE_FOOTER = (
    "A full page alone does not prove completeness. If this listing must be complete, keep "
    "requesting the next page until a page returns fewer items than the limit with no "
    "continuation token (or the response says there are no more pages), then combine all "
    "pages. If you only needed the first items (e.g. a sorted top-N), ignore this note."
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


def _as_dict(result: Any) -> Optional[Dict[str, Any]]:
    if isinstance(result, str):
        text = result.lstrip()
        if not text.startswith("{"):
            return None
        try:
            result = json.loads(text)
        except (ValueError, TypeError):
            return None
    return result if isinstance(result, dict) else None


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


def continuation(result: Any) -> Tuple[Optional[bool], Optional[str]]:
    """What the response says about further pages: ``(more, field)``.

    ``more`` is True when the response claims more pages exist, False when it
    claims this is the last page, None when it says nothing. ``field`` is the
    response key that carried the claim (``next_cursor``, ``has_more``, …),
    searched at the top level and one level down inside the usual metadata
    containers.
    """
    body = _as_dict(result)
    if body is None:
        return None, None
    containers = [body] + [body[k] for k in _META_CONTAINER_KEYS if isinstance(body.get(k), dict)]
    for container in containers:
        for key in _MORE_BOOL_KEYS:
            if key in container and isinstance(container[key], bool):
                return container[key], key
        for key in _MORE_TOKEN_KEYS:
            if key in container:
                return bool(container[key]), key
    return None, None


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
    param_names: Optional[List[str]] = None,
) -> Optional[Dict[str, Any]]:
    """Compact pagination facts for one call, or None when the call is not a
    page of a paginated listing.

    A call is audited only with evidence of a pagination contract: the tool's
    schema (``param_names``) declares a page-index or cursor parameter, the
    call passed one, or the response carries a continuation / end-of-list
    field. ``arg_defaults`` supplies schema defaults for arguments the model
    omitted (AppWorld's ``page_limit`` defaults to 5): an omitted page size is
    still a page size. ``redact_values`` keeps non-pagination argument values
    out of the stored preview (timings-only tracking must not capture
    payloads).
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

    more, more_field = continuation(result)
    index_key = _first_int_key(args, PAGE_INDEX_KEYS)
    cursor_key = next((k for k in CURSOR_KEYS if k in args), None)
    schema = set(param_names or ())
    if index_key is None and cursor_key is None and schema:
        # The model omitted the page argument; the schema still names it.
        index_key = next((k for k in PAGE_INDEX_KEYS if k in schema), None)
        if index_key is None:
            cursor_key = next((k for k in CURSOR_KEYS if k in schema), None)
    if index_key is None and cursor_key is None and more is None:
        return None  # bare limit/size with nothing that says "pages": not a contract

    page_keys = {limit_key, index_key, cursor_key} - {None}
    cursor_from = None
    if index_key:
        index: Optional[int] = _as_int(args.get(index_key)) or 0
    elif cursor_key:
        index_key, index = cursor_key, None  # ordered by call sequence at audit time
    else:
        # Only the response says this paginates (first request of a cursor
        # listing carries no cursor argument).
        cursor_from = more_field
        index_key, index = "cursor", None
    scope_args = {k: v for k, v in args.items() if k not in page_keys and k not in _SCOPE_IGNORED_KEYS}
    # The scope is only a grouping key, so it is stored as a fingerprint: the
    # record is persisted in timings-only mode too, which must not carry
    # argument values.
    scope = hashlib.sha256(json.dumps(scope_args, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    return {
        "limit_key": limit_key,
        "limit": limit,
        "index_key": index_key,
        "index": index,
        "cursor_from": cursor_from,
        "scope": scope,
        "result_len": list_length(result),
        "continues": more is True,
        "terminal": more is False,
        "more_field": more_field,
        "args_preview": _args_preview(args, redact_values),
    }


def audit_pagination(tool_calls: List[Dict[str, Any]]) -> List[str]:
    """Return one note per listing left unresolved within these records.

    A *listing* is (app, tool, every argument except the page ones). It is
    flagged when its latest response says more pages exist, or when some call
    returned exactly ``limit`` items and, within these records, no successful
    call went to a higher page, no call saw a short page and no response
    declared itself the last page. A page skipped on the way to a higher one
    is flagged as well. Failed calls are not progress and are ignored.
    """
    groups: Dict[tuple, List[Dict[str, Any]]] = {}
    for call in tool_calls or []:
        info = call.get("pagination")
        if not info or call.get("error") or call.get("succeeded") is False:
            continue
        key = (call.get("app_name"), call.get("name"), info["scope"])
        groups.setdefault(key, []).append(info)

    notes: List[str] = []
    for (_app, tool_name, _scope), infos in groups.items():
        # Cursor listings carry no index: their order is the call order.
        by_cursor = any(i["index"] is None for i in infos)
        if by_cursor:
            infos = [dict(i, index=n) for n, i in enumerate(infos)]
        latest = max(infos, key=lambda i: i["index"])
        step = latest["limit"] if latest["index_key"] == "offset" else 1

        # An explicit "more pages" claim on the latest response beats length:
        # a short page with a continuation token is not the end.
        if latest.get("continues"):
            notes.append(
                f"`{tool_name}({latest['args_preview']})` returned "
                f"{latest['result_len'] if latest['result_len'] is not None else 'a page of'} items and its "
                f"response says more pages exist (`{latest['more_field']}`), but "
                f"{_next_page_wording(latest, by_cursor, step, None)} was never requested."
            )
            continue

        full = [i for i in infos if i["result_len"] is not None and i["result_len"] == i["limit"]]
        if not full:
            continue
        last_full = max(full, key=lambda i: i["index"])
        # A skipped page is judged first: a later short or terminal page proves
        # nothing about rows that were never read.
        skipped = None if by_cursor else _skipped_page(infos)
        if skipped is None:
            if any(i.get("terminal") for i in infos):
                continue  # the API said so explicitly (has_more: false / next_cursor: "")
            if any(i["result_len"] is not None and i["result_len"] < i["limit"] for i in infos):
                continue  # a short page was seen: the listing was (or is being) exhausted
            if any(i["index"] > last_full["index"] and i["result_len"] is not None for i in infos):
                continue  # the model did advance past the full page and got a page back
        if by_cursor:
            repeats = sum(1 for i in full if i["args_preview"] == last_full["args_preview"])
        else:
            repeats = sum(1 for i in full if i["index"] == last_full["index"])
        note = (
            f"`{tool_name}({last_full['args_preview']})` returned exactly {last_full['limit']} items "
            f"(a full page) and {_next_page_wording(last_full, by_cursor, step, skipped)} was never requested."
        )
        if repeats > 1:
            note += f" The same full page was fetched {repeats} times without advancing."
        notes.append(note)
    return notes


def _next_page_wording(info: Dict[str, Any], by_cursor: bool, step: int, skipped: Optional[int]) -> str:
    if by_cursor:
        source = info.get("cursor_from") or info.get("more_field")
        return f"the next {info['index_key']}" + (f" (from `{source}`)" if source else "")
    return f"{info['index_key']}={skipped if skipped is not None else info['index'] + step}"


def _skipped_page(infos: List[Dict[str, Any]]) -> Optional[int]:
    """First page index jumped over between the lowest and highest page read.

    Starts at the lowest index the block read (a listing resumed at page 2 is
    not missing pages 0-1). Page-number keys advance by 1; ``offset`` advances
    by the page size, so a gap is only judged there when every call used the
    same limit.
    """
    step = 1
    if infos[0]["index_key"] == "offset":
        limits = {i["limit"] for i in infos}
        if len(limits) != 1:
            return None
        step = infos[0]["limit"]
    indices = {i["index"] for i in infos}
    expected = min(indices) + step
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
