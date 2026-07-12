"""LLM helper: when CugaLite gets natural language with no code, decide if we should simulate ``continue``."""

import json
import re
from typing import Any, Optional

from langchain_core.language_models import BaseChatModel
from loguru import logger

from cuga.config import settings

CLASSIFIER_SYSTEM_PROMPT = """You classify a single turn from an API automation coding agent.

The agent must normally respond with a fenced Python script that calls tools. Sometimes it replies with only natural language (status, narration, or a short plan) and no code. That text may still be shown to the end user, which is wrong when the model clearly intends to keep working.

You receive one transcript that concatenates:
1) Assistant content — user-visible reply (may be empty)
2) Reasoning — internal chain-of-thought when the platform provides it (may be empty)

Read the full transcript. Do not decide from reasoning alone: if the visible content is already a complete, substantive answer, use auto_continue false even when reasoning mentions extra steps. Do not ignore reasoning when visible content is empty or a vague one-liner.

Return ONLY JSON, no markdown, no prose: {"auto_continue": true} or {"auto_continue": false}

Use auto_continue true when the combined content + reasoning shows the model still intends executable Python or more task execution (interim status, incompleteness, upcoming tool calls in reasoning).

Use auto_continue false when the combined picture is an appropriate completed turn: final answer, user question, refusal, error explanation, or clear stop. Also false when the turn needs something from the user — a question, missing input, or a choice — regardless of how much future work it mentions.

Examples (visible content → decision):
- "We need to search student_loan app." → {"auto_continue": true} — interim plan; the work it announces has not happened.
- "Let me perform the second phase." → {"auto_continue": true} — interim status before more execution.
- "Ok I will fetch the information, but first I require your ID" → {"auto_continue": false} — blocked on user input despite the announced plan.
- "I could not find any matching loans." → {"auto_continue": false} — a result, not a plan.
- "Which account should I use?" → {"auto_continue": false} — clarifying question."""

_VISIBLE_MAX = 12000
_REASONING_MAX = 8000
_COMBINED_MAX = 20000

# Deterministic fast-path for obvious planning/discovery turns.
#
# The agent occasionally emits a short first-person plan with no code on a turn
# where it clearly intends to keep working, e.g. "We need to search student_loan
# app." or "We need to discover the tool signatures for codebase_comments".
# The LLM classifier has been observed to misfire on these and finalize the plan
# as the answer (the "planning-text stall"). We catch the unambiguous cases here
# so the result does not depend on a flaky model call.
#
# This path is intentionally conservative: it only flips False -> True for short
# text that opens with a first-person intent ("we"/"I"/"let's"/"let me"),
# optionally behind a discourse marker, followed by a forward-looking action or
# modal verb. A genuine final answer rarely matches, and the surrounding graph
# already enforces a step limit before auto-continuing, so an over-fire cannot
# loop forever.
_PLANNING_INTENT_RE = re.compile(
    r"^(?:(?:ok(?:ay)?|now|first(?:ly)?|next|then|so|alright|well)[\s,]+)*"
    r"(?:we|i|let'?s|let\s+me)\b"
    r"(?:(?!\.).)*?\b"
    r"(?:need\s+to|have\s+to|should|must|will|'ll|going\s+to|gonna|"
    r"start\s+by|begin\s+by|"
    r"search|discover|find|look\s+up|fetch|call|query|inspect|"
    r"explore|examine|check|investigate|figure\s+out|determine|"
    r"retrieve|gather|list|enumerate)\b",
    re.IGNORECASE,
)

# A negation usually marks a result or refusal ("I could not find …"), not a
# forward-looking plan — let those fall through to the LLM classifier / finalize.
_NEGATION_RE = re.compile(
    r"\b(?:not|never|unable|cannot|no)\b|\w+n['\u2019]t\b",
    re.IGNORECASE,
)

# A planning statement describes the agent's own next actions. Text that
# addresses the user in the second person may be requesting input ("Ok I will
# fetch the information, but first I require your ID") \u2014 auto-continuing there
# would answer the agent's request with a synthetic "continue" instead of the
# user's reply. Anything second-person falls through to the LLM classifier.
_SECOND_PERSON_RE = re.compile(r"\b(?:you|your|yours)\b", re.IGNORECASE)

