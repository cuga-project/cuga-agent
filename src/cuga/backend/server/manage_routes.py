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


def _extract_agent_feature_overrides(config: dict[str, Any]) -> dict[str, bool | int | None]:
    """Extract enable_todos, reflection_enabled, shortlisting_tool_threshold, cuga_lite_max_steps from config.

    Frontend uses feature_flags.max_steps -> cuga_lite_max_steps.
    """
    out: dict[str, bool | int | None] = {
        "enable_todos": None,
        "reflection_enabled": None,
        "shortlisting_tool_threshold": None,
        "cuga_lite_max_steps": None,
    }
    feature_flags = config.get("feature_flags") or {}
    advanced = config.get("advanced_features") or {}
    out["enable_todos"] = (
        feature_flags.get("enable_todos") if "enable_todos" in feature_flags else advanced.get("enable_todos")
    )
    out["reflection_enabled"] = (
        feature_flags.get("reflection")
        if "reflection" in feature_flags
        else advanced.get("reflection_enabled")
    )
    val = (
        feature_flags.get("shortlisting_tool_threshold")
        if "shortlisting_tool_threshold" in feature_flags
        else advanced.get("shortlisting_tool_threshold")
    )
    out["shortlisting_tool_threshold"] = int(val) if val is not None else None
    max_steps_val = (
        feature_flags.get("max_steps")
        if "max_steps" in feature_flags
        else advanced.get("cuga_lite_max_steps")
    )
    out["cuga_lite_max_steps"] = int(max_steps_val) if max_steps_val is not None else None
    return out


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
            config = await load_draft(agent_id)
            if config is None:
                config, _ = await load_config(None, agent_id)
            if config is None:
                return JSONResponse({"config": {}, "version": "draft", "agent_id": agent_id})
            _merge_mcp_yaml_into_config(config)
            return JSONResponse({"config": config, "version": "draft", "agent_id": agent_id})
        config, ver = await load_config(version, agent_id)
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
        logger.info(
            f"[DEBUG] Config has policies: {'policies' in config if isinstance(config, dict) else 'N/A'}"
        )
        if isinstance(config, dict) and 'policies' in config:
            logger.info(f"[DEBUG] Policies in config: {config['policies']}")

        logger.info(f"[DEBUG] Calling save_draft with agent_id={agent_id}, type={type(agent_id)}")
        await save_draft(config or {}, agent_id)
        logger.info("[DEBUG] save_draft completed successfully")

        # This is the /manage/draft endpoint, so always use draft state
        # The endpoint itself indicates draft mode, not the X-Use-Draft header
        state_to_update = getattr(request.app.state, "draft_app_state", None)
        logger.info("[DEBUG] Using draft_app_state for /manage/draft endpoint")

        logger.info(f"[DEBUG] state_to_update={state_to_update}, config is dict: {isinstance(config, dict)}")

        # Initialize error tracking
        policy_errors = {}

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

            # Apply policies to draft state
            raw_policies = (config or {}).get("policies")
            policies_list = (
                raw_policies.get("policies", [])
                if isinstance(raw_policies, dict) and "policies" in raw_policies
                else raw_policies
                if isinstance(raw_policies, list)
                else []
            )
            if (
                raw_policies is not None
                and state_to_update.policy_system
                and state_to_update.policy_system.storage
            ):
                try:
                    from cuga.backend.cuga_graph.policy.utils import apply_policies_data_to_storage

                    logger.info(f"[DEBUG] Applying {len(policies_list)} policies to draft")
                    logger.info(f"[DEBUG] First policy data: {policies_list[0] if policies_list else 'None'}")

                    result = await apply_policies_data_to_storage(
                        state_to_update.policy_system.storage,
                        policies_list,
                        clear_existing=True,
                        filesystem_sync=state_to_update.policy_filesystem_sync,
                    )
                    logger.info(f"[DEBUG] apply_policies_data_to_storage returned: {result}")

                    await state_to_update.policy_system.initialize()
                    logger.info("[DEBUG] Policy system initialized")

                    # Verify policies were stored
                    stored_policies = await state_to_update.policy_system.storage.list_policies(
                        enabled_only=False
                    )
                    logger.info(f"[DEBUG] Total policies in storage after apply: {len(stored_policies)}")
                    for p in stored_policies:
                        # Handle different policy types - some may not have triggers attribute
                        triggers_info = ""
                        if hasattr(p, 'triggers'):
                            triggers_info = f", triggers={len(p.triggers)}, trigger_types={[type(t).__name__ for t in p.triggers]}"
                        logger.info(f"[DEBUG] Stored policy: id={p.id}, type={p.type}{triggers_info}")

                    # Check for policy errors
                    if result.get("errors"):
                        policy_errors = {"policy_errors": result["errors"]}
                        logger.warning(f"Policy application had {len(result['errors'])} errors")

                    logger.info("Applied %s policies to draft from saved config", result.get("count", 0))
                except Exception as policy_err:
                    logger.warning("Failed to apply policies to draft from config: %s", policy_err)
                    logger.exception("[DEBUG] Full policy error traceback:")
                    policy_errors = {"policy_errors": [str(policy_err)]}

        # Trigger registry reload for the agent FIRST (before rebuilding agent graph)
        tool_errors = {}
        try:
            from cuga.backend.server.config_store import _parse_agent_id

            # Use base agent_id for registry reload (without version suffix)
            logger.info(f"[DEBUG] Before _parse_agent_id: agent_id={agent_id}, type={type(agent_id)}")
            base_agent_id = _parse_agent_id(str(agent_id))
            logger.info(f"[DEBUG] After _parse_agent_id: base_agent_id={base_agent_id}")

            registry_url = get_registry_base_url()
            logger.info(f"[DEBUG] registry_url={registry_url}")

            # For draft, use the full draft agent_id including --draft suffix
            draft_agent_id = f"{base_agent_id}--draft"
            reload_url = f"{registry_url}/reload?agent_id={draft_agent_id}"
            logger.info(f"[DEBUG] reload_url={reload_url}")

            async with httpx.AsyncClient() as client:
                r = await client.post(reload_url, timeout=10.0)
                r.raise_for_status()
                reload_data = r.json()
                logger.info(f"Registry reloaded for {draft_agent_id} agent: {reload_data}")

                # Check if there were any tool initialization errors
                if reload_data.get("status") == "partial" and "errors" in reload_data:
                    tool_errors = reload_data["errors"]
                    logger.warning(f"Tool initialization errors: {tool_errors}")

        except Exception as reload_err:
            logger.warning(f"Failed to reload registry for {str(agent_id)}: {reload_err}")
            logger.exception("[DEBUG] Full traceback:")

        # NOW rebuild the draft agent graph AFTER registry has been reloaded
        try:
            logger.info("[DEBUG] Rebuilding draft agent graph with new configuration...")

            draft_agent = getattr(state_to_update, "agent", None)
            if draft_agent:
                tp = getattr(draft_agent, "tool_provider", None)
                if tp is not None and hasattr(tp, "reset"):
                    tp.reset()
                overrides = _extract_agent_feature_overrides(config or {})
                if overrides["enable_todos"] is not None:
                    draft_agent.enable_todos = overrides["enable_todos"]
                if overrides["reflection_enabled"] is not None:
                    draft_agent.reflection_enabled = overrides["reflection_enabled"]
                if overrides["shortlisting_tool_threshold"] is not None:
                    draft_agent.shortlisting_tool_threshold = overrides["shortlisting_tool_threshold"]
                if overrides["cuga_lite_max_steps"] is not None:
                    draft_agent.cuga_lite_max_steps = overrides["cuga_lite_max_steps"]
                await draft_agent.build_graph()
                logger.info("[DEBUG] Draft agent graph rebuilt successfully")
            else:
                logger.warning("[DEBUG] No draft agent found in state, skipping rebuild")

        except Exception as rebuild_err:
            logger.error(f"Failed to rebuild draft agent graph: {rebuild_err}")
            logger.exception("[DEBUG] Full traceback:")
            # Don't fail the request if rebuild fails, just log it

        logger.info(f"[DEBUG] Returning JSONResponse with agent_id={agent_id}, type={type(agent_id)}")

        # Return response with tool and policy errors if any
        has_errors = bool(tool_errors or policy_errors)
        response_data = {
            "status": "partial" if has_errors else "success",
            "version": "draft",
            "agent_id": str(agent_id),
        }

        error_messages = []
        if tool_errors:
            response_data["tool_errors"] = tool_errors
            error_messages.append(f"{len(tool_errors)} tool(s) failed to initialize")

        if policy_errors:
            response_data.update(policy_errors)
            if "policy_errors" in policy_errors:
                error_messages.append(f"{len(policy_errors['policy_errors'])} policy/policies failed to load")

        if error_messages:
            response_data["message"] = "; ".join(error_messages)

        return JSONResponse(response_data)
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
        ver = await save_config(config or {}, agent_id)
        app_state.config_version = ver
        app_state.tools_include_version = int(ver) if ver else 0
        await _apply_published_config(app_state, config or {})

        # Rebuild the production agent graph to pick up new tools from registry
        try:
            logger.info("[DEBUG] Rebuilding production agent graph with new configuration...")

            prod_agent = getattr(app_state, "agent", None)
            if prod_agent:
                tp = getattr(prod_agent, "tool_provider", None)
                if tp is not None and hasattr(tp, "reset"):
                    tp.reset()
                overrides = _extract_agent_feature_overrides(config or {})
                if overrides["enable_todos"] is not None:
                    prod_agent.enable_todos = overrides["enable_todos"]
                if overrides["reflection_enabled"] is not None:
                    prod_agent.reflection_enabled = overrides["reflection_enabled"]
                if overrides["shortlisting_tool_threshold"] is not None:
                    prod_agent.shortlisting_tool_threshold = overrides["shortlisting_tool_threshold"]
                if overrides["cuga_lite_max_steps"] is not None:
                    prod_agent.cuga_lite_max_steps = overrides["cuga_lite_max_steps"]
                await prod_agent.build_graph()
                logger.info("[DEBUG] Production agent graph rebuilt successfully")
            else:
                logger.warning("[DEBUG] No production agent found in state, skipping rebuild")

        except Exception as rebuild_err:
            logger.error(f"Failed to rebuild production agent graph: {rebuild_err}")
            logger.exception("[DEBUG] Full traceback:")
            # Don't fail the request if rebuild fails, just log it

        return JSONResponse({"status": "success", "version": ver, "agent_id": agent_id})
    except Exception as e:
        logger.error(f"Failed to save manage config: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/config/history")
async def get_manage_config_history():
    """List published config versions (newest first)."""
    try:
        from cuga.backend.server.config_store import list_versions

        versions = await list_versions()
        return JSONResponse({"versions": versions})
    except Exception as e:
        logger.error(f"Failed to list config history: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/config")
async def delete_manage_config(agent_id: Optional[str] = None, reset_db: Optional[bool] = None):
    """Delete all configs for agent, or reset entire config db. Use ?reset_db=1 for full reset."""
    try:
        from cuga.backend.server.config_store import delete_all_configs, reset_config_db

        if reset_db:
            reset_config_db()
            return JSONResponse({"status": "success", "message": "Config db reset"})
        aid = agent_id or "cuga-default"
        count = await delete_all_configs(aid)
        return JSONResponse({"status": "success", "deleted": count, "agent_id": aid})
    except Exception as e:
        logger.error(f"Failed to delete manage config: {e}")
        raise HTTPException(status_code=500, detail=str(e))
