"""
Supervisor Configuration Loader - Loads supervisor configuration from YAML files
"""

import yaml
from typing import Any, Dict, List, Optional, TYPE_CHECKING
from loguru import logger
from pydantic import BaseModel

if TYPE_CHECKING:
    pass

from cuga.backend.cuga_graph.nodes.cuga_lite.providers.base import ToolProviderInterface
from cuga.backend.cuga_graph.nodes.cuga_lite.providers.combined import CombinedToolProvider


class SupervisorConfig(BaseModel):
    """Configuration for supervisor loaded from YAML."""

    supervisor: Dict[str, Any] = {}
    agents: Dict[str, Any] = {}  # Can contain CugaAgent instances or A2A configs
    a2a: Dict[str, Any] = {}


async def load_supervisor_config(
    yaml_path: str, *, auto_load_policies: Optional[bool] = None, scope_tools: bool = False
) -> SupervisorConfig:
    """
    Load and parse supervisor YAML configuration.
    Creates internal CugaAgent instances from YAML config.

    Args:
        yaml_path: Path to YAML configuration file
        auto_load_policies: Default for sub-agents that do not set it themselves.
            ``None`` (the default) preserves existing behaviour — each CugaAgent falls back to
            ``settings.policy.auto_load_policies``. Pass ``False`` for HEADLESS callers (scheduled
            flows, webhooks, channel events): nobody is present to answer an approval interrupt, so
            one would hang the run until the caller times out. A per-agent ``auto_load_policies:``
            key in the YAML always wins over this.
        scope_tools: Restrict each sub-agent to the tools of the apps/mcp_servers it NAMES.
            ``False`` (the default) preserves existing behaviour — every sub-agent is handed the
            whole registry, so a YAML that names some apps but calls a tool it did not name keeps
            working. Pass ``True`` for the events roster, where a sub-agent is a specialist and
            handing it the full registry both blurs delegation and inflates its prompt.

            Opt-IN for the same reason ``auto_load_policies`` is asked for at the call site:
            ``CugaSupervisor.from_yaml`` and every other existing caller must not silently lose
            tools. Scoping by default was a breaking change for any roster in the wild.

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

        elif "import_from" in agent_config:
            # Import a pre-configured CugaAgent instance from a Python module.
            # This lets you define agents fully in Python (with tools, policies, etc.)
            # and reference them from YAML without any duplication.
            #
            # YAML usage:
            #   - name: my_agent
            #     import_from: my_package.agents.my_agent.my_agent_instance
            #
            # The last dotted segment is the attribute name; everything before it is
            # the module path.  Example:
            #   import_from: docs.examples.travel_agent.agents.flight_agent.flight_agent
            #   → importlib.import_module("docs.examples.travel_agent.agents.flight_agent")
            #   → getattr(module, "flight_agent")
            import_path = agent_config["import_from"]
            logger.info(f"Importing pre-configured agent '{agent_name}' from {import_path}")
            try:
                import importlib

                module_path, agent_var = import_path.rsplit(".", 1)
                module = importlib.import_module(module_path)
                agent = getattr(module, agent_var)

                # Use class-name check to avoid issues when the same class is imported
                # from different paths (which would break isinstance()).
                if not (hasattr(agent, "__class__") and agent.__class__.__name__ == "CugaAgent"):
                    raise TypeError(
                        f"Imported object '{agent_var}' from '{module_path}' is not a "
                        f"CugaAgent instance (got {type(agent).__name__})"
                    )

                agents[agent_name] = agent
                logger.info(f"✅ Imported agent '{agent_name}' from {import_path}")
            except Exception as e:
                logger.error(f"Failed to import agent '{agent_name}' from '{import_path}': {e}")
                raise

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
                scope_tools=scope_tools,
            )

            # Get model config if specified
            model = _get_model_from_config(agent_config.get("model"))

            # Policy auto-load. Precedence: the YAML entry wins; otherwise the caller's default;
            # otherwise None, which lets CugaAgent fall back to `settings.policy.auto_load_policies`
            # exactly as it always has.
            #
            # This defaulted to False for a while, which was a silent regression for everyone else:
            # `load_supervisor_config` is public API (CugaSupervisor.from_yaml, documented in the
            # README, plus cuga_graph/graph.py), so hardcoding False disabled policy loading for
            # every downstream supervisor user regardless of their settings — with no error to
            # notice. Headless callers now ask for it explicitly instead of imposing it on all.
            agent = CugaAgent(
                tools=tools,
                tool_provider=tool_provider,
                special_instructions=agent_config.get("special_instructions"),
                model=model,
                auto_load_policies=agent_config.get("auto_load_policies", auto_load_policies),
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
    *,
    scope_tools: bool = False,
) -> Optional[ToolProviderInterface]:
    """
    Create a tool provider from apps and MCP servers configuration.
    Tools will be loaded from the registry based on app names.

    Args:
        apps: List of app configurations (can be dict with 'name' or just string name)
        mcp_servers: List of MCP server configurations
        scope_tools: See ``load_supervisor_config``. ``False`` hands over the whole registry, which
            is what every caller got before scoping existed; ``True`` restricts to the named apps.

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

    # mcp_servers entries contribute their names to the same registry-app filter
    for m in mcp_servers or []:
        n = m.get("name") if isinstance(m, dict) else str(m)
        if n:
            app_names.append(n)

    # Scoping is OPT-IN. `app_names=None` is the pre-existing behaviour: CombinedToolProvider
    # loads the whole registry and the YAML's app list is descriptive only. Turning that into a
    # filter by default silently removed tools from any roster that called something it had not
    # named, on a public API (`CugaSupervisor.from_yaml`) — so the events roster asks for it and
    # nobody else changes.
    #
    # When scoping IS on: registry keys are underscore names ('cuga_finance'); hyphenated names
    # would compose invalid Python identifiers downstream ('cuga-finance_get_price' parses as
    # subtraction), so map them. An agent that names NOTHING still gets all tools.
    if app_names or mcp_servers:
        scoped = ([n.replace("-", "_") for n in app_names] or None) if scope_tools else None
        logger.info(f"Creating CombinedToolProvider scoped to: {scoped or 'ALL apps'}")
        tool_provider = CombinedToolProvider(app_names=scoped)
        await tool_provider.initialize()
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
