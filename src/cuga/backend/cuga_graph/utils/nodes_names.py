"""
Constants file containing all hardcoded node names and action IDs used in the agent graph.
"""


# Node Names
class NodeNames:
    """Constants for node names in the agent graph."""

    END = "__end__"
    SUGGEST_HUMAN_ACTIONS = "SuggestHumanActions"
    CUGA_LITE = "CugaLite"
    CUGA_SUPERVISOR = "CugaSupervisor"
    CUGA_BROWSER = "CugaBrowser"
    ENTRY_ROUTER = "EntryRouter"
    WAIT_FOR_RESPONSE = "WaitForResponse"
    CHAT_AGENT = "ChatAgent"
    FINAL_ANSWER_AGENT = "FinalAnswerAgent"
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
