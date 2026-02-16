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
    version: Optional[int] = None,
    draft: Optional[str] = None,
):
    """Get config: ?draft=1 returns draft; ?version=N returns that version; else latest published."""
    try:
        from cuga.backend.server.config_store import load_config, load_draft

        use_draft = str(draft or "").lower() in ("1", "true", "yes", "on")
        if use_draft:
            config = load_draft()
            if config is None:
                config, _ = load_config(None)
            if config is None:
                return JSONResponse({"config": {}, "version": "draft"})
            _merge_mcp_yaml_into_config(config)
            return JSONResponse({"config": config, "version": "draft"})
        config, ver = load_config(version)
        if config is None:
            return JSONResponse({"config": {}})
        _merge_mcp_yaml_into_config(config)
        return JSONResponse({"config": config, "version": ver})
    except Exception as e:
        logger.error(f"Failed to load manage config: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/config/draft")
async def save_manage_config_draft(request: Request):
    """Auto-save current form to draft (version stays 'draft'). Updates draft agent tools."""
    try:
        from cuga.backend.server.config_store import save_draft

        data = await request.json()
        config = data.get("config", data)
        save_draft(config or {})
        draft_state = getattr(request.app.state, "draft_app_state", None)
        if draft_state and config:
            tools_list = (config or {}).get("tools") or []
            draft_state.tools_include_by_app = {
                t["name"]: t["include"]
                for t in tools_list
                if t.get("name") and isinstance(t.get("include"), list) and len(t["include"]) > 0
            } or None
            draft_state.tools_include_version = getattr(draft_state, "tools_include_version", 0) + 1
        return JSONResponse({"status": "success", "version": "draft"})
    except Exception as e:
        logger.error(f"Failed to save draft: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/config")
async def save_manage_config_publish(request: Request):
    """Create new version from current config and apply to agent (live)."""
    app_state = _app_state(request)
    if app_state is None:
        raise HTTPException(status_code=500, detail="App state not available")
    try:
        from cuga.backend.server.config_store import save_config

        data = await request.json()
        config = data.get("config", data)
        ver = save_config(config or {})
        app_state.config_version = ver
        app_state.tools_include_version = ver or 0
        await _apply_published_config(app_state, config or {})
        return JSONResponse({"status": "success", "version": ver})
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
