"""Manage endpoints: draft config (auto-save) and publish (new version)."""

import os
from typing import Any, Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from loguru import logger

router = APIRouter(prefix="/api/manage", tags=["manage"])


def _app_state(request: Request):
    return getattr(request.app.state, "app_state", None)


def _merge_mcp_yaml_into_config(config: dict[str, Any]) -> None:
    from cuga.backend.server.managed_mcp import get_managed_mcp_path, read_managed_mcp_servers

    tools_list = config.get("tools") or []
    if not tools_list:
        return
    yaml_servers = read_managed_mcp_servers(get_managed_mcp_path())
    for t in tools_list:
        if (t.get("type") or "mcp").lower() != "mcp":
            continue
        if t.get("command"):
            continue
        name = t.get("name")
        if not name or name not in yaml_servers:
            continue
        existing = yaml_servers[name]
        if isinstance(existing, dict):
            for key in ("command", "args", "transport", "description", "env"):
                if key in existing and key not in t:
                    t[key] = existing[key]


async def _apply_published_config(app_state: Any, config: dict[str, Any]) -> None:
    from cuga.backend.server.managed_mcp import get_managed_mcp_path, write_managed_mcp_yaml
    from cuga.backend.tools_env.registry.utils.api_utils import get_registry_base_url

    tools_list = (config or {}).get("tools") or []
    app_state.tools_include_by_app = {
        t["name"]: t["include"]
        for t in tools_list
        if t.get("name") and isinstance(t.get("include"), list) and len(t["include"]) > 0
    } or None
    llm_cfg = (config or {}).get("llm") or {}
    if isinstance(llm_cfg, dict):
        if "model" in llm_cfg and llm_cfg["model"]:
            os.environ["MODEL_NAME"] = str(llm_cfg["model"])
        if "temperature" in llm_cfg and llm_cfg["temperature"] is not None:
            os.environ["MODEL_TEMPERATURE"] = str(llm_cfg["temperature"])
    raw_policies = (config or {}).get("policies")
    policies_list = (
        raw_policies.get("policies", [])
        if isinstance(raw_policies, dict) and "policies" in raw_policies
        else raw_policies
        if isinstance(raw_policies, list)
        else []
    )
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
            logger.info("Applied %s policies from saved config", len(policies_list))
        except Exception as policy_err:
            logger.warning("Failed to apply policies from config: %s", policy_err)
    if os.getenv("CUGA_MANAGER_MODE", "").lower() in ("true", "1", "yes", "on"):
        try:
            write_managed_mcp_yaml(config, get_managed_mcp_path())
            registry_url = get_registry_base_url()
            async with httpx.AsyncClient() as client:
                r = await client.post(f"{registry_url}/reload", timeout=10.0)
                r.raise_for_status()
        except Exception as reload_err:
            logger.warning("Manager mode: write YAML/reload failed: %s", reload_err)


@router.get("/config")
async def get_manage_config(
    request: Request,
    version: Optional[str] = None,
    draft: Optional[str] = None,
    agent_id: Optional[str] = None,
):
    """Get config: ?draft=1 returns draft; ?version=N returns that version; ?agent_id=X for specific agent; else latest published."""
    try:
        from cuga.backend.server.config_store import load_config, load_draft

        # Determine agent_id from parameter or X-Use-Draft header (backward compatibility)
        if agent_id is None:
            agent_id = "cuga-default"
        use_draft = str(draft or "").lower() in ("1", "true", "yes", "on")
        if use_draft:
            config = load_draft(agent_id)
            if config is None:
                config, _ = load_config(None, agent_id)
            if config is None:
                return JSONResponse({"config": {}, "version": "draft", "agent_id": agent_id})
            _merge_mcp_yaml_into_config(config)
            return JSONResponse({"config": config, "version": "draft", "agent_id": agent_id})
        config, ver = load_config(version, agent_id)
        if config is None:
            return JSONResponse({"config": {}, "agent_id": agent_id})
        _merge_mcp_yaml_into_config(config)
        return JSONResponse({"config": config, "version": ver, "agent_id": agent_id})
    except Exception as e:
        logger.error(f"Failed to load manage config: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/config/draft")
