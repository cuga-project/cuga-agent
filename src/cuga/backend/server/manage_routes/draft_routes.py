"""Draft save and section PATCH endpoints."""

from typing import Optional

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from loguru import logger

from cuga.backend.server.manage_routes.router import router

from cuga.backend.server.manage_routes.apply import apply_llm_to_draft_state
from cuga.backend.server.manage_routes.helpers import (
    extract_agent_feature_overrides,
    load_and_patch_draft,
)


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
        state_to_update = getattr(request.app.state, "draftapp_state", None)
        logger.info("[DEBUG] Using draftapp_state for /manage/draft endpoint")

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

                    logger.info(f"Applied {result.get('count', 0)} policies to draft from saved config")
                except Exception as policy_err:
                    logger.warning(f"Failed to apply policies to draft from config: {policy_err}")
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

        # Apply LLM config to draft state only — never mutate os.environ here so the
        # published agent's LLM is not affected before an explicit publish action.
        if state_to_update:
            llm_cfg = (config or {}).get("llm") or {}
            apply_llm_to_draft_state(state_to_update, llm_cfg)

        # NOW rebuild the draft agent graph AFTER registry has been reloaded
        try:
            logger.info("[DEBUG] Rebuilding draft agent graph with new configuration...")

            draft_agent = getattr(state_to_update, "agent", None)
            if draft_agent:
                tp = getattr(draft_agent, "tool_provider", None)
                if tp is not None and hasattr(tp, "reset"):
                    tp.reset()
                overrides = extract_agent_feature_overrides(config or {})
                if overrides["enable_todos"] is not None:
                    draft_agent.enable_todos = overrides["enable_todos"]
                if overrides["reflection_enabled"] is not None:
                    draft_agent.reflection_enabled = overrides["reflection_enabled"]
                if overrides["shortlisting_tool_threshold"] is not None:
                    draft_agent.shortlisting_tool_threshold = overrides["shortlisting_tool_threshold"]
                if overrides["cuga_lite_max_steps"] is not None:
                    draft_agent.cuga_lite_max_steps = overrides["cuga_lite_max_steps"]
                if overrides["enable_filesystem_tools"] is not None:
                    draft_agent.enable_filesystem_tools = overrides["enable_filesystem_tools"]
                llm_cfg = (config or {}).get("llm") or {}
                draft_agent.llm_config = llm_cfg if llm_cfg else None
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


@router.patch("/config/draft/llm")
async def patch_draft_llm(request: Request, agent_id: Optional[str] = None):
    """Update only the LLM section of the draft. No registry reload or agent rebuild."""
    if agent_id is None:
        agent_id = "cuga-default"
    try:
        data = await request.json()
        llm = data.get("llm", data)
        full_draft = await load_and_patch_draft(agent_id, "llm", llm if isinstance(llm, dict) else {})
        state = getattr(request.app.state, "draftapp_state", None)
        if state:
            apply_llm_to_draft_state(state, full_draft.get("llm") or {})
            draft_agent = getattr(state, "agent", None)
            if draft_agent:
                draft_agent.llm_config = full_draft.get("llm") or None
        return JSONResponse({"status": "success", "version": "draft", "agent_id": agent_id})
    except Exception as e:
        logger.error(f"Failed to patch draft LLM: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/config/draft/tools")
