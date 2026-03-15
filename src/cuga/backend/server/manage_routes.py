"""Manage endpoints: draft config (auto-save) and publish (new version)."""

import os
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from loguru import logger

from cuga.backend.server.auth import require_manage_access

router = APIRouter(
    prefix="/api/manage",
    tags=["manage"],
    dependencies=[Depends(require_manage_access)],
)


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
        try:
            from cuga.backend.llm.models import LLMManager, create_llm_from_config
            from cuga.config import settings

            _secrets = getattr(settings, "secrets", None)
            secrets_mode = getattr(_secrets, "mode", "local") or "local"
            force_env = bool(getattr(_secrets, "force_env", False))
        except Exception as _e:
            logger.debug("Failed to get secrets settings: %s", _e)
            secrets_mode = "local"
            force_env = False

        if force_env:
            if "model" in llm_cfg and llm_cfg["model"]:
                os.environ["MODEL_NAME"] = str(llm_cfg["model"])
            else:
                os.environ.pop("MODEL_NAME", None)
            if "temperature" in llm_cfg and llm_cfg["temperature"] is not None:
                os.environ["MODEL_TEMPERATURE"] = str(llm_cfg["temperature"])
            else:
                os.environ.pop("MODEL_TEMPERATURE", None)
            try:
                LLMManager()._models.clear()
            except Exception as _e:
                logger.debug("Failed to clear LLM cache (force_env): %s", _e)
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


def _apply_llm_to_state(state: Any, llm_cfg: dict) -> None:
    """Apply only LLM config to app state (current_llm, env vars). No tools or policies."""
    if not isinstance(llm_cfg, dict):
        return
    try:
        from cuga.backend.llm.models import LLMManager, create_llm_from_config
        from cuga.config import settings

        _secrets = getattr(settings, "secrets", None)
        force_env = bool(getattr(_secrets, "force_env", False))
    except Exception as _e:
        logger.debug("Failed to get secrets settings: %s", _e)
        force_env = False

    if force_env:
        if llm_cfg.get("model"):
            os.environ["MODEL_NAME"] = str(llm_cfg["model"])
        else:
            os.environ.pop("MODEL_NAME", None)
        if llm_cfg.get("temperature") is not None:
            os.environ["MODEL_TEMPERATURE"] = str(llm_cfg["temperature"])
        else:
            os.environ.pop("MODEL_TEMPERATURE", None)
        try:
            LLMManager()._models.clear()
        except Exception as _e:
            logger.debug("Failed to clear LLM cache (force_env): %s", _e)
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
            logger.warning("Failed to create LLM from PATCH: %s", _e)
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


def _apply_llm_to_draft_state(state: Any, llm_cfg: dict) -> None:
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
        logger.warning("Failed to create LLM for draft state: %s", _e)
        state.current_llm = None


async def _load_and_patch_draft(agent_id: str, section: str, value: Any) -> dict[str, Any]:
    from cuga.backend.server.config_store import load_draft, save_draft

    existing = await load_draft(agent_id) or {}
    existing[section] = value
    await save_draft(existing, agent_id)
    return existing


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


_PROVIDER_MODELS_URL = {
    "groq": "https://api.groq.com/openai/v1/models",
    "openai": "https://api.openai.com/v1/models",
    "litellm": None,  # LiteLLM uses OPENAI_BASE_URL from environment
}

_PROVIDER_API_KEY_REF = {
    "groq": "GROQ_API_KEY",
    "openai": "OPENAI_API_KEY",
    "litellm": "OPENAI_API_KEY",
}


