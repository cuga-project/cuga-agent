"""Deterministic final-answer function — the user-owned step of finalize_answer.

Configured by ``[final_answer].function`` (a dotted path to a pure
``(str) -> str``) or injected as a live callable by the SDK
(``CugaAgent(final_answer=my_fn)``). The injected callable wins.

Contract for the user function: pure and deterministic; idempotent is
recommended as a safety margin (the seam applies it once per delivered
answer). It must never break answer delivery: any failure is logged and
the answer is delivered unformatted.
"""

from dataclasses import dataclass
from typing import Callable, Optional

from loguru import logger

from cuga.config import get_class, settings


@dataclass
class FinalAnswerConfig:
    """SDK config for ``CugaAgent(final_answer=...)`` when combining both
    halves: LLM guidance and a deterministic function."""

    instructions: Optional[str] = None
    function: Optional[Callable[[str], str]] = None


def resolve_final_answer_instructions() -> str:
    """``[final_answer].instructions`` from settings/env, stripped ('' when unset)."""
    return (settings.final_answer.instructions or "").strip()


def resolve_answer_function(path: str) -> Callable[[str], str]:
    """Resolve a dotted ``module.attr`` path to a callable.

    Raises with a clear message on a dotless path or a non-callable target.
    (Callers on the answer path catch and log — a bad path is reported per
    answer rather than crashing delivery.)
    """
    if "." not in path:
        raise ValueError(f"[final_answer].function must be a dotted 'module.attr' path, got '{path}'")
    fn = get_class(path)
    if not callable(fn):
        raise TypeError(f"[final_answer].function '{path}' is not callable")
    return fn


def apply_answer_function(state, answer_function: Optional[Callable[[str], str]] = None) -> None:
    """Apply the configured answer function to ``state.final_answer`` in place.

    No-op when the answer is empty or nothing is configured. Never raises.
    """
    if not getattr(state, "final_answer", None):
        return
    fn = answer_function
    if fn is None:
        path = (settings.final_answer.function or "").strip()
        if not path:
            return
        try:
            fn = resolve_answer_function(path)
        except Exception:
            logger.exception(f"final_answer.function {path!r} failed to resolve; delivering unformatted")
            return
    try:
        formatted = fn(state.final_answer)
    except Exception:
        logger.exception("final answer function failed; delivering unformatted answer")
        return
    if isinstance(formatted, str):
        state.final_answer = formatted
    else:
        logger.warning(
            f"final answer function returned {type(formatted).__name__}, expected str; keeping original"
        )
