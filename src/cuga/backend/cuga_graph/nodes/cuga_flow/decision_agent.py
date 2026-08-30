"""
DecisionAgent — per-gateway routing for BPMN processes.

One DecisionAgent per gateway, implemented as a LangGraph whose **shape depends on
where the deciding input comes from**.

Default — the gateway carries a ``condition:`` expression:

    eval_condition ──▶ decide ──▶ (flow_id, reason)
       no LLM           LLM

Node 1 evaluates ``condition`` against process variables by safe string parsing (no
eval(), no LLM), yielding TRUE / FALSE / UNKNOWN. Node 2 reads that result, the process
state and the gateway's markdown policy, and returns one flow ID.

With ``human_consultation: <remote agent>`` — **node 1 is omitted**:

    decide ──▶ (flow_id, reason)
      LLM, holding a consult_user tool

There is no expression to evaluate, so the decide agent obtains what it needs by asking
a person through the remote agent over A2A. Whether and how to ask is part of its
reasoning, which is why consultation is a bound tool rather than a fixed step.

Either way node 2 returns a flow ID validated against the available flows, so it cannot
invent a branch — which is what keeps routing authority local even when the input came
from outside.

Note there is no ``evaluate_condition`` tool: condition evaluation is node 1 itself,
deterministic and always run when present.

A gateway in "native" mode is routed inline by the FlowAgent using condition eval alone
— no DecisionAgent is created for it.

Note: CugaAgent is imported lazily inside methods to avoid a circular import:
sdk.py imports DecisionAgent, so decision_agent.py cannot import CugaAgent at module level.
"""

import re
import operator as _op
from typing import Any, Callable, Dict, List, Optional, TypedDict
from loguru import logger

from langgraph.graph import StateGraph, START, END

from cuga.backend.cuga_graph.nodes.cuga_flow.flow_agent_state import FlowState
from cuga.backend.cuga_graph.nodes.cuga_flow.bpmn_parser import BPMNFlow
from cuga.backend.activity_tracker.tracker import ActivityTracker, Step
from cuga.backend.llm.models import LLMManager

_tracker = ActivityTracker()


# ── Safe condition evaluator ──────────────────────────────────────────────────
# Ordered longest-first so '>=' is tested before '>' etc.
_CMP_OPS: Dict[str, Callable[[Any, Any], bool]] = {
    ">=": _op.ge,
    "<=": _op.le,
    "!=": _op.ne,
    "==": _op.eq,
    ">": _op.gt,
    "<": _op.lt,
}


def _parse_literal(s: str) -> Any:
    """Parse a substituted token into a Python scalar (int/float/bool/None/str)."""
    s = s.strip()
    if s == "None":
        return None
    if s == "True":
        return True
    if s == "False":
        return False
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        return s[1:-1]
    return s


def _safe_compare(expr: str) -> bool:
    """Evaluate a single binary comparison (e.g. '100 > 50') without eval()."""
    for op_str, op_fn in _CMP_OPS.items():
        parts = expr.split(op_str, 1)
        if len(parts) == 2:
            left = _parse_literal(parts[0])
            right = _parse_literal(parts[1])
            if left is None or right is None:
                return False
            try:
                return bool(op_fn(left, right))
            except TypeError:
                return False
    return False


def _substitute_vars(expr: str, variables: Dict[str, Any]) -> str:
    """Replace ${var} tokens with their values from variables dict."""

    def replace(match: re.Match) -> str:
        return str(variables.get(match.group(1).strip(), "None"))

    return re.sub(r'\$\{([^}]+)\}', replace, expr)


def eval_condition(condition: str, state: FlowState) -> bool:
    """
    Public condition eval tool: substitute variables then safe-compare.
    Returns False for missing or unparseable conditions.
    """
    if not condition:
        return False
    try:
        expr = _substitute_vars(condition, state.process_variables or {})
        if any(op in expr for op in _CMP_OPS):
            return _safe_compare(expr)
    except Exception as e:
        logger.debug(f"Could not evaluate condition '{condition}': {e}")
    return False


# ── Internal state for the DecisionAgent's two-node graph ────────────────────


class _DecisionState(TypedDict):
    process_variables: Dict[str, Any]
    task_results: Dict[str, Any]
    condition_result: str  # "TRUE", "FALSE", or "UNKNOWN"
    available_flows: List[Dict[str, str]]  # [{"id": ..., "label": ...}]
    chosen_flow_id: str
    routing_reason: str


# ── DecisionAgent ─────────────────────────────────────────────────────────────


