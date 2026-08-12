"""Harmony (gpt-oss) control-token handling for user-facing text.

gpt-oss speaks the harmony protocol, whose framing tokens (``<|channel|>``,
``<|return|>``, …) can survive into text CUGA shows the user. Providers
normalize the visible ``content`` channel, but CUGA deliberately reads
``reasoning_content`` off the raw response dict (see
``llm/models.ReasoningChatOpenAI``), so the un-normalized reasoning channel is
where this framing actually leaks from.

Two emission points use this module:

- ``cuga_agent_core.graph.shared_nodes`` — refuses to promote token-bearing
  reasoning into an answer when visible content is empty.
- ``nodes.answer.final_answer`` — strips any framing that reaches the delivered
  answer.

The vocabulary comes from ``openai-harmony`` rather than a hand-maintained
list, so it tracks upstream. Everything here is gated on
``advanced_features.strip_harmony_control_tokens`` so non-harmony providers are
never touched.
"""

import re
from functools import lru_cache

from loguru import logger

from cuga.config import settings

__all__ = [
    "contains_harmony_tokens",
    "harmony_handling_enabled",
    "harmony_special_tokens",
    "strip_harmony_tokens",
]

# Anything <|...|>-shaped is a *candidate*; only members of the harmony
# vocabulary are treated as framing, so text like "<|custom|>" survives.
_SPECIAL_TOKEN_SHAPE_RE = re.compile(r"<\|[^|>]*\|>")

# Used only when openai-harmony can't be imported — the framing tokens the
# protocol defines, so a missing wheel degrades rather than disabling handling.
_FALLBACK_CONTROL_TOKENS = frozenset(
    {
        "<|start|>",
        "<|end|>",
        "<|message|>",
        "<|channel|>",
        "<|constrain|>",
        "<|return|>",
        "<|call|>",
        "<|endoftext|>",
    }
)


@lru_cache(maxsize=1)
def harmony_special_tokens() -> frozenset:
    """The harmony special-token vocabulary, taken from ``openai-harmony``.

    Sourcing it from the official encoding rather than a hand-maintained list
    means the set tracks upstream instead of drifting. Loaded lazily and cached:
    callers check :func:`harmony_handling_enabled` first, so non-harmony runs
    never pay for building the encoding.
    """
    try:
        from openai_harmony import HarmonyEncodingName, load_harmony_encoding

        encoding = load_harmony_encoding(HarmonyEncodingName.HARMONY_GPT_OSS)
        return frozenset(encoding.special_tokens_set)
    except Exception as e:  # pragma: no cover - defensive
        logger.debug("openai-harmony unavailable; using built-in token list: {}", e)
        return _FALLBACK_CONTROL_TOKENS


def harmony_handling_enabled() -> bool:
    """Whether harmony control-token handling applies to this run.

    ``advanced_features.strip_harmony_control_tokens``:

    - ``"auto"`` (default) — handle only when the final-answer model is a
      harmony-format model (the gpt-oss family). Providers that never emit this
      framing are left completely untouched.
    - ``true`` / ``false`` — force it on or off, e.g. for a provider that leaks
      framing under a model name we don't recognise, or to disable it outright.
    """
    try:
        mode = getattr(settings.advanced_features, "strip_harmony_control_tokens", "auto")
    except Exception:
        return False
    if isinstance(mode, bool):
        return mode
    normalized = str(mode).strip().lower()
    if normalized in ("true", "1", "yes", "on"):
        return True
    if normalized in ("false", "0", "no", "off"):
        return False
    # auto: harmony framing originates from the gpt-oss family only
    try:
        model_name = str(getattr(settings.agent.final_answer.model, "model_name", "") or "")
    except Exception:
        return False
    return "gpt-oss" in model_name.lower()


def contains_harmony_tokens(text: str) -> bool:
    """True when *text* carries harmony framing that must not reach the user."""
    if "<|" not in (text or ""):
        return False
    if not harmony_handling_enabled():
        return False
    specials = harmony_special_tokens()
    return any(m.group(0) in specials for m in _SPECIAL_TOKEN_SHAPE_RE.finditer(text))


def strip_harmony_tokens(text: str) -> str:
    """Remove harmony framing from *text*, leaving everything else intact.

    Deliberately no ``.strip()``: a token sitting directly before an indented
    block would otherwise take that block's leading indentation with it and
    corrupt e.g. a Markdown code block.
    """
    if "<|" not in (text or ""):
        return text
    if not harmony_handling_enabled():
        return text
    specials = harmony_special_tokens()
    return _SPECIAL_TOKEN_SHAPE_RE.sub(lambda m: "" if m.group(0) in specials else m.group(0), text)
