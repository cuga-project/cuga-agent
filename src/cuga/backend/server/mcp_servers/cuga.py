# server.py

from fastmcp import FastMCP

from cuga.backend.cuga_graph.utils.controller import AgentRunner as CugaAgent, ExperimentResult as AgentResult
from loguru import logger

# Create an MCP server
mcp = FastMCP("Demo")

cuga_agent = None


async def run_task(task: str, start_url: str):
    global cuga_agent
    if not cuga_agent:
        cuga_agent = CugaAgent()
        await cuga_agent.initialize_freemode_env(start_url)
    else:
        await cuga_agent.env.close()
    task_result: AgentResult = await cuga_agent.run_task_generic(eval_mode=False, goal=task)
    return task_result


# Add an addition tool
@mcp.tool()
async def perform_ui_task(start_url: str, task: str) -> str:
    """Perform a task on web application based on starting URL and task string
    :param start_url starting url
    :param task task definition as string
    """
    try:
        agent_result: AgentResult = await run_task(task, start_url)
    except Exception as e:
        logger.exception(e)
        agent_result = AgentResult(answer="Task failed", messages=[], score=0.0)
    return agent_result.answer


if __name__ == "__main__":
    mcp.run(transport="stdio")
