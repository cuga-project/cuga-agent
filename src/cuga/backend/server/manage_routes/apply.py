"""Apply published/draft config to runtime app state (LLM, tools, policies)."""

import os
from typing import Any

import httpx
from loguru import logger

from cuga.backend.server.manage_routes.draft_ops import rebuild_agent_from_config
from cuga.backend.server.manage_routes.helpers import policies_list_from_config


async def apply_published_config(app_state: Any, config: dict[str, Any]) -> None:
    from cuga.backend.tools_env.registry.utils.api_utils import get_registry_base_url

    tools_list = (config or {}).get("tools") or []
    app_state.tools_include_by_app = {
        t["name"]: t["include"]
        for t in tools_list
        if t.get("name") and isinstance(t.get("include"), list) and len(t["include"]) > 0
    } or None
    llm_cfg = (config or {}).get("llm") or {}
    if isinstance(llm_cfg, dict):
        try:
            from cuga.backend.llm.models import LLMManager, create_llm_from_config
            from cuga.config import settings

            _secrets = getattr(settings, "secrets", None)
            secrets_mode = getattr(_secrets, "mode", "local") or "local"
            force_env = bool(getattr(_secrets, "force_env", False))
        except Exception as _e:
            logger.debug(f"Failed to get secrets settings: {_e}")
            secrets_mode = "local"
            force_env = False

        if force_env:
            # Environment-owned deployments must not lose their configured model
            # when a roster/default config has no editor LLM block.
            try:
                LLMManager()._models.clear()
            except Exception as _e:
                logger.debug(f"Failed to clear LLM cache (force_env): {_e}")
            app_state.current_llm = None
        else:
            try:
                app_state.current_llm = create_llm_from_config(llm_cfg)
                logger.info(
                    "Applied LLM from config (mode=%s): provider=%s model=%s",
                    secrets_mode,
                    llm_cfg.get("provider"),
                    llm_cfg.get("model"),
                )
            except Exception as _e:
                logger.warning(
                    "Failed to create LLM from saved config (provider=%s model=%s): %s — "
                    "will use env/TOML settings at request time",
                    llm_cfg.get("provider"),
                    llm_cfg.get("model"),
                    _e,
                )
                app_state.current_llm = None
            if llm_cfg.get("model"):
                os.environ["MODEL_NAME"] = str(llm_cfg["model"])
            else:
                os.environ.pop("MODEL_NAME", None)
            if llm_cfg.get("temperature") is not None:
                os.environ["MODEL_TEMPERATURE"] = str(llm_cfg["temperature"])
            else:
                os.environ.pop("MODEL_TEMPERATURE", None)
            if llm_cfg.get("disable_ssl"):
                os.environ["CUGA_DISABLE_SSL"] = "true"
            else:
                os.environ.pop("CUGA_DISABLE_SSL", None)
    from cuga.backend.server.agent_registry import agent_special_instructions

    special_instructions = agent_special_instructions(config or {})
    agent = getattr(app_state, "agent", None)
    if agent is not None:
        agent.special_instructions = special_instructions

    raw_policies = (config or {}).get("policies")
    policies_list = policies_list_from_config(raw_policies)
    if raw_policies is not None and app_state.policy_system and app_state.policy_system.storage:
        try:
            from cuga.backend.cuga_graph.policy.utils import apply_policies_data_to_storage

            await apply_policies_data_to_storage(
                app_state.policy_system.storage,
                policies_list,
                clear_existing=True,
                filesystem_sync=app_state.policy_filesystem_sync,
            )
            await app_state.policy_system.initialize()
            logger.info(f"Applied {len(policies_list)} policies from saved config")
        except Exception as policy_err:
            logger.warning(f"Failed to apply policies from config: {policy_err}")
    if os.getenv("CUGA_MANAGER_MODE", "").lower() in ("true", "1", "yes", "on"):
        try:
            registry_url = get_registry_base_url()
            async with httpx.AsyncClient() as client:
                r = await client.post(f"{registry_url}/reload", timeout=10.0)
                r.raise_for_status()
        except Exception as reload_err:
            logger.warning(f"Manager mode: write YAML/reload failed: {reload_err}")


def apply_llm_to_state(state: Any, llm_cfg: dict) -> None:
    """Apply only LLM config to app state (current_llm, env vars). No tools or policies."""
    if not isinstance(llm_cfg, dict):
        return
    try:
        from cuga.backend.llm.models import LLMManager, create_llm_from_config
        from cuga.config import settings

        _secrets = getattr(settings, "secrets", None)
        force_env = bool(getattr(_secrets, "force_env", False))
    except Exception as _e:
        logger.debug(f"Failed to get secrets settings: {_e}")
        force_env = False

    if force_env:
        try:
            LLMManager()._models.clear()
        except Exception as _e:
            logger.debug(f"Failed to clear LLM cache (force_env): {_e}")
        state.current_llm = None
    else:
        try:
            state.current_llm = create_llm_from_config(llm_cfg)
            logger.info(
                "Applied LLM from PATCH (provider=%s model=%s)",
                llm_cfg.get("provider"),
                llm_cfg.get("model"),
            )
        except Exception as _e:
            logger.warning(f"Failed to create LLM from PATCH: {_e}")
            state.current_llm = None
        if llm_cfg.get("model"):
            os.environ["MODEL_NAME"] = str(llm_cfg["model"])
        else:
            os.environ.pop("MODEL_NAME", None)
        if llm_cfg.get("temperature") is not None:
            os.environ["MODEL_TEMPERATURE"] = str(llm_cfg["temperature"])
        else:
            os.environ.pop("MODEL_TEMPERATURE", None)
        if llm_cfg.get("disable_ssl"):
            os.environ["CUGA_DISABLE_SSL"] = "true"
        else:
            os.environ.pop("CUGA_DISABLE_SSL", None)


def apply_llm_to_draft_state(state: Any, llm_cfg: dict) -> None:
    """Apply LLM config to draft state only — no os.environ mutation.

    Unlike _apply_llm_to_state / _apply_published_config, this never touches
    os.environ so the published agent's LLM resolution is not affected.
    """
    if not isinstance(llm_cfg, dict):
        return
    try:
        from cuga.backend.llm.models import create_llm_from_config

        state.current_llm = create_llm_from_config(llm_cfg)
        logger.info(
            "Applied draft LLM to draft state (provider=%s model=%s)",
            llm_cfg.get("provider"),
            llm_cfg.get("model"),
        )
    except Exception as _e:
        logger.warning(f"Failed to create LLM for draft state: {_e}")
        state.current_llm = None


async def rebuild_production_agent(app_state: Any, config: dict[str, Any]) -> None:
    """Reset tool provider and rebuild the production agent graph from config."""
    prod_agent = getattr(app_state, "agent", None)
    if not prod_agent:
        logger.warning("No production agent found in state, skipping rebuild")
        return
    # Pass the shared saver so a publish keeps conversations (#774). None (lifespan
    # not run) falls back to a fresh MemorySaver, as before.
    await rebuild_agent_from_config(prod_agent, config, checkpointer=getattr(app_state, "checkpointer", None))
    prod_agent._checkpoint_config_version = str(getattr(app_state, "config_version", None) or 0)
    logger.info("Production agent graph rebuilt successfully")
