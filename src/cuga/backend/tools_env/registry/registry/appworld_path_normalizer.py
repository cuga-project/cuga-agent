"""Deterministic path normalization for AppWorld ``file_system`` calls (#730).

AppWorld's ``file_system`` app expects paths that are either absolute
(``/...``) or home-anchored (``~/...``). Its server-side ``process_path``
rejects double slashes with a 422 and silently prepends ``/`` to anything
else — so an agent-sent cwd-relative path like ``./downloads/x.csv`` is
stored verbatim as ``/./downloads/x.csv``. The write is accepted, but the
file's ``tilde_path`` never maps back to ``~/downloads/x.csv`` (failing
evaluation), and on the read side the same malformed path 404s/422s forever,
feeding the #599 retry loop.

This module rewrites path-valued arguments to canonical form at the
``/functions/call`` choke point, before the rejected-call guard computes its
signature (so the guard dedupes on canonical args and a corrected call is
never short-circuited by rejections recorded under a malformed form):

- runs of ``/`` collapse to one (avoids the 422 double-slash rejection),
- ``.`` / ``..`` segments are resolved (``..`` never escapes the anchor),
- relative paths are anchored at home: ``./x`` -> ``~/x``, ``.`` -> ``~/``,
  bare ``x.csv`` -> ``~/x.csv`` (AppWorld has no cwd concept; its silent
  ``/``-prepend for relative input is itself a trap),
- a leading ``/./`` is treated as home-relative too — it is the server-side
  echo of an agent-sent cwd-relative path,
- clean ``~/...`` and absolute ``/...`` paths pass through unchanged.

Scope is deliberately narrow: only when ``advanced_features.benchmark`` is
``appworld``, only the ``file_system`` app, and only string arguments whose
key is ``path`` or ends with ``_path`` (every path parameter in the
``file_system`` API follows that naming). Paths containing backslashes are
left untouched — rewriting Windows-style separators would be guesswork.

All logic is pure string manipulation with POSIX ``/`` semantics on purpose:
AppWorld paths are virtual and host-OS independent, so ``os.path`` (ntpath on
Windows), ``pathlib`` and ``expanduser`` (which would inject the *host* home
directory) must never touch them.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

_FILE_SYSTEM_APP = "file_system"


def _is_path_key(key: str) -> bool:
    return key == "path" or key.endswith("_path")


def normalize_appworld_path(value: str) -> str:
    """Return ``value`` in canonical AppWorld form (``~/...`` or ``/...``).

    Pure string transformation — identical on every host OS. Returns the
    input unchanged when it is already canonical or out of scope (empty,
    non-string, backslash-containing).
    """
    if not isinstance(value, str) or not value.strip():
        return value
    if "\\" in value:
        return value
    s = value.strip()
    while "//" in s:
        s = s.replace("//", "/")
    # Anchor detection. Everything that is not ``~/...`` or a clean absolute
    # path is home-relative: AppWorld has no cwd for it to be relative to.
    if s == "~" or s.startswith("~/"):
        anchor, rest = "~/", s[1:].lstrip("/")
    elif s.startswith("/"):
        rest = s[1:]
        if rest == "." or rest.startswith("./"):
            anchor = "~/"  # server-side echo of an agent-sent relative path
        else:
            anchor = "/"
    else:
        anchor, rest = "~/", s
    parts: list[str] = []
    for segment in rest.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            if parts:
                parts.pop()
            continue  # never escape above the anchor
        parts.append(segment)
    body = "/".join(parts)
    normalized = anchor + body
    if body and s.endswith("/"):
        normalized += "/"  # preserve an explicit trailing slash
    return normalized


def normalize_file_system_path_args(
    app_name: str, args: Dict[str, Any]
) -> Tuple[Dict[str, Any], Dict[str, Tuple[str, str]]]:
    """Return ``(args, changes)`` with path-valued arguments canonicalized.

    ``changes`` maps each rewritten key to ``(original, normalized)`` so the
    caller can log what the model actually sent. When nothing applies (other
    app, other benchmark, no rewrites) the original ``args`` object is
    returned untouched.
    """
    if app_name != _FILE_SYSTEM_APP or not isinstance(args, dict):
        return args, {}
    from cuga.config import settings

    if getattr(settings.advanced_features, "benchmark", None) != "appworld":
        return args, {}
    changes: Dict[str, Tuple[str, str]] = {}
    for key, value in args.items():
        if not _is_path_key(key) or not isinstance(value, str):
            continue
        normalized = normalize_appworld_path(value)
        if normalized != value:
            changes[key] = (value, normalized)
    if not changes:
        return args, {}
    return {**args, **{key: new for key, (_, new) in changes.items()}}, changes
