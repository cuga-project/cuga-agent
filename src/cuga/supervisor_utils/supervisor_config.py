"""
Supervisor Configuration Loader - Loads supervisor configuration from YAML files
"""

import yaml
from typing import Any, Dict, List, Optional, TYPE_CHECKING
from loguru import logger
from pydantic import BaseModel

if TYPE_CHECKING:
    pass

from cuga.backend.cuga_graph.nodes.cuga_lite.tool_provider_interface import ToolProviderInterface
from cuga.backend.cuga_graph.nodes.cuga_lite.combined_tool_provider import CombinedToolProvider


class SupervisorConfig(BaseModel):
    """Configuration for supervisor loaded from YAML."""

    supervisor: Dict[str, Any] = {}
    agents: Dict[str, Any] = {}  # Can contain CugaAgent instances or A2A configs
    a2a: Dict[str, Any] = {}


async def load_supervisor_config(yaml_path: str) -> SupervisorConfig:
    """
    Load and parse supervisor YAML configuration.
    Creates internal CugaAgent instances from YAML config.

    Args:
        yaml_path: Path to YAML configuration file

    Returns:
        SupervisorConfig with loaded configuration
    """
    with open(yaml_path, "r") as f:
        config = yaml.safe_load(f)

    agents = {}

    for agent_config in config.get("agents", []):
        agent_name = agent_config["name"]

        # Check if this is an external agent (has a2a_protocol)
        if "a2a_protocol" in agent_config and agent_config.get("a2a_protocol", {}).get("enabled"):
            # External agent via A2A - store config for later connection
            agents[agent_name] = {
                "type": "external",
                "config": agent_config,
            }
            logger.info(f"Registered external agent: {agent_name}")
        else:
            # Internal agent - create CugaAgent instance
            logger.info(f"Creating internal agent: {agent_name}")

            # Import here to avoid circular import
            from cuga.sdk import CugaAgent

            # Load tools
            tools = await _load_tools_from_config(agent_config.get("tools", []))

            # Create tool provider - apps can be list of strings (app names) or list of dicts
            apps_config = agent_config.get("apps", [])
            mcp_servers_config = agent_config.get("mcp_servers", [])

            tool_provider = await _create_tool_provider(
                apps=apps_config,
                mcp_servers=mcp_servers_config,
            )

            # Get model config if specified
            model = _get_model_from_config(agent_config.get("model"))

            # Create agent
            agent = CugaAgent(
                tools=tools,
                tool_provider=tool_provider,
                special_instructions=agent_config.get("special_instructions"),
                model=model,
            )

            agents[agent_name] = agent
            logger.info(f"Created internal CugaAgent: {agent_name}")

    return SupervisorConfig(
        supervisor=config.get("supervisor", {}),
        agents=agents,
        a2a=config.get("a2a", {}),
    )


async def _load_tools_from_config(tools_config: List[Dict[str, Any]]) -> List[Any]:
    """
    Load tools from YAML configuration.

    Args:
        tools_config: List of tool configurations from YAML

    Returns:
        List of tool instances
    """
    tools = []

    for tool_config in tools_config:
        tool_name = tool_config.get("name")
        tool_type = tool_config.get("type", "langchain")

        if tool_type == "langchain":
            # For now, we can't load LangChain tools from YAML directly
            # This would require tool definitions or references
            # Placeholder for future implementation
            logger.warning(f"LangChain tool '{tool_name}' from YAML not yet supported - skipping")
        else:
            logger.warning(f"Unknown tool type '{tool_type}' for '{tool_name}' - skipping")

    return tools


async def _create_tool_provider(
    apps: List[Dict[str, Any]],
    mcp_servers: List[Dict[str, Any]],
) -> Optional[ToolProviderInterface]:
    """
    Create a tool provider from apps and MCP servers configuration.
    Tools will be loaded from the registry based on app names.

    Args:
        apps: List of app configurations (can be dict with 'name' or just string name)
        mcp_servers: List of MCP server configurations

    Returns:
        ToolProviderInterface instance or None
    """
    if not apps and not mcp_servers:
        return None

    # Extract app names from config
    app_names = []
    for app_config in apps:
        if isinstance(app_config, dict):
            app_name = app_config.get("name")
            if app_name:
                app_names.append(app_name)
        elif isinstance(app_config, str):
            app_names.append(app_config)

    # Create CombinedToolProvider which loads tools from registry
    # CombinedToolProvider can filter by app names if provided
    if app_names or mcp_servers:
        logger.info(
            f"Creating CombinedToolProvider for apps: {app_names}, MCP servers: {len(mcp_servers) if mcp_servers else 0}"
        )
        tool_provider = CombinedToolProvider()
        await tool_provider.initialize()

        # If specific app names are provided, filter the apps
        if app_names:
            # CombinedToolProvider loads all apps by default, but we can filter
            # The tools will be loaded from registry based on app names when get_tools() is called
            # For now, we'll let it load all and filter at tool retrieval time
            logger.info(f"Tools will be loaded from registry for apps: {app_names}")

        return tool_provider

    return None


def _get_model_from_config(model_config: Optional[Dict[str, Any]]):
    """
    Get model instance from configuration.

    Args:
        model_config: Model configuration dict

    Returns:
        BaseChatModel instance or None
    """
    if not model_config:
        return None

    from cuga.backend.llm.models import LLMManager
    from cuga.config import settings

    llm_manager = LLMManager()

    # Build model config - use default settings as base
    provider = model_config.get("provider", "openai")
    model_name = model_config.get("model_name", "gpt-4o")

    # Get default model config for the provider
    default_config = settings.agent.code.model.copy()

    # Create model config dict with defaults and overrides
    model_settings = {
        "provider": provider,
        "model_name": model_name,
        "max_tokens": model_config.get("max_tokens", default_config.get("max_tokens", 16000)),
        **{k: v for k, v in model_config.items() if k not in ["provider", "model_name"]},
    }

    try:
        model = llm_manager.get_model(model_settings)
        logger.info(f"Created model: {provider}/{model_name}")
        return model
    except Exception as e:
        logger.error(f"Failed to create model from config: {e}")
        return None
