"""LLM helper: when CugaLite gets natural language with no code, decide if we should simulate ``continue``."""

import json
import re
from dataclasses import dataclass
from typing import Any, Optional

from langchain_core.language_models import BaseChatModel
from loguru import logger

from cuga.config import settings

CLASSIFIER_SYSTEM_PROMPT = """You classify a single turn from an API automation coding agent.

The agent must normally respond with a fenced Python script that calls tools. Sometimes it replies with only natural language (status, narration, or a short plan) and no code. That text may still be shown to the end user, which is wrong when the model clearly intends to keep working.

You receive one transcript that concatenates:
1) Assistant content — user-visible reply (may be empty)
2) Reasoning — internal chain-of-thought when the platform provides it (may be empty)

Read the FULL transcript end-to-end (not only the opening sentence). Do not decide from reasoning alone. Do not ignore reasoning when visible content is empty or a vague one-liner.

Return ONLY JSON, no markdown, no prose: {"auto_continue": true} or {"auto_continue": false}

Use auto_continue true when the combined content + reasoning shows the model still intends executable Python or more task execution:
- interim status / incompleteness
- phase-complete narration that then announces the next phase the agent will do itself
- upcoming tool calls, searches, listings, discoveries, or inspections (even if phrased as “I will / I’ll …”)
- multi-step plans where the announced work has not been executed yet in this turn (no code ran)

Important: a completed *sub-step* plus “next I will / proceed to / mark that phase complete and …” is still interim → true. Do NOT finalize just because an earlier clause reports counts or “X is complete” if later text clearly continues the overall task.

Use auto_continue false when the combined picture is an appropriate completed turn OR a hard stop:
- final answer / result with no further agent-owned work announced
- user question, missing input, or a choice the user must make
- refusal, fatal error, or explicit inability to continue (tools missing, environment unavailable, blocked)
- if ANY clause says the agent cannot / is unable to continue (or tools are not available), prefer false even when earlier sentences described a plan

Examples (visible content → decision):
- "We need to search student_loan app." → {"auto_continue": true} — interim plan; the work it announces has not happened.
- "Let me perform the second phase." → {"auto_continue": true} — interim status before more execution.
- "The export is complete: 12 saved tracks, 6 saved albums, and 6 ordered playlists. I’ll mark that phase complete and proceed to account setup discovery." → {"auto_continue": true} — sub-phase done, but the agent announces the next phase it will run itself.
- "I’ll inspect the work directory and search Jonathan’s inbox across all result pages for schedule-related threads, using the supplied current date as the search boundary. The directory listing and email-thread search are independent, so I’ll retrieve both and retain every matching thread page for detailed inspection." → {"auto_continue": true} — pure forward plan; no code yet.
- "I’ll inspect the work directory and search Jonathan’s inbox across all result pages for schedule-related threads… I’m unable to continue because the connected application tool functions are not available in the current execution environment." → {"auto_continue": false} — plan is overridden by a hard stop / tools unavailable.
- "Ok I will fetch the information, but first I require your ID" → {"auto_continue": false} — blocked on user input despite the announced plan.
- "I could not find any matching loans." → {"auto_continue": false} — a result, not a plan.
- "Which account should I use?" → {"auto_continue": false} — clarifying question.
- "Done. All 15 artists are followed on Spotify." → {"auto_continue": false} — completed result with no next agent phase."""

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

# ── Unverified-blocker override (issue #610) ────────────────────────────────
#
# Observed failure mode: on turn 1, before ANY tool call has executed, the model
# emits "plan → refusal" prose ("I'll discover the relevant Spotify tool first.
# I'm sorry, but I couldn't access the Spotify subscription details…") and the
# classifier — correctly, per its spec — treats the refusal as a hard stop. The
# run ends after 2 LLM calls with zero executed calls, on tasks the same prompt
# solves in sibling runs.
#
# When the harness can positively verify the claim is unfounded (tools ARE bound
# for this turn, and nothing has executed or errored yet), the refusal half of
# such a message is always wrong: an inability claim with no attempt behind it.
# In that narrow case we override the finalize once, with a corrective user
# message; a second consecutive refusal is accepted (the caller tracks the
# one-shot marker). This is deliberately NOT `require_tool_call_before_final`
# (removed in PR #416 review): a legitimate tool-free completion ("what can you
# do?") contains no inability claim, does not match the pattern below, and
# finalizes exactly as before.
_BLOCKED_CLAIM_RE = re.compile(
    r"(?:unable\s+to|couldn['’]t|could\s+not|cannot|can['’]t)\s+"
    r"(?:access|locate|find|retrieve|reach|execute|use|continue|proceed)"
    r"|(?:don['’]t|do\s+not|doesn['’]t|does\s+not)\s+have\s+(?:a|the|any)[^.]{0,40}\btools?\b"
    r"|\btools?\b[^.!\n]{0,60}(?:\bnot\b|\bun)available"
    r"|(?:(?:is|are)\s+not|isn['’]t|aren['’]t)\s+available\s+in\s+"
    r"(?:this|the\s+current)\s+(?:session|environment|context)"
    # "there's no (available) tool …", "we have no tool listed", "no such tool":
    # observed verbatim on gpt-oss-120b task 7574325_1 ("there's no available tool
    # or API for updating Venmo credentials"), which the clauses above all missed.
    # Kept as a bigram ("no … tool") so ordinary finals mentioning tools don't hit.
    r"|\bno\s+(?:available\s+|such\s+|suitable\s+|matching\s+)?(?:tools?|apis?)\b"
    r"|\black(?:s|ing)?\s+(?:a|the|any)\s+tool",
    re.IGNORECASE,
)