@router.get("/llm/models")
async def list_llm_models(
    request: Request,
    disable_ssl: bool = Query(False, alias="disable_ssl"),
    agent_id: Optional[str] = None,
):
    """
    List available models for a provider.
    Always uses draft config. Provider is determined from config.
    Supports two modes:
    1. Vault mode (force_env=false): Uses config from saved draft config
    2. Local mode (force_env=true): Uses environment variables
    """
    from cuga.backend.server.config_store import load_draft
    from cuga.backend.secrets import resolve_secret
    from cuga.config import settings
    from cuga.backend.llm.config import LLMConfig
    from pydantic import ValidationError

    # Determine secrets mode
    try:
        _secrets = getattr(settings, "secrets", None)
        force_env = bool(getattr(_secrets, "force_env", False))
    except Exception:
        force_env = False

    # Load draft config to get LLM settings
    llm_cfg: Optional[LLMConfig] = None
    if not force_env:
        try:
            if agent_id is None:
                agent_id = "cuga-default"

            # Always use draft config
            config = await load_draft(agent_id)

            if config:
                llm_cfg_dict = config.get("llm") or {}

                # Parse config using Pydantic model
                try:
                    llm_cfg = LLMConfig(**llm_cfg_dict)
                    logger.info(
                        f"Parsed LLM config - provider: {llm_cfg.provider}, auth_type: {llm_cfg.auth_type}"
                    )
                except ValidationError as e:
                    logger.error(f"LLM config validation failed: {e}")
                    raise HTTPException(status_code=400, detail=f"Invalid LLM configuration: {e}")
        except HTTPException:
            raise
        except Exception as e:
            logger.debug(f"Failed to load config for LLM models: {e}")

    # Use default config if none loaded
    if llm_cfg is None:
        llm_cfg = LLMConfig()
        logger.info("Using default LLM config")

    # Extract values from Pydantic model
    provider_key = llm_cfg.provider.lower()
    auth_type = llm_cfg.auth_type
    base_url = llm_cfg.url
    disable_ssl_cfg = llm_cfg.disable_ssl
    auth_header_name = llm_cfg.auth_header_name

    logger.info(f"Using provider: {provider_key}, auth_type: {auth_type}")

    if provider_key not in _PROVIDER_MODELS_URL:
        raise HTTPException(
            status_code=400, detail=f"provider must be one of: groq, openai, litellm (got: {provider_key})"
        )

    # Get URL
    url = _PROVIDER_MODELS_URL[provider_key]
    if provider_key == "litellm":
        if not base_url:
            raise HTTPException(
                status_code=400,
                detail="LiteLLM requires url/base_url in config",
            )
        # Remove trailing /v1 if present to avoid double /v1/v1
        base_url = base_url.rstrip('/')
        if base_url.endswith('/v1'):
            url = f"{base_url}/models"
        else:
            url = f"{base_url}/v1/models"

    # Resolve the single api_key field (used for both auth modes)
    custom_auth_header = None
    api_key = None

    api_key_ref = llm_cfg.api_key
    if api_key_ref:
        if api_key_ref.startswith("vault://"):
            resolved = resolve_secret(api_key_ref)
            if resolved and not resolved.startswith("vault://"):
                api_key_ref = resolved
                logger.info("Resolved api_key from vault")
            else:
                logger.error(f"Failed to resolve api_key from vault: {api_key_ref}")
                api_key_ref = None
        # else: plain value, use as-is

    if auth_type == "auth_header":
        if api_key_ref:
            # When the header is Authorization and the value has no scheme prefix,
            # add Bearer so the raw token stored by the frontend works out of the box.
            _AUTH_SCHEMES = ("bearer ", "basic ", "token ", "digest ")
            if auth_header_name.lower() == "authorization" and not api_key_ref.lower().startswith(
                _AUTH_SCHEMES
            ):
                custom_auth_header = f"Bearer {api_key_ref}"
            else:
                custom_auth_header = api_key_ref
            logger.info("Using api_key as custom auth header value")
    else:
        if api_key_ref:
            api_key = api_key_ref
            logger.info("Using api_key as Bearer token")

        if not api_key:
            key_ref = _PROVIDER_API_KEY_REF.get(provider_key, "OPENAI_API_KEY")
            api_key = resolve_secret(key_ref)
            if api_key:
                logger.info("Using api_key from secrets manager")

    if not api_key and not custom_auth_header:
        logger.error(f"No authentication available for provider {provider_key}")
        raise HTTPException(
            status_code=400,
            detail="API key required: set X-LLM-API-Key header or configure in config/secrets",
        )

    try:
        headers = {}
        if custom_auth_header:
            headers[auth_header_name] = custom_auth_header
            # Log with masked auth header
            masked_auth = custom_auth_header[:10] + "***" if len(custom_auth_header) > 10 else "***"
            logger.info(f"LiteLLM models request - Provider: {provider_key}, URL: {url}, Auth: {masked_auth}")
        elif api_key:
            headers["Authorization"] = f"Bearer {api_key}"
            # Log with masked API key
            masked_key = api_key[:8] + "***" if len(api_key) > 8 else "***"
            logger.info(
                f"LiteLLM models request - Provider: {provider_key}, URL: {url}, Auth: Bearer {masked_key}"
            )
        else:
            logger.error("LiteLLM models request - No authentication available")

        # Use disable_ssl from config if not explicitly provided
        ssl_disabled = disable_ssl or disable_ssl_cfg
        logger.info(f"LiteLLM models request - SSL verification: {not ssl_disabled}")

        async with httpx.AsyncClient(verify=not ssl_disabled, timeout=10) as client:
            r = await client.get(url, headers=headers)
            r.raise_for_status()
            data = r.json().get("data", [])
        logger.info(f"LiteLLM models request successful - Found {len(data)} models")
        return {"models": sorted(m["id"] for m in data)}
    except httpx.HTTPStatusError as e:
        logger.error(f"LiteLLM models request failed with HTTP {e.response.status_code}: {e.response.text}")
        raise HTTPException(
            status_code=e.response.status_code, detail=f"Models fetch failed: {e.response.text}"
        )
    except Exception as ex:
        logger.exception(f"LiteLLM models request failed: {ex}")
        raise HTTPException(status_code=502, detail=f"Models fetch failed: {str(ex)}")


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

        # Apply LLM config to draft state only — never mutate os.environ here so the
        # published agent's LLM is not affected before an explicit publish action.
        if state_to_update:
            llm_cfg = (config or {}).get("llm") or {}
            _apply_llm_to_draft_state(state_to_update, llm_cfg)

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
        full_draft = await _load_and_patch_draft(agent_id, "llm", llm if isinstance(llm, dict) else {})
        state = getattr(request.app.state, "draft_app_state", None)
        if state:
            _apply_llm_to_draft_state(state, full_draft.get("llm") or {})
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
        full_draft = await _load_and_patch_draft(agent_id, "tools", tools_list)
        state = getattr(request.app.state, "draft_app_state", None)
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
            logger.warning("Failed to reload registry for PATCH tools: %s", reload_err)

        _apply_llm_to_draft_state(state, full_draft.get("llm") or {})
        try:
            draft_agent = getattr(state, "agent", None)
            if draft_agent:
                tp = getattr(draft_agent, "tool_provider", None)
                if tp is not None and hasattr(tp, "reset"):
                    tp.reset()
                overrides = _extract_agent_feature_overrides(full_draft)
                if overrides["enable_todos"] is not None:
                    draft_agent.enable_todos = overrides["enable_todos"]
                if overrides["reflection_enabled"] is not None:
                    draft_agent.reflection_enabled = overrides["reflection_enabled"]
                if overrides["shortlisting_tool_threshold"] is not None:
                    draft_agent.shortlisting_tool_threshold = overrides["shortlisting_tool_threshold"]
                if overrides["cuga_lite_max_steps"] is not None:
                    draft_agent.cuga_lite_max_steps = overrides["cuga_lite_max_steps"]
                draft_agent.llm_config = full_draft.get("llm") or None
                await draft_agent.build_graph()
        except Exception as rebuild_err:
            logger.error("Failed to rebuild draft agent graph after PATCH tools: %s", rebuild_err)

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