async def save_manage_config_draft(request: Request, agent_id: Optional[str] = None):
    """Auto-save current form to draft (version stays 'draft'). Updates draft agent tools and triggers registry reload."""
    try:
        from cuga.backend.server.config_store import save_draft
        from cuga.backend.tools_env.registry.utils.api_utils import get_registry_base_url

        # Always use cuga-default as the base agent_id
        logger.info(
            f"[DEBUG] save_manage_config_draft called with agent_id={agent_id}, type={type(agent_id)}"
        )
        if agent_id is None:
            agent_id = "cuga-default"
        logger.info(f"[DEBUG] After default assignment: agent_id={agent_id}, type={type(agent_id)}")

        data = await request.json()
        logger.info(f"[DEBUG] Received data keys: {list(data.keys())}")
        config = data.get("config", data)
        logger.info(
            f"[DEBUG] Config type: {type(config)}, has tools: {'tools' in config if isinstance(config, dict) else 'N/A'}"
        )

        logger.info(f"[DEBUG] Calling save_draft with agent_id={agent_id}, type={type(agent_id)}")
        save_draft(config or {}, agent_id)
        logger.info("[DEBUG] save_draft completed successfully")

        # This is the /manage/draft endpoint, so always use draft state
        # The endpoint itself indicates draft mode, not the X-Use-Draft header
        state_to_update = getattr(request.app.state, "draft_app_state", None)
        logger.info("[DEBUG] Using draft_app_state for /manage/draft endpoint")

        logger.info(f"[DEBUG] state_to_update={state_to_update}, config is dict: {isinstance(config, dict)}")

        if state_to_update and config:
            tools_list = (config or {}).get("tools") or []
            logger.info(f"[DEBUG] tools_list length: {len(tools_list)}")

            state_to_update.tools_include_by_app = {
                t["name"]: t["include"]
                for t in tools_list
                if t.get("name") and isinstance(t.get("include"), list) and len(t["include"]) > 0
            } or None

            current_version = getattr(state_to_update, "tools_include_version", 0)
            logger.info(
                f"[DEBUG] current tools_include_version={current_version}, type={type(current_version)}"
            )
            # Ensure current_version is an integer before incrementing
            if isinstance(current_version, str):
                current_version = int(current_version) if current_version.isdigit() else 0
            state_to_update.tools_include_version = current_version + 1
            logger.info(f"[DEBUG] new tools_include_version={state_to_update.tools_include_version}")

        # Trigger registry reload for the agent
        try:
            from cuga.backend.server.config_store import _parse_agent_id

            # Use base agent_id for registry reload (without version suffix)
            logger.info(f"[DEBUG] Before _parse_agent_id: agent_id={agent_id}, type={type(agent_id)}")
            base_agent_id = _parse_agent_id(str(agent_id))
            logger.info(f"[DEBUG] After _parse_agent_id: base_agent_id={base_agent_id}")

            registry_url = get_registry_base_url()
            logger.info(f"[DEBUG] registry_url={registry_url}")

            reload_url = f"{registry_url}/reload?agent_id={base_agent_id}"
            logger.info(f"[DEBUG] reload_url={reload_url}")

            async with httpx.AsyncClient() as client:
                r = await client.post(reload_url, timeout=10.0)
                r.raise_for_status()
                logger.info(f"Registry reloaded for {base_agent_id} agent")
        except Exception as reload_err:
            logger.warning(f"Failed to reload registry for {str(agent_id)}: {reload_err}")
            logger.exception("[DEBUG] Full traceback:")

        logger.info(f"[DEBUG] Returning JSONResponse with agent_id={agent_id}, type={type(agent_id)}")
        return JSONResponse({"status": "success", "version": "draft", "agent_id": str(agent_id)})
    except Exception as e:
        logger.error(f"Failed to save draft: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/config")
async def save_manage_config_publish(request: Request, agent_id: Optional[str] = None):
    """Create new version from current config and apply to agent (live)."""
    # Determine agent_id from parameter or default to cuga-default
    if agent_id is None:
        agent_id = "cuga-default"

    app_state = _app_state(request)
    if app_state is None:
        raise HTTPException(status_code=500, detail="App state not available")
    try:
        from cuga.backend.server.config_store import save_config

        data = await request.json()
        config = data.get("config", data)
        ver = save_config(config or {}, agent_id)
        app_state.config_version = ver
        app_state.tools_include_version = int(ver) if ver else 0
        await _apply_published_config(app_state, config or {})
        return JSONResponse({"status": "success", "version": ver, "agent_id": agent_id})
    except Exception as e:
        logger.error(f"Failed to save manage config: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/config/history")
async def get_manage_config_history():
    """List published config versions (newest first)."""
    try:
        from cuga.backend.server.config_store import list_versions

        versions = list_versions()
        return JSONResponse({"versions": versions})
    except Exception as e:
        logger.error(f"Failed to list config history: {e}")
        raise HTTPException(status_code=500, detail=str(e))