# Sent as the synthetic user turn instead of the plain "continue" when the
# override fires — a bare "continue" tends to elicit the same refusal again.
BLOCKED_CLAIM_CORRECTION = (
    "Your previous message claimed the required tools or data are unavailable, "
    "but no tool call has been executed yet and connected applications with "
    "tools ARE available (see Connected Applications and Current Available "
    "Tools in the system prompt). An unverified inability claim is not an "
    "acceptable final answer. Use find_tools(query, app_name) to discover the "
    "relevant tools and continue the task. If a call genuinely fails, report "
    "the observed error instead."
)


@dataclass(frozen=True)
class BlockedClaimEvidence:
    """What the harness knows about this turn, for the unverified-blocker override.

    ``tools_available``: at least one callable tool is bound for this turn.
    ``code_executed``: any sandbox execution has already run this task (a refusal
    after real attempts may be legitimate — the override only targets turn-1
    claims with nothing behind them).
    ``retry_used``: the one-shot corrective retry has already been spent.
    """

    tools_available: bool
    code_executed: bool
    retry_used: bool


@dataclass(frozen=True)
class AutoContinueDecision:
    auto_continue: bool
    blocked_override: bool = False


def looks_like_unverified_blocker(visible: str, reasoning: str = "") -> bool:
    """True when the turn's text contains an inability/unavailability claim."""
    combined = f"{visible or ''}\n{reasoning or ''}"
    return bool(_BLOCKED_CLAIM_RE.search(combined))


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


def _blocked_override_applies(visible: str, reasoning: str, evidence: Optional[BlockedClaimEvidence]) -> bool:
    """The unverified-blocker override (issue #610): all conditions must hold."""
    if evidence is None:
        return False
    if not getattr(settings.advanced_features, "cuga_lite_blocked_claim_retry", True):
        return False
    if not evidence.tools_available or evidence.code_executed or evidence.retry_used:
        return False
    return looks_like_unverified_blocker(visible, reasoning)


async def classify_nl_auto_continue_decision(
    llm: BaseChatModel,
    assistant_visible: Any,
    reasoning_excerpt: Optional[Any],
    *,
    evidence: Optional[BlockedClaimEvidence] = None,
) -> AutoContinueDecision:
    """Full decision: whether to auto-continue, and whether the blocked-claim override fired.

    ``evidence`` is what the harness knows about the turn; without it the
    unverified-blocker override never fires and behavior is unchanged.
    """
    if not getattr(settings.advanced_features, "cuga_lite_nl_auto_continue", True):
        return AutoContinueDecision(auto_continue=False)
    visible = normalize_assistant_text(assistant_visible)
    reasoning = normalize_assistant_text(reasoning_excerpt)
    if looks_like_planning_text(visible):
        logger.info("NL auto-continue: planning-text fast-path matched; auto-continuing")
        return AutoContinueDecision(auto_continue=True)
    combined = build_combined_content_and_reasoning(visible, reasoning)
    if not combined.strip():
        return AutoContinueDecision(auto_continue=False)
    user_block = (
        "Classify this assistant output (content + reasoning below).\n\n"
        f"{combined}\n\n"
        'Respond with JSON only: {"auto_continue": true} or {"auto_continue": false}'
    )
    finalize = AutoContinueDecision(auto_continue=False)
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
            return finalize
        if parsed:
            return AutoContinueDecision(auto_continue=True)
    except Exception as e:
        logger.warning(f"NL auto-continue classifier failed: {e}")
        return finalize

    # The classifier explicitly chose finalize (parsed False). Only that verdict
    # reaches the override — a classifier error or unparsable output finalizes
    # above, exactly like the pre-existing bool path, so the override never
    # fires on anything but a confirmed finalize (PR #657 review, finding 1).
    # If the finalize is an inability claim the harness can positively
    # contradict (tools bound, nothing executed, retry unspent), override once
    # with a corrective continue instead.
    if _blocked_override_applies(visible, reasoning, evidence):
        logger.warning(
            "NL auto-continue: turn-1 inability claim with tools bound and zero executed "
            "calls — overriding finalize with one corrective retry (issue #610)"
        )
        return AutoContinueDecision(auto_continue=True, blocked_override=True)
    return finalize


async def classify_nl_auto_continue(
    llm: BaseChatModel,
    assistant_visible: Any,
    reasoning_excerpt: Optional[Any],
) -> bool:
    """Return True if the graph should append a user ``continue`` message and re-invoke the coder model."""
    decision = await classify_nl_auto_continue_decision(llm, assistant_visible, reasoning_excerpt)
    return decision.auto_continue