_PLANNING_MAX_LEN = 400


def looks_like_planning_text(visible: str) -> bool:
    """True for a short first-person intent statement that signals more work to come.

    Conservative deterministic detector for the planning-text stall. Returns
    False for empty text, anything longer than a couple of sentences, text
    that reads as a question (clarifying questions should finalize, not loop),
    or text that addresses the user in the second person (it may be requesting
    input the user must supply).
    """
    t = (visible or "").strip()
    if not t or len(t) > _PLANNING_MAX_LEN:
        return False
    if t.rstrip().endswith("?"):
        return False
    if _NEGATION_RE.search(t):
        return False
    if _SECOND_PERSON_RE.search(t):
        return False
    return bool(_PLANNING_INTENT_RE.match(t))


def build_combined_content_and_reasoning(visible: str, reasoning: str) -> str:
    """Single transcript: user-visible content plus internal reasoning (either part may be omitted)."""
    v = (visible or "").strip()[:_VISIBLE_MAX]
    r = (reasoning or "").strip()[:_REASONING_MAX]
    parts: list[str] = []
    if v:
        parts.append(f"## Assistant content (user-visible)\n{v}")
    if r:
        parts.append(f"## Reasoning (internal)\n{r}")
    combined = "\n\n".join(parts)
    if len(combined) > _COMBINED_MAX:
        combined = combined[: _COMBINED_MAX - 20] + "\n...[truncated]"
    return combined


def normalize_assistant_text(content: Any) -> str:
    """Turn model `content` (str, content blocks list, etc.) into a single plain string."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                t = block.get("text")
                if isinstance(t, str):
                    parts.append(t)
                elif isinstance(block.get("content"), str):
                    parts.append(block["content"])
                elif t is not None:
                    parts.append(normalize_assistant_text(t))
            else:
                parts.append(str(block))
        return "\n".join(p for p in parts if p).strip()
    return str(content).strip()


def parse_auto_continue_json(raw: str) -> Optional[bool]:
    t = (raw or "").strip()
    if not t:
        return None
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.IGNORECASE).strip()
        t = re.sub(r"\s*```\s*$", "", t).strip()
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        obj = json.loads(t[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    v = obj.get("auto_continue")
    if isinstance(v, bool):
        return v
    if isinstance(v, str) and v.lower() in ("true", "false"):
        return v.lower() == "true"
    return None


async def classify_nl_auto_continue(
    llm: BaseChatModel,
    assistant_visible: Any,
    reasoning_excerpt: Optional[Any],
) -> bool:
    """Return True if the graph should append a user ``continue`` message and re-invoke the coder model."""
    if not getattr(settings.advanced_features, "cuga_lite_nl_auto_continue", True):
        return False
    visible = normalize_assistant_text(assistant_visible)
    reasoning = normalize_assistant_text(reasoning_excerpt)
    if looks_like_planning_text(visible):
        logger.info("NL auto-continue: planning-text fast-path matched; auto-continuing")
        return True
    combined = build_combined_content_and_reasoning(visible, reasoning)
    if not combined.strip():
        return False
    user_block = (
        "Classify this assistant output (content + reasoning below).\n\n"
        f"{combined}\n\n"
        'Respond with JSON only: {"auto_continue": true} or {"auto_continue": false}'
    )
    try:
        from cuga.backend.cuga_graph.utils.langfuse_tracing import get_langfuse_invoke_config

        resp = await llm.ainvoke(
            [
                {"role": "system", "content": CLASSIFIER_SYSTEM_PROMPT},
                {"role": "user", "content": user_block},
            ],
            config=get_langfuse_invoke_config(),
        )
        parsed = parse_auto_continue_json(getattr(resp, "content", "") or "")
        if parsed is None:
            logger.warning("NL auto-continue classifier returned unparsable output; treating as finalize")
            return False
        return parsed
    except Exception as e:
        logger.warning(f"NL auto-continue classifier failed: {e}")
        return False
