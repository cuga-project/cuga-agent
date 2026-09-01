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


async def build_agents_from_list(agents_list: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Build a ``{agent_name: CugaAgent | external-config-dict}`` map from a list of
    agent config dicts (the ``agents:`` section of a supervisor YAML file).

    Shared by the YAML loader (:func:`load_supervisor_config`) and the manage-UI
    store-sourced loader (:func:`build_agents_from_stored_subagents`), so both paths
    feed :func:`cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_graph.create_cuga_supervisor_graph`
    the exact same agent shapes.
    """
    agents = {}

    for agent_config in agents_list:
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
                agent_id=agent_config.get("agent_id"),
                include_by_app=agent_config.get("include_by_app"),
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
            feature_overrides = agent_config.get("feature_overrides") or {}
            agent._feature_overrides = {k: v for k, v in feature_overrides.items() if v is not None}

            agents[agent_name] = agent
            logger.info(f"Created internal CugaAgent: {agent_name}")

    return agents


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

    agents = await build_agents_from_list(config.get("agents", []))

    return SupervisorConfig(
        supervisor=config.get("supervisor", {}),
        agents=agents,
        a2a=config.get("a2a", {}),
    )


async def build_agents_from_stored_subagents(sub_agents: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Build a ``{agent_name: CugaAgent | external-config-dict}`` map from the manage-UI's
    stored ``supervisor.subAgents`` list (see ``agents_routes.py`` / ``ManagePage.tsx``).

    Each entry is either:
      - ``{"kind": "internal", "ref": "<agent_id>"}`` — resolved to a CugaAgent built
        from that agent's own *published* config (tools/apps/model/special_instructions).
      - ``{"kind": "a2a", "name", "endpoint", "auth": {"type": "bearer", "tokenEnvVar"}, "timeout"}``
        — resolved to the same external-agent dict shape :func:`build_agents_from_list`
        produces from a YAML ``a2a_protocol`` block, so delegation.py drives both identically.
    """
    import os

    from cuga.backend.server.config_store import load_config
    from cuga.backend.server.manage_routes.helpers import extract_agent_feature_overrides

    agent_configs: List[Dict[str, Any]] = []

    for entry in sub_agents:
        kind = entry.get("kind")
        if kind == "internal":
            ref = entry.get("ref")
            if not ref:
                continue
            ref_config, _ = await load_config(None, ref)
            if not ref_config:
                logger.warning(f"Supervisor sub-agent '{ref}': no published config found, skipping")
                continue
            agent_meta = ref_config.get("agent") or {}
            tools_list = ref_config.get("tools") or []
            include_by_app = {
                t["name"]: t["include"]
                for t in tools_list
                if t.get("name") and isinstance(t.get("include"), list) and len(t["include"]) > 0
            } or None
            # ref_config["tools"] holds registry-app entries (name + include filter), not
            # loadable langchain tool defs — pass app names through `apps` and skip the
            # `tools` key so `_load_tools_from_config` (a langchain-only stub) doesn't warn.
            agent_configs.append(
                {
                    "name": ref,
                    "agent_id": ref,
                    "apps": [t["name"] for t in tools_list if t.get("name")],
                    "include_by_app": include_by_app,
                    "special_instructions": ref_config.get("special_instructions")
                    or agent_meta.get("description"),
                    "model": _model_config_from_stored_llm(ref_config.get("llm")),
                    "feature_overrides": extract_agent_feature_overrides(ref_config),
                }
            )
        elif kind == "a2a":
            name = entry.get("name")
            endpoint = entry.get("endpoint")
            if not name or not endpoint:
                logger.warning("Supervisor A2A sub-agent missing name or endpoint, skipping")
                continue
            auth_cfg = entry.get("auth") or {}
            resolved_auth = None
            if auth_cfg.get("type") == "bearer":
                token_env_var = auth_cfg.get("tokenEnvVar")
                token = os.environ.get(token_env_var) if token_env_var else None
                if token:
                    resolved_auth = {"type": "bearer", "token": token}
                elif token_env_var:
                    logger.warning(f"Supervisor A2A sub-agent '{name}': env var {token_env_var} is empty")
            agent_configs.append(
                {
                    "name": name,
                    "a2a_protocol": {
                        "enabled": True,
                        "endpoint": endpoint,
                        "transport": "http",
                        "auth": resolved_auth,
                        "timeout": entry.get("timeout", 30),
                    },
                }
            )
        else:
            logger.warning(f"Unknown supervisor sub-agent kind: {kind!r}")

    return await build_agents_from_list(agent_configs)


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
    agent_id: Optional[str] = None,
    include_by_app: Optional[Dict[str, List[str]]] = None,
) -> Optional[ToolProviderInterface]:
    """Create a tool provider from apps and MCP servers configuration."""
    if not apps and not mcp_servers:
        return None

    app_names = []
    for app_config in apps:
        if isinstance(app_config, dict):
            app_name = app_config.get("name")
            if app_name:
                app_names.append(app_name)
        elif isinstance(app_config, str):
            app_names.append(app_config)

    if app_names or mcp_servers:
        logger.info(
            f"Creating CombinedToolProvider for apps: {app_names}, MCP servers: {len(mcp_servers) if mcp_servers else 0}"
        )
        tool_provider = CombinedToolProvider(
            app_names=app_names or None,
            agent_id=agent_id,
            get_include_by_app=(lambda: (include_by_app, 0)) if include_by_app else None,
        )
        await tool_provider.initialize()
        return tool_provider

    return None


def _model_config_from_stored_llm(llm_cfg: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Translate manage-UI ``llm`` ({provider, model, ...}) into YAML ``model`` shape."""
    if not isinstance(llm_cfg, dict):
        return None
    model_name = str(llm_cfg.get("model") or llm_cfg.get("model_name") or "").strip()
    if not model_name:
        return None
    translated = {k: v for k, v in llm_cfg.items() if k != "model" and v is not None}
    translated["provider"] = llm_cfg.get("provider") or "openai"
    translated["model_name"] = model_name
    return translated


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