class DecisionAgent:
    """
    Per-gateway routing agent implemented as a two-node LangGraph.

    Node 1 — ``eval_condition``: deterministic condition evaluation using safe string
    parsing (no eval(), no LLM). Stores "TRUE" / "FALSE" / "UNKNOWN". **Omitted from the
    graph entirely when the gateway declares ``human_consultation:``** — there is no
    expression to evaluate in that case.

    Node 2 — ``decide``: CugaAgent reads the condition result (or, when consulting, asks
    the user via its consult_user tool), the process state, and the gateway's decision
    policy, then returns exactly one flow ID.

    route() compiles and runs this internal graph, returning the chosen flow ID.
    """

    def __init__(
        self,
        gateway_id: str,
        policy: str,
        condition: Optional[str] = None,
        flow_decisions: Optional[Dict[str, str]] = None,
        model_name: Optional[str] = None,
        temperature: float = 0.0,
        consultation_tool: Optional[Any] = None,
    ):
        self.gateway_id = gateway_id
        self.policy = policy
        self.condition = condition
        self.flow_decisions: Dict[str, str] = flow_decisions or {}
        self._model_name = model_name
        self._temperature = temperature
        # Set when the gateway declares human_consultation:. Bound as a tool on the
        # decide agent, and its presence removes node 1 from the graph — see _build_graph.
        self._consultation_tool = consultation_tool

        # CugaAgent for the decide node — created lazily.
        self._agent: Optional[Any] = None

        # Compile the internal two-node routing graph once at construction time.
        self._compiled_graph = self._build_graph()

    # ── Graph construction ────────────────────────────────────────────────────

    def _build_graph(self):
        """
        Compile the routing graph. Its shape depends on where the input comes from.

        With ``human_consultation:`` the condition-evaluation node is **omitted**: there
        is no expression to evaluate, and the decide agent obtains what it needs by
        calling the consultation tool. Without it, the original two-node path applies.
        """
        graph: StateGraph = StateGraph(_DecisionState)
        graph.add_node("decide", self._decide_node)
        if self._consultation_tool is None:
            graph.add_node("eval_condition", self._eval_condition_node)
            graph.add_edge(START, "eval_condition")
            graph.add_edge("eval_condition", "decide")
        else:
            graph.add_edge(START, "decide")
        graph.add_edge("decide", END)
        return graph.compile()

    # ── Node 1: deterministic condition evaluation (no LLM) ──────────────────

    def _eval_condition_node(self, state: _DecisionState) -> dict:
        """Evaluate the gateway condition analytically and store the result."""
        if not self.condition:
            return {"condition_result": "UNKNOWN"}

        expr = _substitute_vars(self.condition, state["process_variables"])
        result = _safe_compare(expr) if any(op in expr for op in _CMP_OPS) else False
        outcome = "TRUE" if result else "FALSE"

        _tracker.collect_step(
            Step(
                name=f"Gateway {self.gateway_id}: condition eval",
                data=f"`{self.condition}` → `{expr}` → {outcome}",
            )
        )
        logger.info(f"DecisionAgent {self.gateway_id}: eval `{self.condition}` → {outcome}")
        return {"condition_result": outcome}

    # ── Node 2: CugaAgent decision ────────────────────────────────────────────

    async def _decide_node(self, state: _DecisionState) -> dict:
        """CugaAgent selects a flow ID given the evaluation result, state, and policy."""
        flow_lines = "\n".join(f"- {f['id']}: {f['label']}" for f in state["available_flows"])
        task_results_text = (
            "\n".join(
                f"  {k}: {v.get('output', v.get('error', v)) if isinstance(v, dict) else v}"
                for k, v in state.get("task_results", {}).items()
            )
            or "  (none)"
        )

        # With consultation configured, node 1 was skipped — there is no condition
        # result to report, and the agent gets its input by calling consult_user.
        if self._consultation_tool is not None:
            input_section = (
                "## Deciding Input\n"
                "This gateway routes on what the user wants. Call the consult_user tool to "
                "ask them, then choose the flow their answer points to.\n"
                f"What to ask about:\n{self.condition or '(see the policy above)'}"
            )
            basis = "the policy and the user's reply"
        else:
            input_section = f"## Condition Evaluation Result\n{state['condition_result']}"
            basis = "the policy and condition result"

        prompt = (
            f"Gateway '{self.gateway_id}' routing decision.\n\n"
            f"## Decision Policy\n{self.policy}\n\n"
            f"{input_section}\n\n"
            f"## Current Process State\n"
            f"Process variables: {state['process_variables']}\n"
            f"Task results:\n{task_results_text}\n\n"
            f"## Available Flows\n{flow_lines}\n\n"
            f"Based on {basis}, respond with ONLY:\n"
            f"<chosen_flow_id>|<one-sentence reason>\n"
            f"Example: Flow_0ybszcv|Credit score 0.75 meets the approval threshold of 0.60."
        )

        try:
            result = await self._get_agent().invoke(prompt)

            if isinstance(result, dict):
                text = result.get("output", result.get("content", str(result)))
            elif hasattr(result, "messages") and result.messages:
                last = result.messages[-1]
                text = (
                    last.get("content", str(last))
                    if isinstance(last, dict)
                    else getattr(last, "content", str(last))
                )
            else:
                text = str(result)

            available_ids = [f["id"] for f in state["available_flows"]]
            chosen = None
            reason = ""
            for line in text.strip().splitlines():
                if "|" in line:
                    candidate, _, rest = line.partition("|")
                    candidate = candidate.strip()
                    if candidate in available_ids:
                        chosen = candidate
                        reason = rest.strip()
                        break
            if not chosen:
                chosen = next((fid for fid in available_ids if fid in text), available_ids[0])

            _tracker.collect_step(
                Step(
                    name=f"Gateway {self.gateway_id}: routing decision",
                    data=f"→ {chosen}" + (f" — {reason}" if reason else ""),
                )
            )
            logger.info(f"DecisionAgent {self.gateway_id}: decided → {chosen} ({reason})")
            return {"chosen_flow_id": chosen, "routing_reason": reason}

        except Exception as e:
            logger.error(f"DecisionAgent {self.gateway_id}: decide node error: {e}")
            return {"chosen_flow_id": state["available_flows"][0]["id"], "routing_reason": ""}

    # ── CugaAgent (lazy) ──────────────────────────────────────────────────────

    def _get_agent(self):
        """Return (or create) the CugaAgent used in the decide node."""
        if self._agent is None:
            from cuga.sdk import CugaAgent
            from cuga.config import settings

            model_config = (
                settings.agent.planner.model if not self._model_name else {"model": self._model_name}
            )
            llm = LLMManager().get_model(model_config)

            consult_note = (
                "You have a consult_user tool. This gateway routes on the user's wishes, so "
                "call it to ask them, then pick the flow their answer points to. It reports "
                "what they said — the choice remains yours.\n"
                if self._consultation_tool
                else ""
            )
            self._agent = CugaAgent(
                special_instructions=(
                    f"You are a BPMN gateway routing agent for gateway '{self.gateway_id}'.\n"
                    "You will receive a condition evaluation result, current process state, "
                    "and a list of available flows with their decision labels.\n"
                    f"{consult_note}"
                    "Select exactly ONE flow ID from the list. Respond with ONLY:\n"
                    "<chosen_flow_id>|<one-sentence reason>\n"
                    "Example: Flow_0ybszcv|Credit score 0.75 meets the approval threshold of 0.60."
                ),
                model=llm,
                enable_knowledge=False,
                auto_load_policies=False,
                tools=[self._consultation_tool] if self._consultation_tool else None,
            )
            logger.debug(f"DecisionAgent {self.gateway_id}: CugaAgent created")
        return self._agent

    # ── Public interface ──────────────────────────────────────────────────────

    async def route(self, flows: List[BPMNFlow], state: FlowState) -> tuple:
        """
        Run the eval_condition → decide graph and return (chosen_flow_id, reason).

        Args:
            flows: Outgoing BPMNFlow objects from this gateway.
            state: Current FlowState.

        Returns:
            Tuple of (flow_id, one-sentence routing reason).
        """
        if not flows:
            logger.error(f"DecisionAgent {self.gateway_id}: no outgoing flows provided")
            return "", ""

        if len(flows) == 1:
            return flows[0].id, ""

        initial: _DecisionState = {
            "process_variables": dict(state.process_variables or {}),
            "task_results": dict(state.task_results or {}),
            "condition_result": "",
            "available_flows": [
                {"id": f.id, "label": self.flow_decisions.get(f.id) or f.name or f.id} for f in flows
            ],
            "chosen_flow_id": flows[0].id,
            "routing_reason": "",
        }

        result = await self._compiled_graph.ainvoke(initial)
        chosen = result.get("chosen_flow_id", flows[0].id)
        reason = result.get("routing_reason", "")

        chosen_label = next(
            (self.flow_decisions.get(f.id) or f.name or f.id for f in flows if f.id == chosen),
            chosen,
        )
        logger.info(f"DecisionAgent {self.gateway_id}: routed to {chosen_label} ({chosen}) — {reason}")
        return chosen, reason


# Made with Bob
