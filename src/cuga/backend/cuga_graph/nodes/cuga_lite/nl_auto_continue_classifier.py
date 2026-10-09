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

# ── Autonomous mode (#445): structured verdict, policy in code ───────────────
#
# ``CLASSIFIER_SYSTEM_PROMPT`` above is the interactive prompt, unchanged from
# before #445, and it is what every interactive call still sends. In autonomous
# mode (no user present) the classifier is not asked for a verdict at all: it
# answers five factual yes/no questions about the turn, and the continue-vs-
# finalize policy is ``decide_autonomous`` below. The mode logic is therefore
# unit-testable, and a new failure class becomes a new field or example rather
# than another prose rule.
#
# Why not an addendum of extra rules on the shared prompt (the earlier shape of
# #732): on the 343-case gpt-oss-120b regression set a "deferral → continue"
# rule fixed the 12 #445 deferral cases but regressed 77 completed final
# answers ("… let me know if you need anything else!") and 8 hard stops. The
# structured form below, fed the harness evidence in ``build_classifier_user_
# block``, held every final answer and 11/12 hard stops on the 59-case decision
# subset while fixing 11/12 deferrals (PR #732 comments, 3 runs each).

AUTONOMOUS_CLASSIFIER_SYSTEM_PROMPT = """You classify a single turn from an API automation coding agent.

The agent must normally respond with a fenced Python script that calls tools. Sometimes it replies with only natural language (status, narration, a short plan, a question, or a result) and no code. The harness must decide whether to send the agent a synthetic "continue" or to treat the text as the agent's final answer.

You receive one transcript that concatenates:
1) Harness evidence — facts the harness knows about this run (task, session mode, what has executed)
2) Assistant content — user-visible reply (may be empty)
3) Reasoning — internal chain-of-thought when the platform provides it (may be empty)

Read the FULL transcript end-to-end. When the visible content is empty or a vague one-liner, the reasoning IS the turn: answer the questions from it.

Do NOT decide continue-vs-finalize yourself. Answer five factual yes/no questions about the turn and return ONLY JSON, no markdown, no prose:
{"outcome_achieved": bool, "defers_to_user": bool, "agent_can_proceed": bool, "announces_pending_action": bool, "hard_stop": bool}

Definitions:
- outcome_achieved: the turn reports the task's requested END result as done, judged against the task in the harness evidence. A completed sub-step, phase, search, or discovery is NOT the end result.
- defers_to_user: continuing depends on the user answering, choosing, confirming, or supplying something (a clarifying question, a request for missing input, "would you like me to…", "shall I…", "let me know how to proceed"). A courtesy closer after a completed result ("let me know if you need anything else!") is NOT deferral.
- agent_can_proceed: the agent could make further progress on the task by itself. True when the question it asks is one it could answer on its own — relax a threshold slightly, try different search terms or another page, choose the best available alternative, use another option already on file. False ONLY when the only way forward needs something the agent cannot obtain (card details, credentials, a file or record that does not exist) or the turn states that every option has already been tried and exhausted.
- announces_pending_action: the turn is interim — it announces or implies agent-owned work that has not happened yet in this turn. Includes an explicit plan ("I'll search…", "we need to discover…", "next I will…", "I will transfer you to an agent"), a completed sub-step followed by the next phase, and reasoning that plans a tool call while the visible content is empty or a bare status line.
- hard_stop: the agent states it cannot / is unable to continue (tools unavailable, environment blocked, fatal error), or the text is only a quoted or drafted message addressed to a third party (e.g. the body of an email the agent already sent).

Examples (visible content → JSON):
- "We need to search student_loan app." → {"outcome_achieved": false, "defers_to_user": false, "agent_can_proceed": true, "announces_pending_action": true, "hard_stop": false}
- (visible content empty) Reasoning: "We have the tool amazon_show_orders_orders_get. We need the last 2 orders, so we will call it with page_limit=2." → {"outcome_achieved": false, "defers_to_user": false, "agent_can_proceed": true, "announces_pending_action": true, "hard_stop": false}
- "The export is complete: 12 saved tracks. I’ll mark that phase complete and proceed to account setup discovery." → {"outcome_achieved": false, "defers_to_user": false, "agent_can_proceed": true, "announces_pending_action": true, "hard_stop": false}
- "Done. All 15 artists are followed on Spotify. Let me know if you need anything else!" → {"outcome_achieved": true, "defers_to_user": false, "agent_can_proceed": false, "announces_pending_action": false, "hard_stop": false}
- "Your order has been placed (Order ID 3146). Let me know if you’d like the receipt saved to a file!" → {"outcome_achieved": true, "defers_to_user": false, "agent_can_proceed": false, "announces_pending_action": false, "hard_stop": false}
- "No microwaves were found that fit your countertop and have a rating ≥ 4.2. Would you like me to relax the rating requirement or search with different keywords?" → {"outcome_achieved": false, "defers_to_user": true, "agent_can_proceed": true, "announces_pending_action": false, "hard_stop": false} — the agent can relax the threshold or change keywords itself.
- "After searching 40 threads from your manager, none contain a meeting schedule. Would you like me to check other senders or attachments?" → {"outcome_achieved": false, "defers_to_user": true, "agent_can_proceed": true, "announces_pending_action": false, "hard_stop": false} — more searching is possible.
- "A new, valid payment card is required to proceed. Please provide the card number, expiry and CVV." → {"outcome_achieved": false, "defers_to_user": true, "agent_can_proceed": false, "announces_pending_action": false, "hard_stop": false} — only the user has card details.
- "All available threads from your manager have been exhausted (the last page was empty). None contain a meeting schedule, so no alarms can be set." → {"outcome_achieved": false, "defers_to_user": false, "agent_can_proceed": false, "announces_pending_action": false, "hard_stop": false} — search space exhausted.
- "I’m unable to continue because the connected application tool functions are not available in the current execution environment." → {"outcome_achieved": false, "defers_to_user": false, "agent_can_proceed": false, "announces_pending_action": false, "hard_stop": true}
- "Ok I will fetch the information, but first I require your ID" → {"outcome_achieved": false, "defers_to_user": true, "agent_can_proceed": false, "announces_pending_action": true, "hard_stop": false}
- "…Let me know if it looks good. I can place the order once you confirm. Best, Stephen Mccoy" (the body of an email the agent sent) → {"outcome_achieved": true, "defers_to_user": false, "agent_can_proceed": false, "announces_pending_action": false, "hard_stop": true}"""

