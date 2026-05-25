"""Helpers for normalizing how CodeAct/sandbox invokes registry tools."""

from __future__ import annotations

from typing import Any, Dict, List


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
