"""Harmony (gpt-oss) control-token handling for user-facing text.

gpt-oss speaks the harmony protocol, whose framing tokens (``<|channel|>``,
``<|return|>``, …) can survive into text CUGA shows the user. Providers
normalize the visible ``content`` channel, but CUGA deliberately reads
``reasoning_content`` off the raw response dict (see
``llm/models.ReasoningChatOpenAI``), so the un-normalized reasoning channel is
where this framing actually leaks from.

Handling happens where the model response is decoded — ``normalize_response``,
the single point both the CugaLite and supervisor graphs share — so every
downstream surface (``final_answer``, ``state.messages``, the streamed
``CodeAgent`` event, the chat copy, the trajectory step) inherits clean text.
Sanitizing per display surface does not converge: each new surface is a new
leak.

Detection is driven by the *text*, not by the configured model name. A name can
be wrong in ways the framing cannot — proxies, ``rits/`` prefixes, published
``llm_config``, and ``configurable["llm"]`` overrides all rename the underlying
model, and CugaLite resolves a different model than the final-answer agent. The
``"<|" not in text`` fast path keeps non-harmony runs free, and only members of
the ``openai-harmony`` vocabulary are treated as framing, so text that merely
mentions ``<|custom|>`` survives untouched.
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
    """Whether harmony control-token handling applies at all — a kill switch.

    ``advanced_features.strip_harmony_control_tokens``:

    - ``"auto"`` (default) / ``true`` — handle framing wherever it appears.
      Whether a given piece of text is touched is decided by the text itself,
      so runs that never see framing are unaffected either way.
    - ``false`` — disable outright.

    Deliberately does NOT inspect the configured model name. Doing so keyed the
    behaviour off ``agent.final_answer.model`` while CugaLite — the default
    path — runs on ``agent.code.model`` or a published ``llm_config``, so the
    common gpt-oss setup was never handled at all.
    """
    try:
        mode = getattr(settings.advanced_features, "strip_harmony_control_tokens", "auto")
    except Exception:
        # Unreadable settings must not silently disable a safety filter.
        return True
    if isinstance(mode, bool):
        return mode
    return str(mode).strip().lower() not in ("false", "0", "no", "off")


def contains_harmony_tokens(text: str) -> bool:
    """True when *text* carries harmony framing that must not reach the user."""
    if "<|" not in (text or ""):
        return False
    if not harmony_handling_enabled():
        return False
    specials = harmony_special_tokens()
    return any(m.group(0) in specials for m in _SPECIAL_TOKEN_SHAPE_RE.finditer(text))


def strip_harmony_tokens(text: str) -> str:
    """Return the user-facing text with harmony framing removed.

    Two shapes occur in practice and they need different treatment:

    - A trailing/loose control token on otherwise plain text
      (``"The total is 42<|return|>"``). Removing the token is exactly right.
    - Fully channel-structured output
      (``"<|channel|>analysis<|message|>…<|end|><|channel|>final<|message|>42"``).
      Here removing tokens alone is actively harmful: it welds the channel names
      onto the text (``"analysis…final42"``) and, worse, promotes the model's
      private analysis channel into user-visible output.

    So when the text is channel-structured, keep only the last channel's body —
    the final channel is what the protocol designates as the answer — and strip
    from that. Falls back to plain token removal when there is no channel
    framing.

    Deliberately no ``.strip()``: a token sitting directly before an indented
    block would otherwise take that block's leading indentation with it and
    corrupt e.g. a Markdown code block.
    """
    if "<|" not in (text or ""):
        return text
    if not harmony_handling_enabled():
        return text
    specials = harmony_special_tokens()

    # Channel-structured: everything after the final ``<|message|>`` is the
    # answer body; earlier channels (analysis/commentary) are not for the user.
    if "<|message|>" in text and "<|message|>" in specials:
        text = text.rsplit("<|message|>", 1)[1]

    return _SPECIAL_TOKEN_SHAPE_RE.sub(lambda m: "" if m.group(0) in specials else m.group(0), text)
