"""Native function calling with the CugaAgent SDK (issue #471).

CUGA-lite is a code-act agent: by default the model calls tools by writing
Python. Native *function calling* is an opt-in invocation mode where the model
may instead emit native tool calls; CUGA executes them through the same guarded,
tracked, variable-aware sandbox pipeline.

Enable it per agent or per invoke with ``ToolCalling``. When it is off (the
default), nothing changes — the agent behaves exactly as before.

Run (uses whatever model your .env / AGENT_SETTING_CONFIG selects):

    LOCAL_SANDBOX=true uv run python docs/examples/native_function_calling/main.py
"""

import asyncio

from langchain_core.tools import tool

from cuga import CugaAgent, ToolCalling

NOTIFIED = []  # ground truth: which customers the tool actually notified


@tool
def notify(customer: str) -> str:
    """Send a notification to a single customer by name."""
    NOTIFIED.append(customer)
    return f"notification sent to {customer}"


async def main():
    agent = CugaAgent(
        tools=[notify],
        # Enable native function calling for this agent. Options:
        #   ToolCalling(mode="native")                     -> bind all tools
        #   ToolCalling(mode="native", native_tools=[...])  -> bind specific tools
        #   ToolCalling(mode="hybrid", apps=["crm"])        -> code + native, per app
        # Omit tool_calling entirely to keep the default code-act behavior.
        tool_calling=ToolCalling(mode="native"),
        auto_load_policies=False,
        enable_knowledge=False,
        enable_skills=False,
    )

    # A tool-calling-trained model may answer this with several native tool
    # calls in one turn; they all execute (before the fix, only the first ran).
    result = await agent.invoke("Notify these customers: acme, globex, and initech.")
    print("answer:", result.answer)
    print("customers actually notified:", sorted(set(NOTIFIED)))

    # Per-invoke override: turn native FC on (or off) for a single call.
    # await agent.invoke("...", tool_calling=ToolCalling(mode="code"))


if __name__ == "__main__":
    asyncio.run(main())
