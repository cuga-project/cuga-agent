"""
Loan Approval FlowAgent Instance

Creates the FlowAgent that orchestrates the BPMN-based loan approval workflow.
"""

from pathlib import Path
from cuga.backend.cuga_graph.nodes.cuga_flow.flow_agent import FlowAgent
from cuga.backend.cuga_graph.nodes.cuga_flow.task_agent import TaskAgent
from cuga.backend.cuga_graph.nodes.cuga_flow.hook_manager import Hook, HookType, HookAction, HookResult

from docs.examples.flow_agent_app.agents.credit_checker import credit_checker_agent
from docs.examples.flow_agent_app.agents.loan_processor import loan_processor_agent
from docs.examples.flow_agent_app.agents.rejection_handler import rejection_handler_agent
from docs.examples.flow_agent_app.agents.credit_decision import credit_decision_agent

CURRENT_DIR = Path(__file__).parent
_CONFIG = CURRENT_DIR / "config"
_POLICIES = _CONFIG / "policies"

loan_flow_agent = FlowAgent(
    bpmn_file=str(_CONFIG / "BPMNdiagram.bpmn"),
    task_agents={
        "Activity_0oydey5": TaskAgent(
            task_id="Activity_0oydey5",
            task_name="Check Credit",
            agent=credit_checker_agent,
        ),
        "Activity_1h9ix55": TaskAgent(
            task_id="Activity_1h9ix55",
            task_name="Give Loan",
            agent=loan_processor_agent,
        ),
        "Activity_131ar38": TaskAgent(
            task_id="Activity_131ar38",
            task_name="Send Rejection",
            agent=rejection_handler_agent,
        ),
    },
    task_policies={
        "Activity_0oydey5": (_POLICIES / "task-credit_check.md").read_text(),
        "Activity_1h9ix55": (_POLICIES / "task-loan_processor.md").read_text(),
        "Activity_131ar38": (_POLICIES / "task-rejection_handler.md").read_text(),
    },
    gateway_agents={
        "Gateway_09ad5fc": credit_decision_agent,  # credit decision gateway (LLM + policy)
        # Gateway_15y9k7z is a merge gateway — tool-mode (single outgoing flow, no decision needed)
    },
    process_variables={
        "applicant_name": "",
        "applicant_id": "",
        "loan_amount": 1000,
        "credit_score": 0.0,
        "approved": False,
    },
    hooks=[
        Hook(
            id="audit_credit_decision",
            hook_type=HookType.EDGE,
            location="Flow_0ybszcv",
            handler=lambda state: HookResult(action=HookAction.CONTINUE),
            priority=0,
            policy=(_POLICIES / "hook-audit_credit_decision.md").read_text(),
        ),
    ],
)

# Made with Bob
