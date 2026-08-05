"""Helpers for normalizing how CodeAct/sandbox invokes registry tools."""

from __future__ import annotations

from typing import Any, Dict, List


def unexpected_tool_arg_names(
    args: tuple,
    kwargs: Dict[str, Any],
    param_names: List[str],
) -> List[str]:
    """Return unexpected argument names for schema-backed tool calls.

    When a single positional dict mixes known and unknown keys, the unknown names
    are reported. A dict with *no* known keys is treated as a nested payload for
    the first parameter (same as ``merge_tool_call_args``) and is not unexpected.
    """
    if not param_names:
        return []

    known = set(param_names)
    unexpected: set[str] = set()

    if len(args) == 1 and isinstance(args[0], dict):
        d: Dict[str, Any] = args[0]
        if any(k in known for k in d):
            unexpected.update(k for k in d if k not in known)
    else:
        for i in range(len(param_names), len(args)):
            unexpected.add(f"arg{i}")
    unexpected.update(k for k in kwargs if k not in known)
    return sorted(unexpected)


def merge_tool_call_args(
    args: tuple,
    kwargs: Dict[str, Any],
    param_names: List[str],
) -> Dict[str, Any]:
    """Combine positional and keyword args for dynamically generated API tools.

    Generated code often calls ``await tool({"product_id": 1, "quantity": 2})`` instead of
    keyword form. The naive mapping assigns the entire dict to the first schema field
    (e.g. ``product_id``), which breaks validation. When a single positional dict's keys
    are all known parameter names, treat it as a kwargs bag.
    """
    all_kwargs: Dict[str, Any] = {}
    if len(args) == 1 and isinstance(args[0], dict):
        d: Dict[str, Any] = args[0]
        if not param_names:
            all_kwargs.update(d)
        else:
            known = set(param_names)
            picked = {k: v for k, v in d.items() if k in known}
            if picked:
                all_kwargs.update(picked)
            elif d:
                all_kwargs[param_names[0]] = d
    else:
        for i, arg in enumerate(args):
            if i < len(param_names):
                all_kwargs[param_names[i]] = arg
            else:
                all_kwargs[f"arg{i}"] = arg
    all_kwargs.update(kwargs)
    return all_kwargs


def resolve_tool_call_args(
    args: tuple,
    kwargs: Dict[str, Any],
    param_names: List[str],
) -> tuple[Dict[str, Any], List[str]]:
    """Merge tool args and report unexpected names in one step.

    Returns ``(merged_args, unexpected_names)``. When unexpected names are present,
    ``merged_args`` keeps the unfiltered raw arguments for tracking/diagnostics;
    otherwise it is the schema-filtered merge used for the API call.
    """
    unexpected = unexpected_tool_arg_names(args, kwargs, param_names)
    if unexpected:
        return merge_tool_call_args(args, kwargs, []), unexpected
    return merge_tool_call_args(args, kwargs, param_names), []