AUTONOMOUS_FIELDS = (
    "outcome_achieved",
    "defers_to_user",
    "agent_can_proceed",
    "announces_pending_action",
    "hard_stop",
)

_USER_BLOCK_PREAMBLE = "Classify this assistant output (content + reasoning below).\n\n"
_USER_BLOCK_SUFFIX = 'Respond with JSON only: {"auto_continue": true} or {"auto_continue": false}'
_USER_BLOCK_AUTONOMOUS_SUFFIX = (
    'Respond with JSON only: {"outcome_achieved": bool, "defers_to_user": bool, '
    '"agent_can_proceed": bool, "announces_pending_action": bool, "hard_stop": bool}'
)


@dataclass(frozen=True)
class BlockedClaimEvidence:
    """What the harness knows about this turn.

    ``tools_available`` / ``code_executed`` / ``retry_used`` drive the
    unverified-blocker override (issue #610): at least one callable tool is
    bound; any sandbox execution has already run this task; the one-shot
    corrective retry has been spent.

    ``task`` and ``nl_streak`` (#445) are rendered into the autonomous-mode
    user block so the classifier judges "outcome achieved" against the actual
    task and knows how many natural-language turns it has already continued.
    """

    tools_available: bool
    code_executed: bool
    retry_used: bool
    task: str = ""
    nl_streak: int = 0


def build_harness_evidence_block(evidence: Optional[BlockedClaimEvidence]) -> str:
    """The autonomous-mode evidence header. Unknown facts are said to be unknown
    rather than guessed — the classifier was tuned with that wording."""
    ev = evidence or BlockedClaimEvidence(tools_available=False, code_executed=False, retry_used=False)
    task = (ev.task or "").strip()
    streak = int(ev.nl_streak or 0)
    # The streak resets on every code turn, so a non-zero streak means no code
    # has run since the previous natural-language turn.
    if streak > 0:
        since_last_nl = "no"
    elif ev.code_executed:
        since_last_nl = "yes"
    else:
        since_last_nl = "no"
    return "\n".join(
        [
            "## Harness evidence",
            f"- Task given to the agent: {json.dumps(task) if task else 'unknown'}",
            "- Session mode: autonomous — no user is present to answer",
            f"- Code has executed earlier in this run: {'yes' if ev.code_executed else 'no'}",
            f"- Code executed since the previous natural-language turn: {since_last_nl}",
            f"- Natural-language turns already auto-continued in a row without code: {streak}",
        ]
    )


