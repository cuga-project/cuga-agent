"""LLM model listing endpoint."""

from typing import Optional

import httpx
from fastapi import HTTPException, Query, Request
from loguru import logger

from cuga.backend.server.manage_routes.router import router

_PROVIDER_MODELS_URL = {
    "groq": "https://api.groq.com/openai/v1/models",
    "openai": "https://api.openai.com/v1/models",
    "litellm": None,
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
