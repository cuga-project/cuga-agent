"""Manage endpoints: draft config (auto-save) and publish (new version)."""

import os
from collections.abc import Mapping
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
        "enable_filesystem_tools": None,
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
    out["enable_filesystem_tools"] = (
        feature_flags.get("enable_filesystem_tools")
        if "enable_filesystem_tools" in feature_flags
        else advanced.get("enable_filesystem_tools")
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


def _merge_feature_flags_defaults(config: dict[str, Any]) -> None:
    """Fill config.feature_flags from stored advanced_features, then global settings.

    Manage stores agent overrides under ``advanced_features`` (e.g. demo_manage_setup
    sets enable_filesystem_tools there only). The UI reads ``feature_flags``. When a
    key is missing from feature_flags, prefer the persisted advanced_features dict
    over global settings so per-agent demo/publish state is reflected correctly.
    """
    from cuga.config import settings

    ff = config.setdefault("feature_flags", {})
    raw_adv = config.get("advanced_features")
    stored_adv: dict[str, Any] = raw_adv if isinstance(raw_adv, dict) else {}
    af = getattr(settings, "advanced_features", None)

    def _bool(v: Any) -> bool:
        return bool(v)

    if "enable_todos" not in ff:
        if "enable_todos" in stored_adv:
            ff["enable_todos"] = _bool(stored_adv["enable_todos"])
        elif af:
            ff["enable_todos"] = getattr(af, "enable_todos", False)
        else:
            ff["enable_todos"] = False
    if "reflection" not in ff:
        if "reflection" in stored_adv:
            ff["reflection"] = _bool(stored_adv["reflection"])
        elif "reflection_enabled" in stored_adv:
            ff["reflection"] = _bool(stored_adv["reflection_enabled"])
        elif af:
            ff["reflection"] = getattr(af, "reflection_enabled", False)
        else:
            ff["reflection"] = False
    if "enable_filesystem_tools" not in ff:
        if "enable_filesystem_tools" in stored_adv:
            ff["enable_filesystem_tools"] = _bool(stored_adv["enable_filesystem_tools"])
        elif af:
            ff["enable_filesystem_tools"] = getattr(af, "enable_filesystem_tools", False)
        else:
            ff["enable_filesystem_tools"] = False
    if "shortlisting_tool_threshold" not in ff:
        if "shortlisting_tool_threshold" in stored_adv:
            v = stored_adv["shortlisting_tool_threshold"]
            ff["shortlisting_tool_threshold"] = int(v) if v is not None else 35
        elif af:
            ff["shortlisting_tool_threshold"] = getattr(af, "shortlisting_tool_threshold", 35)
        else:
            ff["shortlisting_tool_threshold"] = 35
    if "max_steps" not in ff:
        if "max_steps" in stored_adv:
            v = stored_adv["max_steps"]
            ff["max_steps"] = int(v) if v is not None else 70
        elif "cuga_lite_max_steps" in stored_adv:
            v = stored_adv["cuga_lite_max_steps"]
            ff["max_steps"] = int(v) if v is not None else 70
        elif af:
            ff["max_steps"] = getattr(af, "cuga_lite_max_steps", 70)
        else:
            ff["max_steps"] = 70
    if "builtin_tools" not in ff:
        if "builtin_tools" in stored_adv:
            bt = stored_adv["builtin_tools"]
            ff["builtin_tools"] = list(bt) if isinstance(bt, (list, tuple)) else bt
        elif af:
            ff["builtin_tools"] = list(getattr(af, "builtin_tools", ["knowledge"]))
        else:
            ff["builtin_tools"] = ["knowledge"]


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
            logger.debug(f"Failed to get secrets settings: {_e}")
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
    special_instructions = (config or {}).get("special_instructions") or None
    agent = getattr(app_state, "agent", None)
    if agent is not None:
        agent.special_instructions = special_instructions

    raw_policies = (config or {}).get("policies")
    policies_list = (
        raw_policies.get("policies", [])
        if isinstance(raw_policies, dict) and "policies" in raw_policies
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
        logger.debug(f"Failed to get secrets settings: {_e}")
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
        logger.warning(f"Failed to create LLM for draft state: {_e}")
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
            _merge_feature_flags_defaults(config)
            return JSONResponse({"config": config, "version": "draft", "agent_id": agent_id})
        config, ver = await load_config(version, agent_id)
        if config is None:
            return JSONResponse({"config": {}, "agent_id": agent_id})
        _merge_mcp_yaml_into_config(config)
        _merge_feature_flags_defaults(config)
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
            logger.warning(f"Failed to reload registry for PATCH tools: {reload_err}")

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
            await _load_and_patch_draft(agent_id, "agent", agent_meta)
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
        full_draft = await _load_and_patch_draft(agent_id, "policies", policies)
        state = getattr(request.app.state, "draft_app_state", None)
        if state and state.policy_system and state.policy_system.storage:
            raw_policies = full_draft.get("policies")
            policies_list = (
                raw_policies.get("policies", [])
                if isinstance(raw_policies, dict) and "policies" in raw_policies
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
                logger.warning(f"Failed to apply policies from PATCH: {policy_err}")
        return JSONResponse({"status": "success", "version": "draft", "agent_id": agent_id})
    except Exception as e:
        logger.error(f"Failed to patch draft policies: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/knowledge/test_embeddings")
async def test_embeddings_connection(request: Request):
    """Round-trip a single embed call to validate connectivity + auth + model.

    Body: { provider, model, api_key, base_url, extra_params }
    Returns: { ok: bool, dim?: int, latency_ms?: int, error?: str, error_class?: str }

    Used by the UI's 'Test connection' button — surfaces failures BEFORE save,
    rather than 30s into an ingest. Times out at 10s.
    """
    import asyncio as _asyncio
    import time as _time

    body = await request.json()
    provider = (body.get("provider") or "").strip()
    model = (body.get("model") or "").strip()
    api_key = (body.get("api_key") or "").strip()
    base_url = (body.get("base_url") or "").strip()
    extra_params = body.get("extra_params") or {}
    if not provider:
        raise HTTPException(status_code=400, detail="provider is required")

    from cuga.backend.knowledge.config import KnowledgeConfig
    from cuga.backend.knowledge.engine import create_embeddings
    from pathlib import Path
    import tempfile

    # Build a throwaway config; reuse the same factory the live engine uses
    # so the test path matches reality.
    try:
        cfg = KnowledgeConfig(
            enabled=True,
            persist_dir=Path(tempfile.mkdtemp(prefix="cuga-test-emb-")),
            embedding_provider=provider,
            embedding_model=model,
            embedding_api_key=api_key,
            embedding_base_url=base_url,
            embedding_extra_params=dict(extra_params),
        )
        cfg.validate()
    except (ValueError, TypeError):
        logger.exception("Knowledge embedding test config validation failed")
        return JSONResponse(
            {
                "ok": False,
                "error_class": "InvalidEmbeddingConfiguration",
                "error": "Invalid knowledge embedding configuration. Check provider, model, base URL, and extra parameters.",
            }
        )

    def _do_test() -> dict[str, Any]:
        t0 = _time.monotonic()
        try:
            emb = create_embeddings(cfg)
            vec = emb.embed_query("connection test")
            dt_ms = int((_time.monotonic() - t0) * 1000)
            return {"ok": True, "dim": len(vec), "latency_ms": dt_ms}
        except Exception:
            logger.exception("Knowledge embedding connection test failed")
            return {
                "ok": False,
                "error_class": "EmbeddingConnectionFailed",
                "error": "Embedding connection test failed. Check the base URL, model, and credentials.",
            }

    try:
        result = await _asyncio.wait_for(_asyncio.to_thread(_do_test), timeout=10.0)
        return JSONResponse(result)
    except _asyncio.TimeoutError:
        return JSONResponse(
            {
                "ok": False,
                "error_class": "Timeout",
                "error": "Embedding call did not complete within 10 seconds. "
                "Check the base URL is reachable and the model is correct.",
            }
        )


@router.get("/knowledge/defaults")
async def get_knowledge_defaults():
    """Return the factory-default knowledge config (dataclass defaults).

    Used by the UI's per-section "Reset to defaults" buttons. Returns the
    same shape that ``KnowledgeConfig().to_dict(include_secrets=False)``
    produces — secrets are blank, persist_dir is excluded. Crucially this is
    NOT the user's settings.toml view — it's the canonical project defaults,
    so reset semantics are predictable regardless of deployment overrides.
    """
    try:
        from cuga.backend.knowledge.config import KnowledgeConfig

        defaults = KnowledgeConfig().to_dict(include_secrets=False)
        # Drop internal fields (anything underscore-prefixed) so the UI
        # doesn't have to know about them.
        public = {k: v for k, v in defaults.items() if not k.startswith("_")}
        return JSONResponse({"defaults": public})
    except Exception as e:
        logger.error(f"Failed to compute knowledge defaults: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/knowledge/env-presets")
async def get_knowledge_env_presets():
    """Detected embedding-provider presets based on environment variables.

    Lets the UI offer "one-click apply" for providers whose credentials
    are already in the host's environment (``.env`` or shell). Returns
    ONLY booleans + suggested config — NEVER the actual env values, so
    the response is safe to surface in any logged-out or shared UI
    context. The "Apply" action on the UI side just sets
    embedding_provider + embedding_model and leaves embedding_api_key
    empty; the engine + LiteLLM then read the matching env var at
    embed-time.
    """
    import os as _os

    # Provider preset catalog. ``required_env`` controls the ready flag;
    # ``optional_env`` is exposed for completeness so the UI can show
    # "BASE_URL detected, will override default" hints.
    PROVIDER_PRESETS = [
        {
            "id": "openai",
            "label": "OpenAI",
            "required_env": ["OPENAI_API_KEY"],
            "optional_env": ["OPENAI_BASE_URL"],
            "default_provider": "openai",
            "default_model": "text-embedding-3-small",
        },
        {
            "id": "openrouter",
            "label": "OpenRouter",
            "required_env": ["OPENROUTER_API_KEY"],
            "optional_env": [],
            "default_provider": "openrouter",
            "default_model": "openai/text-embedding-3-small",
        },
        {
            "id": "watsonx",
            "label": "IBM Watsonx (via LiteLLM)",
            # LiteLLM accepts WATSONX_URL or WATSONX_API_BASE. We require
            # at least one of those two — surface both as detected when
            # either is present.
            "required_env": ["WATSONX_APIKEY", "WATSONX_PROJECT_ID"],
            "optional_env": ["WATSONX_URL", "WATSONX_API_BASE"],
            "default_provider": "litellm",
            "default_model": "watsonx/ibm/slate-30m-english-rtrvr",
        },
        {
            "id": "azure",
            "label": "Azure OpenAI (via LiteLLM)",
            "required_env": ["AZURE_API_KEY", "AZURE_API_BASE"],
            "optional_env": ["AZURE_API_VERSION"],
            "default_provider": "litellm",
            "default_model": "azure/text-embedding-3-small",
        },
        {
            "id": "cohere",
            "label": "Cohere (via LiteLLM)",
            "required_env": ["COHERE_API_KEY"],
            "optional_env": [],
            "default_provider": "litellm",
            "default_model": "cohere/embed-english-v3.0",
        },
    ]

    presets = []
    for p in PROVIDER_PRESETS:
        env_vars = {
            v: bool((_os.environ.get(v) or "").strip()) for v in (p["required_env"] + p["optional_env"])
        }
        # Watsonx is the lone provider with a "one-of-two" optional rule
        # (URL or API_BASE). Special-case the ready check: required keys
        # AND at least one of the URL aliases.
        if p["id"] == "watsonx":
            url_ok = env_vars.get("WATSONX_URL") or env_vars.get("WATSONX_API_BASE")
            ready = all(env_vars[v] for v in p["required_env"]) and bool(url_ok)
            missing = [v for v in p["required_env"] if not env_vars[v]]
            if not url_ok:
                missing.append("WATSONX_URL")
        else:
            ready = all(env_vars[v] for v in p["required_env"])
            missing = [v for v in p["required_env"] if not env_vars[v]]
        presets.append(
            {
                "id": p["id"],
                "label": p["label"],
                "default_provider": p["default_provider"],
                "default_model": p["default_model"],
                "ready": ready,
                "env_vars": env_vars,
                "missing": missing,
            }
        )

    # Local providers — no env detection needed; always exposed so the
    # UI can show them in the "always available" section.
    always_available = [
        {
            "id": "fastembed",
            "label": "Fastembed (local, default)",
            "default_provider": "fastembed",
            "default_model": "BAAI/bge-small-en-v1.5",
        },
        {
            "id": "ollama",
            "label": "Ollama (local)",
            "default_provider": "ollama",
            "default_model": "nomic-embed-text",
        },
    ]

    return JSONResponse({"presets": presets, "always_available": always_available})


@router.get("/knowledge/accelerator")
async def get_knowledge_accelerator(request: Request):
    """Live hardware acceleration status for the running knowledge engine.

    Returns what the user *requested* (use_gpu flag) alongside what the
    runtime *actually loaded* — so UI can flag silent CPU fallbacks.
    """
    try:
        app_state = getattr(request.app.state, "app_state", None)
        engine = getattr(app_state, "knowledge_engine", None) if app_state else None
        if engine is None:
            return JSONResponse(
                {"available": False, "reason": "knowledge engine not initialized"},
                status_code=200,
            )
        status = engine.accelerator_status()
        return JSONResponse({"available": True, **status})
    except Exception as e:
        logger.error(f"Failed to read accelerator status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/config/draft/knowledge")
async def patch_draft_knowledge(request: Request, agent_id: Optional[str] = None):
    """Update knowledge section of draft AND apply it to the live engine.

    Knowledge config differs from tools/LLM/policies in one important way: it
    affects how the SAME documents are parsed, embedded, and stored. There's
    no "preview" semantic where you'd want the draft and live to diverge — a
    user who sets ``docling_pdf_mode = "fast"`` expects the next upload to
    use fast mode, full stop.

    So we save to draft (for crash recovery + publish snapshot) AND apply
    to the live engine immediately. Cheap fields (docling mode, batch sizes,
    rag_profile) skip the preflight by design in
    ``KnowledgeEngine.prepare_knowledge_update``; only embedding
    provider/model changes pay the dim-probe cost.

    If the live apply fails (e.g. a bad embedding key fails preflight),
    we return 400 and DO NOT save the draft — the saved-but-not-applied
    case was the previous bug.
    """
    if agent_id is None:
        agent_id = "cuga-default"
    # NOTE: previously called ``await request.is_disconnected()`` here as
    # Slice A telemetry. That call invokes ``self._receive()`` to peek at
    # the ASGI channel, which can CONSUME the first body chunk and
    # leave ``await request.json()`` below blocked indefinitely waiting
    # for a chunk that's already been eaten. Symptom: PATCH hangs at
    # body.read until the client times out with ClientDisconnect.
    # Removed — Slice B (engine generation counter) doesn't need this
    # telemetry, and FastAPI surfaces real disconnects via the normal
    # exception path anyway.
    try:
        from cuga.backend.server.config_store import _parse_agent_id
        from cuga.backend.tools_env.registry.utils.api_utils import get_registry_base_url

        data = await request.json()
        knowledge = data.get("knowledge", data)
        if not isinstance(knowledge, dict):
            raise HTTPException(status_code=400, detail="knowledge must be a dict")

        # Merge with existing draft knowledge
        from cuga.backend.server.config_store import load_draft

        existing_draft = await load_draft(agent_id) or {}
        existing_knowledge = existing_draft.get("knowledge", {})
        merged = {**existing_knowledge, **knowledge}

        # Filter to known KnowledgeConfig fields only
        from cuga.backend.knowledge.config import KnowledgeConfig
        from dataclasses import fields as _dc_fields

        known_fields = {f.name for f in _dc_fields(KnowledgeConfig)} - {"persist_dir"}
        filtered = {k: v for k, v in merged.items() if k in known_fields}

        # Capture pre-patch adaptation + glossary hashes for diff-logging
        # (audit trail). Audit-finding B2: glossary changes used to be
        # invisible in audit logs — we now diff both hashes independently.
        from cuga.backend.knowledge.config import (
            ClientAdaptationError,
            client_adaptation_hash,
            client_glossary_hash,
        )

        _prev_adapt_hash = client_adaptation_hash(existing_knowledge.get("client_adaptation_text", ""))
        _prev_gloss_hash = client_glossary_hash(existing_knowledge.get("client_adaptation_glossary", []))

        # Validate via shared helper (same coercion + validation as engine apply).
        # ClientAdaptationError carries a machine-readable code + detail dict so
        # the UI can render specific affordances per failure mode (length /
        # bidi / control / phrase) — return 422 with structured body.
        try:
            validated = KnowledgeConfig.coerce_and_validate(filtered)
        except ClientAdaptationError as cae:
            raise HTTPException(status_code=422, detail=cae.to_dict())
        except (ValueError, TypeError) as ve:
            raise HTTPException(status_code=400, detail=str(ve))

        # Apply to the LIVE engine FIRST. If this fails (e.g. preflight
        # embed_query rejects a bad key/model), surface the error to the
        # user without saving a broken draft.
        live_state = getattr(request.app.state, "app_state", None)
        live_engine = getattr(live_state, "knowledge_engine", None) if live_state else None
        live_apply_result: dict[str, Any] | None = None
        if live_engine is not None:
            import asyncio as _asyncio_apply

            try:
                # ``apply_knowledge_config`` calls ``prepare_knowledge_update``
                # which, on embedding-provider/model change, runs a synchronous
                # ``embed_query("test")`` preflight — a network round-trip to
                # the embeddings API. Without ``to_thread`` that round-trip
                # blocks the event loop and stalls every other request for
                # the duration. Per Sami's review (Dec 2026).
                live_apply_result = await _asyncio_apply.to_thread(
                    live_engine.apply_knowledge_config, filtered
                )
                logger.info(
                    "Live engine knowledge config applied: changed=%s, reindex_recommended=%s",
                    {
                        k: live_apply_result.get(k)
                        for k in ("embedding_changed", "chunking_changed", "metric_changed")
                    },
                    live_apply_result.get("reindex_recommended"),
                )
            except (ValueError, TypeError) as ve:
                raise HTTPException(status_code=400, detail=f"Engine validation failed: {ve}")
            except Exception as live_err:
                # Preflight network/auth errors land here (e.g. embedding API rejected).
                # IMPORTANT: distinguish two cases:
                #   (a) USER-supplied a key that the provider rejected — they
                #       made an explicit choice and the choice is broken.
                #       Block save, surface error.
                #   (b) ENV-VAR fallback failed — the user didn't supply a key
                #       (just switched provider, or relying on server env). The
                #       failure is about deployment state, not the user's input.
                #       Soft-fail: save the config, return 200 with a warning
                #       so the UI can show a toast without blocking. The user
                #       can fix it by entering their own key or running Test
                #       connection.
                _user_supplied_key = bool((knowledge.get("embedding_api_key") or "").strip())
                _provider = (knowledge.get("embedding_provider") or "").lower()
                _is_credentialed = _provider in ("openai", "openrouter", "litellm")
                _err_str = str(live_err)
                _looks_like_auth = any(
                    s in _err_str
                    for s in ("401", "Unauthorized", "Invalid API", "Incorrect API", "AuthenticationError")
                )
                if _is_credentialed and not _user_supplied_key and _looks_like_auth:
                    logger.warning(
                        "Live engine preflight failed via env-var fallback (no user key supplied) — "
                        "soft-failing so the user can continue editing: %s",
                        live_err,
                    )
                    live_apply_result = {
                        "embedding_changed": False,
                        "chunking_changed": False,
                        "metric_changed": False,
                        "reindex_recommended": False,
                        "dim_changed": False,
                        "previous_dim": None,
                        "new_dim": None,
                        "_preflight_warning": (
                            "Environment-variable API key was rejected by the provider. "
                            "Settings saved, but ingest will fail until you set a valid key "
                            "or fix the env var. Use Test connection to verify."
                        ),
                    }
                    # Do NOT raise — fall through to save the draft so the user
                    # can keep editing without the toast-storm.
                else:
                    logger.warning(f"Live engine knowledge apply failed: {live_err}")
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            "Live knowledge engine rejected the new config. "
                            "Check the embedding provider/key/model and try again. "
                            f"Underlying error: {live_err}"
                        ),
                    )

        # Now save to draft (post-apply so we only persist configs that the
        # engine accepted). The draft serves crash recovery + publish snapshot.
        full_draft = await _load_and_patch_draft(agent_id, "knowledge", filtered)

        # Audit log: diff old vs new adaptation hash (NEVER the text itself —
        # PII + prompt-IP). Lets SREs answer "when did the adaptation change"
        # without snapshot-by-snapshot diffing.
        _new_adapt_hash = client_adaptation_hash(validated.client_adaptation_text)
        _new_gloss_hash = client_glossary_hash(validated.client_adaptation_glossary)
        if _prev_adapt_hash != _new_adapt_hash:
            logger.info(
                "cuga.knowledge.adaptation_patched",
                extra={
                    "cuga_knowledge_adaptation_old_hash": _prev_adapt_hash,
                    "cuga_knowledge_adaptation_new_hash": _new_adapt_hash,
                    "cuga_knowledge_adaptation_new_len": len(validated.client_adaptation_text),
                    "cuga_knowledge_agent_id": str(agent_id),
                    "cuga_knowledge_source": "patch_draft",
                },
            )
        if _prev_gloss_hash != _new_gloss_hash:
            logger.info(
                "cuga.knowledge.glossary_patched",
                extra={
                    "cuga_knowledge_glossary_old_hash": _prev_gloss_hash,
                    "cuga_knowledge_glossary_new_hash": _new_gloss_hash,
                    "cuga_knowledge_glossary_new_count": len(validated.client_adaptation_glossary),
                    "cuga_knowledge_agent_id": str(agent_id),
                    "cuga_knowledge_source": "patch_draft",
                },
            )

        # Store draft knowledge config so Try-It-Out can use it for search behavior.
        # Per-agent isolation: shared draft_app_state holds a DICT keyed by
        # base agent_id (stripped of any "--draft" suffix). The legacy
        # singular attribute is kept in sync for back-compat with any reader
        # that still expects it on the same instance.
        state = getattr(request.app.state, "draft_app_state", None)
        if state:
            try:
                base_agent_id_for_key = _parse_agent_id(str(agent_id))
                cfgs = getattr(state, "draft_knowledge_configs", None)
                if not isinstance(cfgs, dict):
                    cfgs = {}
                    state.draft_knowledge_configs = cfgs
                cfgs[base_agent_id_for_key] = validated
                # Back-compat shadow: keep singular attr writing the last patch
                # so any reader still using the singular name sees this agent's
                # data when it's the only configured agent.
                state.draft_knowledge_config = validated
            except Exception:
                pass
        if state:
            try:
                base_agent_id = _parse_agent_id(str(agent_id))
                draft_agent_id = f"{base_agent_id}--draft"
                registry_url = get_registry_base_url()
                async with httpx.AsyncClient() as client:
                    r = await client.post(f"{registry_url}/reload?agent_id={draft_agent_id}", timeout=10.0)
                    r.raise_for_status()
            except Exception as reload_err:
                logger.warning(f"Failed to reload registry for PATCH knowledge: {reload_err}")

            try:
                draft_agent = getattr(state, "agent", None)
                if draft_agent:
                    tp = getattr(draft_agent, "tool_provider", None)
                    if tp is not None and hasattr(tp, "reset"):
                        tp.reset()
                    llm_cfg = (full_draft or {}).get("llm") or {}
                    draft_agent.llm_config = llm_cfg if llm_cfg else None
                    await draft_agent.build_graph()
            except Exception as rebuild_err:
                logger.warning(f"Failed to rebuild draft agent graph after knowledge PATCH: {rebuild_err}")

        response: dict[str, Any] = {
            "status": "success",
            "version": "draft",
            "agent_id": agent_id,
            "live_applied": live_engine is not None,
        }
        if live_apply_result:
            # Expose the engine's change flags so the UI can show the
            # "reindex recommended" banner without an extra round-trip.
            response["live_changes"] = {
                k: live_apply_result.get(k)
                for k in (
                    "embedding_changed",
                    "chunking_changed",
                    "metric_changed",
                    "reindex_recommended",
                    "dim_changed",
                    "previous_dim",
                    "new_dim",
                )
            }
            # If the engine soft-failed (e.g. bad env-var key on provider
            # switch), surface the warning so the UI can toast it without
            # blocking the save.
            _pf_warn = live_apply_result.get("_preflight_warning")
            if _pf_warn:
                response["preflight_warning"] = _pf_warn
            # If the embedding dim actually changed, existing vectors are no
            # longer compatible — auto-trigger reindex of the requesting
            # agent's collections only. Scanning every directory under
            # files_dir would touch collections owned by other agents in
            # multi-tenant deployments, which is exactly the wrong scope
            # for a PATCH that targeted a single agent's draft.
            if live_apply_result.get("dim_changed") and live_engine is not None:
                triggered_collections: list[dict[str, Any]] = []
                try:
                    import re as _re

                    files_dir = getattr(live_engine, "_files_dir", None)
                    # Agent collections live under ``kb_agent_<sanitized_agent_id>``
                    # with an optional ``_<config_hash>`` suffix
                    # (see ``awareness._agent_collection_name``). Filter to
                    # that prefix so we only reindex what THIS agent owns.
                    sanitized = _re.sub(r"[^a-zA-Z0-9_]", "_", agent_id)
                    agent_prefix = f"kb_agent_{sanitized}"

                    # Auto-reindex MUST land docs under the new-hash collection
                    # name, not under the prior hash. Reindexing in place under
                    # the prior name re-embeds correctly but leaves the docs
                    # at the OLD collection name; publish then sees the
                    # new-hash collection empty and triggers a SECOND
                    # migration reindex (copy_source_files + reindex). This
                    # block migrates once: pick a non-empty source dir, copy
                    # its files to the target name, then reindex the target.
                    # Publish's migration path then short-circuits via the
                    # "target collection already has docs, skipping migration"
                    # branch in save_manage_config_publish.
                    try:
                        current_hash = live_engine._config.vector_config_hash()
                    except Exception:
                        current_hash = ""
                    target_collection = f"{agent_prefix}_{current_hash}" if current_hash else agent_prefix

                    if files_dir and files_dir.exists():
                        collections = [
                            d.name
                            for d in files_dir.iterdir()
                            if d.is_dir()
                            and (d.name == agent_prefix or d.name.startswith(f"{agent_prefix}_"))
                        ]
                    else:
                        collections = []

                    target_done = False
                    for coll in collections:
                        try:
                            if coll == target_collection:
                                # Already at the target name — reindex in place.
                                r = await live_engine.reindex(coll)
                                triggered_collections.append({"collection": coll, "result": r})
                                target_done = True
                            elif not target_done:
                                # Migrate this collection's files to the
                                # target name then reindex target. Only do
                                # this once per request — additional stale
                                # collection dirs become orphans (cleaned up
                                # via a separate housekeeping pass).
                                await live_engine.copy_source_files(coll, target_collection)
                                r = await live_engine.reindex(target_collection)
                                triggered_collections.append(
                                    {
                                        "collection": target_collection,
                                        "migrated_from": coll,
                                        "result": r,
                                    }
                                )
                                target_done = True
                            else:
                                # Orphan stale collection — leave for cleanup.
                                triggered_collections.append({"collection": coll, "skipped": "orphan_stale"})
                        except Exception as rerr:
                            logger.warning(f"Auto-reindex of {coll} failed: {rerr}")
                            triggered_collections.append({"collection": coll, "error": str(rerr)})

                    # Track the runtime collection-name hash so
                    # ``resolve_collection`` (and any caller that builds the
                    # agent collection name from ``app_state.knowledge_config_hash``)
                    # routes to the migrated data, not the orphan old-hash dir.
                    # Without this, search after a draft-only profile switch
                    # would hit the empty old collection because resolve_collection
                    # is keyed off this attribute.
                    if target_done and current_hash and live_state is not None:
                        try:
                            live_state.knowledge_config_hash = current_hash
                        except Exception as _hash_err:
                            logger.warning(
                                "Failed to update knowledge_config_hash after migrate: %s",
                                _hash_err,
                            )

                    response["auto_reindex"] = {
                        "triggered": True,
                        "scope": agent_prefix,
                        "target": target_collection,
                        "reason": f"embedding dim changed: {live_apply_result.get('previous_dim')} → {live_apply_result.get('new_dim')}",
                        "collections": triggered_collections,
                    }
                except Exception as auto_err:
                    logger.warning(f"Auto-reindex discovery failed: {auto_err}")
                    response["auto_reindex"] = {"triggered": False, "error": str(auto_err)}
        return JSONResponse(response)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to patch draft knowledge: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/config/draft/special_instructions")
async def patch_draft_special_instructions(request: Request, agent_id: Optional[str] = None):
    """Persist special_instructions to draft config."""
    if agent_id is None:
        agent_id = "cuga-default"
    try:
        body = await request.json()
        value = body.get("special_instructions", "") or ""
        await _load_and_patch_draft(agent_id, "special_instructions", value)
        draft_state = getattr(request.app.state, "draft_app_state", None)
        draft_agent = getattr(draft_state, "agent", None) if draft_state is not None else None
        if draft_agent is not None:
            draft_agent.special_instructions = value or None
        return JSONResponse({"status": "success", "version": "draft", "agent_id": agent_id})
    except Exception as e:
        logger.error(f"Failed to patch draft special_instructions: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/config")
async def save_manage_config_publish(request: Request, agent_id: Optional[str] = None):
    """Create new version from current config and apply to agent (live)."""
    # IMPORTANT: _apply_published_config is called at startup (main.py:488,582).
    # Do NOT add knowledge/reindex logic there. Knowledge apply belongs here only.
    if agent_id is None:
        agent_id = "cuga-default"

    app_state = _app_state(request)
    if app_state is None:
        raise HTTPException(status_code=500, detail="App state not available")

    data = await request.json()
    config = data.get("config", data) or {}
    agent_meta = config.get("agent")
    if not isinstance(agent_meta, dict) or not (agent_meta.get("name") or "").strip():
        return JSONResponse(
            status_code=400,
            content={"detail": "Agent name is required"},
        )

    try:
        from cuga.backend.server.config_store import (
            load_config,
            load_draft,
            save_config,
            save_draft,
            update_published_config_at_version,
        )

        data = await request.json()
        config = data.get("config", data)

        # Phase A: Validate + preflight knowledge (no mutation, no version save yet)
        prepared_knowledge = None
        knowledge_result = None
        knowledge_cfg = (config or {}).get("knowledge")
        engine = getattr(app_state, "knowledge_engine", None)

        # Ensure knowledge config is always present in published versions.
        # If missing, snapshot current engine config or use defaults.
        if not knowledge_cfg or not isinstance(knowledge_cfg, dict):
            from cuga.backend.knowledge.config import KnowledgeConfig as _KC

            knowledge_cfg = None
            if engine is not None:
                eng_attr = getattr(engine, "config", None)
                if eng_attr is not None:
                    to_dict = getattr(eng_attr, "to_dict", None)
                    if callable(to_dict):
                        # Publish snapshot — strip secrets so API keys do not
                        # cross machines via the snapshot store. Importing
                        # instances must supply their own keys.
                        try:
                            _snap = to_dict(include_secrets=False)
                        except TypeError:
                            _snap = to_dict()
                        if isinstance(_snap, dict):
                            knowledge_cfg = _snap
                    elif isinstance(eng_attr, dict):
                        knowledge_cfg = dict(eng_attr)
                if knowledge_cfg is None:
                    for _getter in ("get_config", "get_knowledge_config"):
                        _fn = getattr(engine, _getter, None)
                        if callable(_fn):
                            _cand = _fn()
                            if isinstance(_cand, dict):
                                knowledge_cfg = _cand
                                break
            if knowledge_cfg is None:
                knowledge_cfg = _KC().to_dict(include_secrets=False)
            if config is None:
                config = {}
            config["knowledge"] = knowledge_cfg

        if knowledge_cfg and isinstance(knowledge_cfg, dict):
            # Lazy engine creation: if knowledge was disabled at startup but user
            # enables it via Publish, create the engine now (full init with MCP + warmup).
            if not engine and knowledge_cfg.get("enabled", False):
                try:
                    from cuga.backend.knowledge.config import KnowledgeConfig

                    kb_config = KnowledgeConfig.coerce_and_validate(knowledge_cfg)
                    _initializer = getattr(app_state, "initialize_knowledge_engine", None)
                    if _initializer:
                        await _initializer(app_state, kb_config)
                        engine = app_state.knowledge_engine
                        logger.info("Knowledge engine created via publish (was disabled at startup)")
                    else:
                        logger.warning("Knowledge engine initializer not available")
                except Exception as e:
                    logger.warning(f"Failed to create knowledge engine on publish: {e}")
            if engine:
                try:
                    prepared_knowledge = engine.prepare_knowledge_update(knowledge_cfg)
                except (ValueError, TypeError) as ve:
                    raise HTTPException(status_code=400, detail=f"Invalid knowledge config: {ve}")

        # Vector-config hash: fields that affect stored vectors (embedding model,
        # chunk size, metric). When a file copy + reindex migration is required,
        # the persisted _vector_config_hash stays at the previous value until
        # that migration succeeds; see promotion after copy_source_files/reindex.
        from cuga.backend.knowledge.config import KnowledgeConfig as _KC
        from cuga.backend.knowledge.config import client_adaptation_hash as _cah
        from cuga.backend.knowledge.config import client_glossary_hash as _cgh

        _prev_hash = ""
        _prev_adapt_hash = ""
        _prev_gloss_hash = ""
        try:
            _prev_config, _ = await load_config(version=None, agent_id=agent_id)
            if _prev_config:
                _prev_hash = (_prev_config.get("knowledge") or {}).get("_vector_config_hash", "")
                _prev_adapt_hash = (_prev_config.get("knowledge") or {}).get("_adaptation_hash", "")
                _prev_gloss_hash = (_prev_config.get("knowledge") or {}).get("_glossary_hash", "")
        except Exception:
            pass

        try:
            _kb_obj = _KC.coerce_and_validate(knowledge_cfg)
            _vec_hash = _kb_obj.vector_config_hash()
        except Exception:
            _vec_hash = ""

        # Adaptation + glossary hashes for snapshot-level diffability.
        # NOT folded into _vector_config_hash on purpose (prompt edits don't
        # invalidate vectors). Stored as separate top-level metadata keys so
        # the UI can show "v3 → v4: adaptation changed / glossary changed"
        # without re-reading the bodies.
        _adapt_text = (
            knowledge_cfg.get("client_adaptation_text", "") if isinstance(knowledge_cfg, dict) else ""
        )
        _glossary_list = (
            knowledge_cfg.get("client_adaptation_glossary", []) if isinstance(knowledge_cfg, dict) else []
        )
        _new_adapt_hash = _cah(_adapt_text)
        _new_gloss_hash = _cgh(_glossary_list)
        if _prev_adapt_hash != _new_adapt_hash:
            logger.info(
                "cuga.knowledge.adaptation_published",
                extra={
                    "cuga_knowledge_adaptation_old_hash": _prev_adapt_hash,
                    "cuga_knowledge_adaptation_new_hash": _new_adapt_hash,
                    "cuga_knowledge_adaptation_new_len": len(_adapt_text),
                    "cuga_knowledge_agent_id": str(agent_id),
                    "cuga_knowledge_source": "publish",
                },
            )
        if _prev_gloss_hash != _new_gloss_hash:
            logger.info(
                "cuga.knowledge.glossary_published",
                extra={
                    "cuga_knowledge_glossary_old_hash": _prev_gloss_hash,
                    "cuga_knowledge_glossary_new_hash": _new_gloss_hash,
                    "cuga_knowledge_glossary_new_count": len(_glossary_list)
                    if isinstance(_glossary_list, list)
                    else 0,
                    "cuga_knowledge_agent_id": str(agent_id),
                    "cuga_knowledge_source": "publish",
                },
            )

        import re as _re_coll

        _san_id = _re_coll.sub(r"[^a-zA-Z0-9_]", "_", agent_id)
        _new_collection = f"kb_agent_{_san_id}_{_vec_hash}" if _vec_hash else f"kb_agent_{_san_id}"
        _old_collection = f"kb_agent_{_san_id}_{_prev_hash}" if _prev_hash else f"kb_agent_{_san_id}"

        _migration_precheck_done = False
        _defer_vector_hash_promotion = False
        _target_docs_precheck = None
        _old_docs_precheck = None

        if engine and _vec_hash and _prev_hash != _vec_hash:
            try:
                _target_docs_precheck = await engine.list_documents(_new_collection)
                _old_docs_precheck = await engine.list_documents(_old_collection)
                _migration_precheck_done = True
                if not _target_docs_precheck and _old_docs_precheck:
                    _defer_vector_hash_promotion = True
            except Exception as _pre_err:
                logger.warning(f"Vector migration precheck failed: {_pre_err}")

        if _vec_hash:
            if _defer_vector_hash_promotion:
                config["knowledge"]["_vector_config_hash"] = _prev_hash
            else:
                config["knowledge"]["_vector_config_hash"] = _vec_hash

        # Persist the adaptation + glossary hashes alongside the vector hash
        # so the version history can show "adaptation changed" / "glossary
        # changed" indicators without re-reading snapshot bodies. Underscore
        # prefix follows the existing pattern for snapshot-level metadata
        # keys (filtered out by coerce_and_validate's known_fields filter on
        # the next round-trip).
        config["knowledge"]["_adaptation_hash"] = _new_adapt_hash
        config["knowledge"]["_glossary_hash"] = _new_gloss_hash

        # Snapshot knowledge state for import/export portability.
        # Stores collection name, persist_dir, pinned collection config, and
        # document metadata (filenames + file paths) so an imported config can
        # reconnect to existing on-disk knowledge data without re-ingestion.
        if engine:
            import re as _re_snap

            _current_hash = (
                (_prev_hash if _defer_vector_hash_promotion else _vec_hash)
                or getattr(app_state, "knowledge_config_hash", "")
                or _prev_hash
            )
            _snap_id = _re_snap.sub(r"[^a-zA-Z0-9_]", "_", agent_id)
            _snap_col = f"kb_agent_{_snap_id}_{_current_hash}" if _current_hash else f"kb_agent_{_snap_id}"
            try:
                _state: dict[str, Any] = {
                    "collection": _snap_col,
                    "persist_dir": str(engine._config.persist_dir),
                }
                _col_cfg = await engine._metadata.get_collection_config(_snap_col)
                if _col_cfg and (isinstance(_col_cfg, dict) or isinstance(_col_cfg, Mapping)):
                    _state["collection_config"] = {
                        "embedding_provider": _col_cfg.get("embedding_provider", ""),
                        "embedding_model": _col_cfg.get("embedding_model", ""),
                        "embedding_dim": _col_cfg.get("embedding_dim"),
                    }
                _docs = await engine.list_documents(_snap_col)
                if _docs:
                    _files_dir = engine._files_dir / _snap_col
                    _state["documents"] = [
                        {
                            "filename": d.filename,
                            "file_path": str(_files_dir / d.filename),
                            "chunk_count": d.chunk_count,
                            "status": d.status,
                            "ingested_at": d.ingested_at,
                        }
                        for d in _docs
                    ]
                config["knowledge_state"] = _state
            except Exception as _snap_err:
                logger.warning(f"Failed to snapshot knowledge state: {_snap_err}")

        # Phase B: Save version + commit knowledge immediately after
        # Keep draft aligned with the just-published configuration because
        # Manage loads draft first on re-entry.
        await save_draft(config or {}, agent_id)
        # Strip secrets from the PUBLISHED snapshot only — the draft (saved
        # above) keeps the key so the local engine survives a restart.
        # Importing instances on other machines must supply their own keys.
        try:
            from cuga.backend.knowledge.config import KnowledgeConfig as _KC_strip

            published_config = dict(config or {})
            kb_pub = published_config.get("knowledge")
            if isinstance(kb_pub, dict):
                kb_pub = dict(kb_pub)
                for _secret in _KC_strip._SECRET_FIELDS:
                    if _secret in kb_pub and kb_pub[_secret]:
                        kb_pub[_secret] = ""
                published_config["knowledge"] = kb_pub
        except Exception:
            published_config = config or {}
        ver = await save_config(published_config, agent_id)
        app_state.config_version = ver
        app_state.tools_include_version = int(ver) if ver else 0

        # NOTE: knowledge_config_hash is updated AFTER migration completes (below)
        # to avoid a race where queries hit the new (empty) collection during migration.

        # Commit knowledge to runtime (pure in-memory, cannot fail from infra)
        if prepared_knowledge:
            knowledge_result = engine.commit_knowledge_update(prepared_knowledge)

        # Phase C: Apply LLM/tools/policies + rebuild (existing, best-effort)
        # Strip knowledge_state before runtime apply — it's for export/import only.
        config.pop("knowledge_state", None)
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
                if overrides["enable_filesystem_tools"] is not None:
                    prod_agent.enable_filesystem_tools = overrides["enable_filesystem_tools"]
                llm_cfg = (config or {}).get("llm") or {}
                prod_agent.llm_config = llm_cfg if llm_cfg else None
                await prod_agent.build_graph()
                logger.info("[DEBUG] Production agent graph rebuilt successfully")
            else:
                logger.warning("[DEBUG] No production agent found in state, skipping rebuild")

        except Exception as rebuild_err:
            logger.error(f"Failed to rebuild production agent graph: {rebuild_err}")
            logger.exception("[DEBUG] Full traceback:")

        response_data: dict[str, Any] = {"status": "success", "version": ver, "agent_id": agent_id}

        reindex_info = None

        if engine and _vec_hash and _prev_hash != _vec_hash:
            try:
                if (
                    _migration_precheck_done
                    and _target_docs_precheck is not None
                    and _old_docs_precheck is not None
                ):
                    target_docs = _target_docs_precheck
                else:
                    target_docs = await engine.list_documents(_new_collection)
                if target_docs:
                    logger.info(
                        "Knowledge config hash changed (%s -> %s) but target collection "
                        "already has %d doc(s), skipping migration",
                        _prev_hash or "(none)",
                        _vec_hash,
                        len(target_docs),
                    )
                else:
                    if _migration_precheck_done and _old_docs_precheck is not None:
                        old_docs = _old_docs_precheck
                    else:
                        old_docs = await engine.list_documents(_old_collection)
                    if old_docs:
                        logger.info(
                            "Knowledge config hash changed (%s -> %s), migrating %d doc(s) to new collection",
                            _prev_hash or "(none)",
                            _vec_hash,
                            len(old_docs),
                        )
                        try:
                            await engine.copy_source_files(_old_collection, _new_collection)
                            reindex_info = await engine.reindex(_new_collection)
                        except Exception as mig_err:
                            logger.warning(f"Document migration failed: {mig_err}")
                            reindex_info = {"status": "failed", "error": str(mig_err)}
            except Exception as e:
                logger.warning(f"Failed to check docs for migration: {e}")

        if _defer_vector_hash_promotion and ver:
            _promote_failed = reindex_info and reindex_info.get("status") == "failed"
            if not _promote_failed and reindex_info is not None:
                try:
                    pub_cfg, _ = await load_config(version=ver, agent_id=agent_id)
                    if pub_cfg and isinstance(pub_cfg.get("knowledge"), dict):
                        pub_cfg["knowledge"]["_vector_config_hash"] = _vec_hash
                        await update_published_config_at_version(pub_cfg, agent_id, ver)
                    draft_for_hash = await load_draft(agent_id)
                    if draft_for_hash and isinstance(draft_for_hash.get("knowledge"), dict):
                        draft_for_hash["knowledge"]["_vector_config_hash"] = _vec_hash
                        await save_draft(draft_for_hash, agent_id)
                except Exception as promo_err:
                    logger.warning(
                        "Migrated knowledge files but failed to persist new _vector_config_hash: %s",
                        promo_err,
                    )

        if not reindex_info and knowledge_result and knowledge_result.get("reindex_recommended") and engine:
            try:
                docs = await engine.list_documents(_new_collection)
                if docs:
                    try:
                        reindex_info = await engine.reindex(_new_collection)
                    except Exception as reindex_err:
                        from cuga.backend.knowledge.engine import ReindexBusyError

                        if isinstance(reindex_err, ReindexBusyError):
                            reindex_info = {
                                "status": "busy",
                                "detail": f"{reindex_err.pending_count} upload(s) in progress",
                                "pending_count": reindex_err.pending_count,
                            }
                            engine._reindex_deferred.add(_new_collection)
                        else:
                            logger.warning(f"Reindex failed: {reindex_err}")
            except Exception as e:
                logger.warning(f"Failed to check docs for reindex: {e}")

        if _vec_hash:
            if reindex_info and reindex_info.get("status") == "failed":
                logger.error(
                    "Keeping knowledge_config_hash=%r after vector-config migration/reindex failure "
                    "(would have switched to %s)",
                    getattr(app_state, "knowledge_config_hash", None),
                    _vec_hash,
                )
            else:
                app_state.knowledge_config_hash = _vec_hash

        if reindex_info:
            response_data["reindex"] = reindex_info

        return JSONResponse(response_data)
    except HTTPException:
        raise
    except Exception as e:
        from cuga.backend.knowledge.engine import EmbeddingModelLoadError

        if isinstance(e, EmbeddingModelLoadError):
            # A bad/just-switched embedding model (e.g. a large local model still
            # downloading) must not 500 — return an actionable 400 the UI can show.
            logger.warning(f"Embedding model load failed during publish: {e}")
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Couldn't load embedding model '{e.model}' ({e.provider}). If it's a large "
                    "local model it may still be downloading — retry in a moment. Otherwise check "
                    "the model name and your provider key / connectivity."
                ),
            )
        logger.error(f"Failed to save manage config: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/config/history")
async def get_manage_config_history(agent_id: Optional[str] = None):
    """List published config versions (newest first)."""
    if agent_id is None:
        agent_id = "cuga-default"
    try:
        from cuga.backend.server.config_store import list_versions

        versions = await list_versions(agent_id)
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
