"""
DecisionAgent - Per-gateway LLM-powered routing for BPMN processes.

Each DecisionAgent is bound to one gateway and wraps a CugaAgent instance.
The CugaAgent is equipped with an `evaluate_condition` tool that analytically
evaluates BPMN condition expressions using safe string parsing (no eval()).

Routing flow:
  1. `evaluate_condition` tool — called by the LLM to assess the gateway condition
                                 expression against current process_variables.
  2. LLM decision — reads the gateway's decision policy (markdown), the tool result,
                    and the full process state to select the flow ID to follow.

Implementation note: route() uses async direct LangChain tool-calling (llm.bind_tools)
rather than CugaAgent.invoke(). CugaAgent uses CugaLite's code-act model whose
verbose final-answer extraction is unreliable for returning a specific token (flow ID).
Direct tool-calling is deterministic: LLM calls the tool, gets TRUE/FALSE, replies
with only the chosen flow ID. The CugaAgent is still created (lazy) so the agent is
conceptually a CugaAgent with a registered tool; it is exposed via _get_agent().

If a gateway is configured in "native" mode the FlowAgent routes it inline using
condition eval alone — no DecisionAgent instance is created for it.

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

    Node 1 — ``eval_condition``: deterministic condition evaluation using safe
    string parsing (no eval(), no LLM). Stores "TRUE" / "FALSE" / "UNKNOWN".

    Node 2 — ``decide``: CugaAgent reads the evaluation result, process state,
    and the gateway's decision policy, then returns exactly one flow ID.

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
    ):
        self.gateway_id = gateway_id
        self.policy = policy
        self.condition = condition
        self.flow_decisions: Dict[str, str] = flow_decisions or {}
        self._model_name = model_name
        self._temperature = temperature

        # CugaAgent for the decide node — created lazily.
        self._agent: Optional[Any] = None

        # Compile the internal two-node routing graph once at construction time.
        self._compiled_graph = self._build_graph()

    # ── Graph construction ────────────────────────────────────────────────────

    def _build_graph(self):
        """Compile the eval_condition → decide StateGraph."""
        graph: StateGraph = StateGraph(_DecisionState)
        graph.add_node("eval_condition", self._eval_condition_node)
        graph.add_node("decide", self._decide_node)
        graph.add_edge(START, "eval_condition")
        graph.add_edge("eval_condition", "decide")
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

        prompt = (
            f"Gateway '{self.gateway_id}' routing decision.\n\n"
            f"## Decision Policy\n{self.policy}\n\n"
            f"## Condition Evaluation Result\n{state['condition_result']}\n\n"
            f"## Current Process State\n"
            f"Process variables: {state['process_variables']}\n"
            f"Task results:\n{task_results_text}\n\n"
            f"## Available Flows\n{flow_lines}\n\n"
            f"Based on the policy and condition result, respond with ONLY:\n"
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

            self._agent = CugaAgent(
                special_instructions=(
                    f"You are a BPMN gateway routing agent for gateway '{self.gateway_id}'.\n"
                    "You will receive a condition evaluation result, current process state, "
                    "and a list of available flows with their decision labels.\n"
                    "Select exactly ONE flow ID from the list. Respond with ONLY:\n"
                    "<chosen_flow_id>|<one-sentence reason>\n"
                    "Example: Flow_0ybszcv|Credit score 0.75 meets the approval threshold of 0.60."
                ),
                model=llm,
                enable_knowledge=False,
                auto_load_policies=False,
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