@router.patch("/config/draft/policies")
async def patch_draft_policies(request: Request, agent_id: Optional[str] = None):
    """Update only the policies section of the draft. No registry reload or agent rebuild."""
    if agent_id is None:
        agent_id = "cuga-default"
    try:
        data = await request.json()
        policies = data.get("policies", data)
        full_draft = await _load_and_patch_draft(agent_id, "policies", policies)
        state = getattr(request.app.state, "draft_app_state", None)
        if state and state.policy_system and state.policy_system.storage:
            raw_policies = full_draft.get("policies")
            policies_list = (
                raw_policies.get("policies", [])
                if isinstance(raw_policies, dict) and "policies" in raw_policies
                else raw_policies
                if isinstance(raw_policies, list)
                else []
            )
            try:
                from cuga.backend.cuga_graph.policy.utils import apply_policies_data_to_storage

                await apply_policies_data_to_storage(
                    state.policy_system.storage,
                    policies_list,
                    clear_existing=True,
                    filesystem_sync=state.policy_filesystem_sync,
                )
                await state.policy_system.initialize()
            except Exception as policy_err:
                logger.warning("Failed to apply policies from PATCH: %s", policy_err)
        return JSONResponse({"status": "success", "version": "draft", "agent_id": agent_id})
    except Exception as e:
        logger.error(f"Failed to patch draft policies: {e}")
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

        # Rebuild the production agent graph to pick up new tools + LLM config
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
                # Propagate published LLM config so build_graph uses the correct provider/model
                llm_cfg = (config or {}).get("llm") or {}
                prod_agent.llm_config = llm_cfg if llm_cfg else None
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
