import os
import subprocess
import asyncio

from cuga.backend.cuga_graph.nodes.cuga_lite.executors.code_executor import CodeExecutor
from cuga.backend.cuga_graph.state.agent_state import AgentState
from cuga.config import settings, PACKAGE_ROOT
from cuga.backend.activity_tracker.tracker import ActivityTracker
from loguru import logger

tracker = ActivityTracker()


def run_petstore():
    server_module = "agent/api/example_api_servers/petstore.py"
    from cuga.config import settings

    subprocess.run(
        [
            "uv",
            "run",
            "fastapi",
            "dev",
            server_module,
            "--no-reload",
            f"--port={settings.server_ports.petstore_api}",
        ]
    )


def run_api_registry_base(mode):
    """Run the FastAPI server."""
    if mode == "appworld":
        os.environ["MCP_SERVERS_FILE"] = os.path.join(
            PACKAGE_ROOT, "backend/tools_env/registry/config/mcp_servers_appworld.yaml"
        )
    host = os.environ.get("CUGA_HOST", "127.0.0.1")
    server_module = os.path.join(PACKAGE_ROOT, "backend/tools_env/registry/registry/api_registry_server.py")
    subprocess.run(
        [
            "uv",
            "run",
            "fastapi",
            "dev",
            server_module,
            f"--host={host}",
            "--no-reload",
            f"--port={settings.server_ports.registry}",
        ]
    )


def run_api_registry():
    run_api_registry_base('demo')


def run_api_registry_appworld():
    run_api_registry_base('appworld')


def run_demo():
    """Run the FastAPI server."""
    host = os.environ.get("CUGA_HOST", "127.0.0.1")
    server_module = "backend/server/main.py"
    subprocess.run(
        [
            "uv",
            "run",
            "fastapi",
            "dev",
            os.path.join(PACKAGE_ROOT, server_module),
            f"--host={host}",
            "--no-reload",
            f"--port={settings.server_ports.demo}",
        ]
    )


def run_eval_api():
    """Run the FastAPI server."""
    subprocess.run(["python", "server/main.py", "--api-mode"])


def run_digital_sales_mcp():
    subprocess.run(
        [
            "uv",
            "run",
            os.path.join(
                PACKAGE_ROOT, "..", "..", "docs/examples/cuga_with_runtime_tools/fast_mcp_example.py"
            ),
        ]
    )


def run_digital_sales_openapi():
    """Run the digital sales OpenAPI server."""
    host = os.environ.get("CUGA_HOST", "127.0.0.1")
    server_module = os.path.join(PACKAGE_ROOT, "..", "..", "docs/examples/digital_sales_openapi/main.py")
    subprocess.run(
        [
            "uv",
            "run",
            "fastapi",
            "dev",
            server_module,
            f"--host={host}",
            "--no-reload",
            f"--port={settings.server_ports.digital_sales_api}",
        ]
    )


async def _test_sandbox_async(remote: bool = False):
    tracker.current_date = "2023-05-18T12:00:00"

    if remote:
        os.environ["DYNACONF_FEATURES__LOCAL_SANDBOX"] = "false"
        logger.info("Testing with remote sandbox (Docker/Podman)...")
        mode = 'docker'
    else:
        os.environ["DYNACONF_FEATURES__LOCAL_SANDBOX"] = "true"
        logger.info("Testing with local sandbox...")
        mode = 'local'

    # Create a simple state for testing
    state = AgentState(input="test", url="")

    # Use CodeExecutor instead of deprecated run_code
    code = "print('test succeeded')"
    result, _ = await CodeExecutor.eval_with_tools_async(
        code=code,
        _locals={},
        state=state,
        mode=mode,
    )
    logger.info(result)


def test_sandbox(remote: bool = False):
    """Test sandbox execution.

    Args:
        remote: If True, test with remote Docker/Podman sandbox. If False (default), test with local sandbox.
    """
    asyncio.run(_test_sandbox_async(remote))


def test_llm():
    """Send 'hi' to the LLM via LLMManager and print the output."""
    from langchain_core.messages import HumanMessage

    from cuga.backend.llm.models import LLMManager
    from cuga.config import settings

    llm_manager = LLMManager()
    model_config = getattr(settings.agent.chat, "model", None) or settings.agent.code.model
    model = llm_manager.get_model(model_config)
    response = model.invoke([HumanMessage(content="hi")])
    print(response.content)


def setup_appworld_environment():
    """Set up the appworld environment with necessary data and installations."""
    import subprocess
    import sys

    # Check if appworld directory exists
    if not os.path.isdir("appworld"):
        print("Error: 'appworld' directory not found!")
        print("Please clone the repository first")
        sys.exit(1)

    # Change to appworld directory
    os.chdir("appworld")

    # Install the package
    subprocess.run(["uv", "pip", "install", "."])
    # Note: For experiment reproduction use:
    subprocess.run(["python", "-m", "appworld.cli", "install"])

    # Unpack encrypted code
    subprocess.run(["appworld", "install", "--repo"])

    # Download benchmark data
    subprocess.run(["appworld", "download", "data"])

    # Copy data folder to evaluation folder
    # subprocess.run(["cp", "-r", "./data", "../evaluation/"])

    print("Appworld environment setup complete!")


def setup_appworld_environment_docker():
    """Set up the appworld environment with necessary data and installations."""
    import subprocess
    import sys

    # Check if appworld directory exists
    if not os.path.isdir("/app/appworld"):
        print("Error: 'appworld' directory not found!")
        print("Please clone the repository first")
        sys.exit(1)

    # Change to appworld directory
    os.chdir("/app/appworld")

    # Install the package
    subprocess.run(["uv", "pip", "install", "."])
    # Note: For experiment reproduction use:
    subprocess.run(["python", "-m", "appworld.cli", "install"])
    #
    # # Unpack encrypted code
    # subprocess.run(["appworld", "install", "--repo"])
    #
    # # Download benchmark data
    # subprocess.run(["appworld", "download", "data"])

    # Copy data folder to evaluation folder
    # subprocess.run(["cp", "-r", "./data", "../evaluation/"])

    print("Appworld environment setup complete!")
