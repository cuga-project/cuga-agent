"""Checks that run when the agent is about to give its final answer.

free-code runs *stop hooks* at this point of its loop: when the model ends a turn without a
tool call, each hook may block the stop and hand the model a message, and the loop goes
round again. This is the same seam. A check gets a :class:`StopContext` and returns
``None`` (let the answer through) or a correction text (send the model back with it).

- Checks are named. ``cuga_lite_stop_checks`` lists which run, in order: a name registered
  with :func:`register_stop_check`, or an import path ``"package.module:function"``.
- Each check fires at most once per user turn, so a check can never loop the agent; the
  step limit applies on top.
- A check that raises is logged and skipped — a broken check must never stop the agent
  from answering.

Built in: ``pending_todos`` — the plan still has unfinished steps.
"""

from __future__ import annotations

import importlib
import inspect
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Tuple, Union

from loguru import logger

STOP_CHECKS_FIRED_KEY = "_stop_checks_fired"
STOP_CHECK_PREFIX = "Before you finish: "


@dataclass(frozen=True)
class StopContext:
    """What a check can look at. Read-only; checks must not change run state."""

    answer: str
    messages: List[Any]
    todos: List[Dict[str, Any]] = field(default_factory=list)
    configurable: Dict[str, Any] = field(default_factory=dict)
    execution_mode: str = "codeact"


StopCheck = Callable[[StopContext], Union[Optional[str], Awaitable[Optional[str]]]]

_REGISTRY: Dict[str, StopCheck] = {}


def register_stop_check(name: str, check: StopCheck) -> None:
    """Make ``check`` available under ``name`` for ``cuga_lite_stop_checks``."""
    _REGISTRY[name] = check


def pending_todos(ctx: StopContext) -> Optional[str]:
    """The plan has steps never started: do them or say they are not needed.

    Lenient on purpose — this runs on every finish. A step ``in_progress`` is the one the
    answer is completing, and the plan's last step is usually "give the answer", which is
    what the model is doing right now; neither counts.
    """
    items = list(ctx.todos)[:-1]
    open_steps = [
        str(t.get("text", "")).strip()
        for t in items
        if str(t.get("status", "pending")).strip().lower() == "pending"
    ]
    if not open_steps:
        return None
    listed = "; ".join(s for s in open_steps[:5] if s)
    return (
        f"your plan (Current Plan) still has {len(open_steps)} unfinished step(s): {listed}. "
        "Do them now if the task needs them. If they are already done or no longer needed, update the plan "
        "with create_update_todos, then give the final answer."
    )


register_stop_check("pending_todos", pending_todos)


def _resolve(spec: str) -> Optional[Tuple[str, StopCheck]]:
    spec = (spec or "").strip()
    if not spec:
        return None
    if spec in _REGISTRY:
        return spec, _REGISTRY[spec]
    if ":" in spec:
        module_name, _, attr = spec.partition(":")
        try:
            check = getattr(importlib.import_module(module_name), attr)
        except Exception as exc:
            logger.error("stop check {!r} could not be imported: {}", spec, exc)
            return None
        if callable(check):
            return spec, check
    logger.error("stop check {!r} is not registered and is not an import path", spec)
    return None


def configured_stop_checks(configurable: Optional[dict], settings: Any) -> List[str]:
    """``cuga_lite_stop_checks`` from ``configurable``, else ``advanced_features``."""
    cfg = configurable or {}
    raw = (
        cfg["cuga_lite_stop_checks"]
        if "cuga_lite_stop_checks" in cfg
        else getattr(getattr(settings, "advanced_features", None), "cuga_lite_stop_checks", None)
    )
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [part for part in raw.split(",")]
    return [str(item).strip() for item in raw if str(item).strip()]


async def run_stop_checks(
    ctx: StopContext, specs: Iterable[str], fired: Iterable[str]
) -> Optional[Tuple[str, str]]:
    """``(check name, correction text)`` from the first check that objects, else ``None``."""
    already = set(fired or ())
    for spec in specs:
        resolved = _resolve(spec)
        if resolved is None:
            continue
        name, check = resolved
        if name in already:
            continue
        try:
            outcome = check(ctx)
            if inspect.isawaitable(outcome):
                outcome = await outcome
        except Exception as exc:
            logger.exception("stop check {!r} failed; letting the answer through: {}", name, exc)
            continue
        if isinstance(outcome, str) and outcome.strip():
            logger.warning("stop check {!r} sent the model back before finishing", name)
            return name, STOP_CHECK_PREFIX + outcome.strip()
    return None


def fired_checks(metadata: Optional[dict]) -> List[str]:
    value = (metadata or {}).get(STOP_CHECKS_FIRED_KEY) or []
    return [str(v) for v in value] if isinstance(value, (list, tuple)) else []
