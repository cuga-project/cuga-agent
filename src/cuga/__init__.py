"""
CUGA: The Configurable Generalist Agent

CUGA is a state-of-the-art generalist agent designed for enterprise needs,
combining best-of-breed agentic patterns with structured planning and smart
variable management.

Quick Start:
    ```python
    from cuga import CugaAgent
    from langchain_core.tools import tool

    @tool
    def get_weather(city: str) -> str:
        '''Get weather for a city'''
        return f"Weather in {city}: Sunny"

    agent = CugaAgent(tools=[get_weather])
    result = await agent.invoke("What's the weather in NYC?")
    print(result.answer)
    ```

For more information, visit: https://cuga.dev
"""

from cuga.sdk import CugaAgent, CugaSupervisor, run_agent, InvokeResult
from cuga.backend.cuga_graph.nodes.cuga_lite.tool_call_tracker import tracked_tool
from cuga.backend.knowledge import KnowledgeClient, KnowledgeEngine
from cuga.backend.knowledge.config import KnowledgeConfig

__version__ = "0.2.20"
__all__ = [
    "CugaAgent",
    "CugaSupervisor",
    "run_agent",
    "InvokeResult",
    "tracked_tool",
    "KnowledgeClient",
    "KnowledgeEngine",
    "KnowledgeConfig",
]
