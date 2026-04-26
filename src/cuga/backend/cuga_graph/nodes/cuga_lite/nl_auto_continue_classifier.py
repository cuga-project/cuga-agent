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

Use auto_continue true when the combined content + reasoning shows the model still intends executable Python or more task execution, including:
- Interim status, incompleteness, or upcoming tool calls in reasoning
- Planning-only visible text: states what it still needs to do (e.g. discover which tools exist, list APIs, find the right endpoint) and that it will do that next, with no Python and no substantive answer to the user yet
- A multi-step play-by-play plan with no code yet: chains like "First, I will …", "Then, I will …", "After that, …", "Finally, I will …", "Let's start by …" when they only outline upcoming reads, tool calls, or file work and have not actually produced results for the user
- Phrases like "I need to …", "I'll first …", "I will discover …" when they describe work not yet done

Use auto_continue false when the combined picture is an appropriate completed turn: final answer to the user, substantive result or conclusion, user question back, refusal, or clear stop—even if there is no code.

Use auto_continue false when the turn is (or is mainly) explaining a code-execution or sandbox failure, traceback, or that a run failed—do not ask for another "continue" in that case."""

_VISIBLE_MAX = 12000
_REASONING_MAX = 8000
_COMBINED_MAX = 20000


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
    combined = build_combined_content_and_reasoning(visible, reasoning)
    if not combined.strip():
        return False
    user_block = (
        "Classify this assistant output (content + reasoning below).\n\n"
        f"{combined}\n\n"
        'Respond with JSON only: {"auto_continue": true} or {"auto_continue": false}'
    )
    try:
        resp = await llm.ainvoke(
            [
                {"role": "system", "content": CLASSIFIER_SYSTEM_PROMPT},
                {"role": "user", "content": user_block},
            ],
            config={"callbacks": []},
        )
        parsed = parse_auto_continue_json(getattr(resp, "content", "") or "")
        if parsed is None:
            logger.warning("NL auto-continue classifier returned unparsable output; treating as finalize")
            return False
        return parsed
    except Exception as e:
        logger.warning(f"NL auto-continue classifier failed: {e}")
        return False
