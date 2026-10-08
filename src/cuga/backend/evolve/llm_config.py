"""Reuse the default published CUGA model in the private, bundled Evolve worker.

This is a startup snapshot, not per-user credential forwarding. Remote Evolve
services own their configuration and never receive these credentials.
"""

import os

from loguru import logger

_MODEL_OVERRIDES = (
    "EVOLVE_MODEL_NAME",
    "EVOLVE_GUIDELINES_MODEL",
    "EVOLVE_TIPS_MODEL",
    "EVOLVE_FACT_EXTRACTION_MODEL",
    "EVOLVE_CONFLICT_RESOLUTION_MODEL",
    "EVOLVE_CUSTOM_LLM_PROVIDER",
)


class UnsupportedProviderError(ValueError):
    """Provider cannot be shared through the bundled LiteLLM transport."""


def _secret(value):
    return value.get_secret_value() if hasattr(value, "get_secret_value") else value


def apply_model(model) -> None:
    """Translate an already-resolved CUGA client into private LiteLLM settings."""
    import litellm
    from langchain_openai import AzureChatOpenAI, ChatOpenAI

    headers = None
    version = None
    if isinstance(model, AzureChatOpenAI):
        name = model.deployment_name
        provider = "azure"
        base = model.azure_endpoint
        key = _secret(model.openai_api_key)
        version = model.openai_api_version
        if model.azure_ad_token or model.azure_ad_token_provider:
            raise UnsupportedProviderError(
                "Azure token authentication requires explicit Evolve configuration"
            )
        headers = model.default_headers
    elif isinstance(model, ChatOpenAI):
        name = model.model_name
        provider = "openai"
        base = model.openai_api_base
        key = _secret(model.openai_api_key)
        headers = model.default_headers
    else:
        from langchain_litellm import ChatLiteLLM

        if not isinstance(model, ChatLiteLLM):
            raise UnsupportedProviderError("This CUGA provider requires explicit Evolve configuration")
        name = model.model
        provider = model.custom_llm_provider
        base = model.api_base
        key = _secret(model.api_key)
        version = model.model_kwargs.get("api_version")
        headers = model.extra_headers
        if not provider:
            _, provider, _, _ = litellm.get_llm_provider(name)
    if not name:
        raise ValueError("CUGA model has no resolved model/deployment name")

    # Globals are confined to this dedicated child process. No secrets are sent
    # through MCP, exposed to the browser, written to disk, or put on argv.
    if isinstance(model, (ChatOpenAI, AzureChatOpenAI)):
        litellm.client_session = model.http_client
        litellm.aclient_session = model.http_async_client
    litellm.api_key = key
    litellm.api_base = str(base) if base else None
    litellm.api_version = version
    litellm.headers = dict(headers) if headers else None
    for variable in _MODEL_OVERRIDES[:5]:
        os.environ[variable] = name
    if provider:
        os.environ["EVOLVE_CUSTOM_LLM_PROVIDER"] = provider


async def configure_bundled_llm() -> None:
    """Resolve secrets using CUGA's normal published-config/TOML path once."""
    if os.environ.get("CUGA_EVOLVE_INHERIT_LLM", "true").lower() == "false":
        return
    if any(os.environ.get(name) for name in _MODEL_OVERRIDES):
        return

    from cuga.backend.llm.models import LLMManager, create_llm_from_config
    from cuga.backend.server.config_store import load_config
    from cuga.config import settings

    try:
        config, _ = await load_config(None)
        llm_config = (config or {}).get("llm") or {}
        model = (
            create_llm_from_config(llm_config)
            if llm_config
            else LLMManager().get_model(settings.agent.code.model)
        )
        apply_model(model)
    except UnsupportedProviderError:
        logger.warning(
            "CUGA model transport cannot be inherited by bundled Evolve; "
            "configure Evolve's model/provider explicitly"
        )
        return
    except Exception:
        # Provider exceptions can contain secrets or credential-bearing URLs.
        logger.warning(
            "Could not inherit CUGA's model connection; bundled Evolve will use its own configuration"
        )
        return
    logger.info("Bundled Evolve will use the default CUGA agent's resolved model connection")
