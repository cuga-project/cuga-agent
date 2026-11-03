import os

# Set the environment file to the .env file in the current directory
os.environ["ENV_FILE"] = os.path.join(os.path.dirname(__file__), ".env")
os.environ["MCP_SERVERS_FILE"] = os.path.join(os.path.dirname(__file__), "mcp_servers.yaml")

from cuga.backend.activity_tracker.tracker import ActivityTracker
from cuga.backend.cuga_graph.utils.controller import AgentRunner as CugaAgent, ExperimentResult as AgentResult
from fastmcp import FastMCP

# Initialize components
tracker = ActivityTracker()
mcp = FastMCP("CUGA Running as MCP 🚀")


@mcp.tool
async def run_api_task(task: str) -> str:
    """
    Run a task using API mode only - headless browser automation without GUI interaction
    Args:
        task: The task description to execute

    Returns:
        str: The result of the task execution
    """
    os.environ["DYNA_CONF_ADVANCED_FEATURES__MODE"] = "api"
    cuga_agent = CugaAgent(browser_enabled=False)
    await cuga_agent.initialize_appworld_env()
    task_result: AgentResult = await cuga_agent.run_task_generic(eval_mode=False, goal=task)
    return task_result.answer


@mcp.tool
async def run_web_task(task: str, start_url: str) -> str:
    """
    Run a task using web mode only - browser automation with GUI interaction
    Args:
        task: The task description to execute
        start_url: The starting URL for the task

    Returns:
        str: The result of the task execution
    """
    os.environ["DYNA_CONF_ADVANCED_FEATURES__MODE"] = "web"
    cuga_agent = CugaAgent(browser_enabled=False)
    try:
        await cuga_agent.initialize_freemode_env(start_url=start_url, interface_mode="browser_only")
        task_result: AgentResult = await cuga_agent.run_task_generic(eval_mode=False, goal=task)
    except Exception:
        if hasattr(cuga_agent, "env") and cuga_agent.env:
            cuga_agent.env.close()
        raise
    else:
        cuga_agent.env.close()
        return task_result.answer


@mcp.tool
async def run_hybrid_task(task: str, start_url: str) -> str:
    """
    Run a task using hybrid mode - combination of API and web interaction
    Args:
        task: The task description to execute
        start_url: The starting URL for the task

    Returns:
        str: The result of the task execution
    """
    os.environ["DYNA_CONF_ADVANCED_FEATURES__MODE"] = "hybrid"
    cuga_agent = CugaAgent(browser_enabled=False)
    try:
        await cuga_agent.initialize_freemode_env(start_url=start_url, interface_mode="browser_only")
        task_result: AgentResult = await cuga_agent.run_task_generic(eval_mode=False, goal=task)
    except Exception:
        if hasattr(cuga_agent, "env") and cuga_agent.env:
            cuga_agent.env.close()
        raise
    else:
        cuga_agent.env.close()
        return task_result.answer


def main():
    """Main function to run the MCP server."""
    print("Starting FastMCP server...")
    print("Server must be running before starting the main application.")
    mcp.run(transport="sse")


if __name__ == "__main__":
    main()
