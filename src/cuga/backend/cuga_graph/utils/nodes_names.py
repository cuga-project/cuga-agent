"""
Constants file containing all hardcoded node names and action IDs used in the agent graph.
"""


# Node Names
class NodeNames:
    """Constants for node names in the agent graph."""

    END = "__end__"
    SUGGEST_HUMAN_ACTIONS = "SuggestHumanActions"
    REUSE_AGENT = "ReuseAgent"
    CUGA_LITE = "CugaLite"
    CUGA_SUPERVISOR = "CugaSupervisor"
    API_CODE_PLANNER_AGENT = "APICodePlannerAgent"
    SHORTLISTER_AGENT = "ShortlisterAgent"
    DECOMPOSITION_AGENT = "TaskDecompositionAgent"
    WAIT_FOR_RESPONSE = "WaitForResponse"
    CHAT_AGENT = "ChatAgent"
    API_PLANNER_AGENT = "APIPlannerAgent"
    CODE_AGENT = "CodeAgent"
    PLAN_CONTROLLER_AGENT = "PlanControllerAgent"
    FINAL_ANSWER_AGENT = "FinalAnswerAgent"
    TASK_ANALYZER_AGENT = "TaskAnalyzerAgent"
    MEMORY_AGENT = "MemoryAgent"


# Action IDs
class ActionIds:
    """Constants for human-in-the-loop action IDs."""

    SAVE_REUSE = "save_reuse"
    SAVE_REUSE_INTENT = "save_reuse_intent"
    FLOW_APPROVE = "flow_approve"
    NEW_FLOW_APPROVE = "new_flow_approve"
    CONSULT_WITH_HUMAN = "consult_with_human"
    TOOL_APPROVAL = "tool_approval"
    AGENT_APPROVAL = "agent_approval"


# Message Prefixes
class MessagePrefixes:
    """Constants for message content prefixes."""

    ANSWER_PREFIX = "\n\nAnswer: "