async def patch_draft_tools(request: Request, agent_id: Optional[str] = None):
    """Update only the tools section of the draft. Triggers registry reload and agent rebuild."""
    if agent_id is None:
        agent_id = "cuga-default"
    try:
        from cuga.backend.server.config_store import _parse_agent_id
        from cuga.backend.tools_env.registry.utils.api_utils import get_registry_base_url

        data = await request.json()
        tools = data.get("tools", data)
        tools_list = tools if isinstance(tools, list) else []
        full_draft = await load_and_patch_draft(agent_id, "tools", tools_list)
        state = getattr(request.app.state, "draftapp_state", None)
        if not state:
            return JSONResponse({"status": "success", "version": "draft", "agent_id": agent_id})

        state.tools_include_by_app = {
            t["name"]: t["include"]
            for t in tools_list
            if isinstance(t, dict)
            and t.get("name")
            and isinstance(t.get("include"), list)
            and len(t["include"]) > 0
        } or None
        current_version = getattr(state, "tools_include_version", 0)
        if isinstance(current_version, str):
            current_version = int(current_version) if current_version.isdigit() else 0
        state.tools_include_version = current_version + 1

        tool_errors = {}
        try:
            base_agent_id = _parse_agent_id(str(agent_id))
            draft_agent_id = f"{base_agent_id}--draft"
            registry_url = get_registry_base_url()
            async with httpx.AsyncClient() as client:
                r = await client.post(f"{registry_url}/reload?agent_id={draft_agent_id}", timeout=10.0)
                r.raise_for_status()
                reload_data = r.json()
                if reload_data.get("status") == "partial" and "errors" in reload_data:
                    tool_errors = reload_data["errors"]
        except Exception as reload_err:
            logger.warning(f"Failed to reload registry for PATCH tools: {reload_err}")

        apply_llm_to_draft_state(state, full_draft.get("llm") or {})
        try:
            draft_agent = getattr(state, "agent", None)
            if draft_agent:
                tp = getattr(draft_agent, "tool_provider", None)
                if tp is not None and hasattr(tp, "reset"):
                    tp.reset()
                overrides = extract_agent_feature_overrides(full_draft)
                if overrides["enable_todos"] is not None:
                    draft_agent.enable_todos = overrides["enable_todos"]
                if overrides["reflection_enabled"] is not None:
                    draft_agent.reflection_enabled = overrides["reflection_enabled"]
                if overrides["shortlisting_tool_threshold"] is not None:
                    draft_agent.shortlisting_tool_threshold = overrides["shortlisting_tool_threshold"]
                if overrides["cuga_lite_max_steps"] is not None:
                    draft_agent.cuga_lite_max_steps = overrides["cuga_lite_max_steps"]
                if overrides["enable_filesystem_tools"] is not None:
                    draft_agent.enable_filesystem_tools = overrides["enable_filesystem_tools"]
                draft_agent.llm_config = full_draft.get("llm") or None
                await draft_agent.build_graph()
        except Exception as rebuild_err:
            logger.error(f"Failed to rebuild draft agent graph after PATCH tools: {rebuild_err}")

        response_data = {
            "status": "partial" if tool_errors else "success",
            "version": "draft",
            "agent_id": agent_id,
        }
        if tool_errors:
            response_data["tool_errors"] = tool_errors
        return JSONResponse(response_data)
    except Exception as e:
        logger.error(f"Failed to patch draft tools: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/config/draft/agent")
async def patch_draft_agent(request: Request, agent_id: Optional[str] = None):
    """Update only the agent (name, description) section of the draft."""
    if agent_id is None:
        agent_id = "cuga-default"
    try:
        data = await request.json()
        agent_meta = data.get("agent", data)
        if isinstance(agent_meta, dict):
            name = agent_meta.get("name")
            if not name or not str(name).strip():
                raise HTTPException(status_code=400, detail="Agent name is required")
            await load_and_patch_draft(agent_id, "agent", agent_meta)
        return JSONResponse({"status": "success", "version": "draft", "agent_id": agent_id})
    except Exception as e:
        logger.error(f"Failed to patch draft agent: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/config/draft/policies")
async def patch_draft_policies(request: Request, agent_id: Optional[str] = None):
    """Update only the policies section of the draft. No registry reload or agent rebuild."""
    if agent_id is None:
        agent_id = "cuga-default"
    try:
        data = await request.json()
        policies = data.get("policies", data)
        full_draft = await load_and_patch_draft(agent_id, "policies", policies)
        state = getattr(request.app.state, "draftapp_state", None)
        if state and state.policy_system and state.policy_system.storage:
            raw_policies = full_draft.get("policies")
            policies_list = (
                raw_policies.get("policies", [])
                if isinstance(raw_policies, dict) and "policies" in raw_policies
                else []
            )
            try:
                from cuga.backend.cuga_graph.policy.utils import apply_policies_data_to_storage
                from cuga.backend.cuga_graph.nodes.cuga_lite.providers.toolguard import (
                    invalidate_toolguard_provider,
                )

                await apply_policies_data_to_storage(
                    state.policy_system.storage,
                    policies_list,
                    clear_existing=True,
                    filesystem_sync=state.policy_filesystem_sync,
                )
                await state.policy_system.initialize()

                # Invalidate the ToolGuard runtime cache so the next tool call
                # re-initialises with the updated policy (e.g. guards_enabled toggle).
                draft_agent = getattr(state, "agent", None)
                if draft_agent:
                    tp = getattr(draft_agent, "tool_provider", None)
                    if tp is not None:
                        invalidate_toolguard_provider(tp)
            except Exception as policy_err:
                logger.warning(f"Failed to apply policies from PATCH: {policy_err}")
        return JSONResponse({"status": "success", "version": "draft", "agent_id": agent_id})
    except Exception as e:
        logger.error(f"Failed to patch draft policies: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/config/draft/special_instructions")
async def patch_draft_special_instructions(request: Request, agent_id: Optional[str] = None):
    """Persist special_instructions to draft config."""
    if agent_id is None:
        agent_id = "cuga-default"
    try:
        body = await request.json()
        value = body.get("special_instructions", "") or ""
        await load_and_patch_draft(agent_id, "special_instructions", value)
        draft_state = getattr(request.app.state, "draftapp_state", None)
        draft_agent = getattr(draft_state, "agent", None) if draft_state is not None else None
        if draft_agent is not None:
            draft_agent.special_instructions = value or None
        return JSONResponse({"status": "success", "version": "draft", "agent_id": agent_id})
    except Exception as e:
        logger.error(f"Failed to patch draft special_instructions: {e}")
        raise HTTPException(status_code=500, detail=str(e))