def build_classifier_system_prompt(autonomous: bool) -> str:
    """Interactive: the pre-#445 prompt verbatim. Autonomous: the structured-verdict prompt."""
    return AUTONOMOUS_CLASSIFIER_SYSTEM_PROMPT if autonomous else CLASSIFIER_SYSTEM_PROMPT


def build_classifier_user_block(
    combined: str, autonomous: bool, evidence: Optional[BlockedClaimEvidence] = None
) -> str:
    """Interactive: the pre-#445 user message verbatim. Autonomous: evidence header,
    transcript, and the five-field answer format."""
    if not autonomous:
        return f"{_USER_BLOCK_PREAMBLE}{combined}\n\n{_USER_BLOCK_SUFFIX}"
    return f"{_USER_BLOCK_PREAMBLE}{build_harness_evidence_block(evidence)}\n\n{combined}\n\n{_USER_BLOCK_AUTONOMOUS_SUFFIX}"


def parse_autonomous_verdict(raw: str) -> Optional[dict]:
    """The five booleans, or None when any is missing or not a boolean."""
    t = (raw or "").strip()
    if t.startswith("```"):
        t = t.strip("`").strip()
        if t.lower().startswith("json"):
            t = t[4:]
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        obj = json.loads(t[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    out: dict = {}
    for k in AUTONOMOUS_FIELDS:
        v = obj.get(k)
        if isinstance(v, str) and v.lower() in ("true", "false"):
            v = v.lower() == "true"
        if not isinstance(v, bool):
            return None
        out[k] = v
    return out


def decide_autonomous(fields: dict) -> bool:
    """Continue-vs-finalize policy for autonomous runs, from the five answers.

    A hard stop or an achieved outcome always finalizes. Otherwise the turn
    continues when it defers a question the agent could answer itself, or when
    it announces its own next action and is not blocked on the absent user. A
    deferral the agent cannot resolve ("please provide the card details, then I
    will add it") therefore finalizes even though it also announces an action —
    on the decision subset that rule alone took hard stops from 9/12 to 11/12
    with no other change.
    """
    if fields["hard_stop"] or fields["outcome_achieved"]:
        return False
    if fields["defers_to_user"]:
        return fields["agent_can_proceed"]
    return fields["announces_pending_action"]


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
    autonomous: bool = False,
) -> AutoContinueDecision:
    """Full decision: whether to auto-continue, and whether the blocked-claim override fired.

    ``evidence`` is what the harness knows about the turn; without it the
    unverified-blocker override never fires and behavior is unchanged.

    ``autonomous`` (#445) tells the classifier whether a real user is present.
    Interactive (the default) sends exactly the pre-#445 prompt and user
    message and reads a bool verdict. Autonomous sends
    ``AUTONOMOUS_CLASSIFIER_SYSTEM_PROMPT`` plus the harness evidence, reads
    five factual answers, and applies ``decide_autonomous``.
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
    user_block = build_classifier_user_block(combined, autonomous, evidence)
    finalize = AutoContinueDecision(auto_continue=False)
    try:
        from cuga.backend.cuga_graph.utils.langfuse_tracing import get_langfuse_invoke_config

        resp = await llm.ainvoke(
            [
                {"role": "system", "content": build_classifier_system_prompt(autonomous)},
                {"role": "user", "content": user_block},
            ],
            config=get_langfuse_invoke_config(),
        )
        raw = getattr(resp, "content", "") or ""
        if autonomous:
            fields = parse_autonomous_verdict(raw)
            if fields is None:
                logger.warning(
                    "NL auto-continue classifier (autonomous) returned unparsable output; treating as finalize"
                )
                return finalize
            logger.info(f"NL auto-continue (autonomous) fields={fields}")
            if decide_autonomous(fields):
                return AutoContinueDecision(auto_continue=True)
        else:
            parsed = parse_auto_continue_json(raw)
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
    *,
    autonomous: bool = False,
) -> bool:
    """Return True if the graph should append a user ``continue`` message and re-invoke the coder model."""
    decision = await classify_nl_auto_continue_decision(
        llm, assistant_visible, reasoning_excerpt, autonomous=autonomous
    )
    return decision.auto_continue
