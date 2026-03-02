"""Startup seeding: auto-seeds known LLM API key env vars into the DB secrets store."""

import asyncio
import os

from loguru import logger

SECRET_ENV_SEED_MAP: dict[str, str] = {
    "GROQ_API_KEY": "groq-api-key",
    "OPENAI_API_KEY": "openai-api-key",
    "ANTHROPIC_API_KEY": "anthropic-api-key",
    "GOOGLE_API_KEY": "google-api-key",
    "OPENROUTER_API_KEY": "openrouter-api-key",
    "RITS_API_KEY_RESTRICT": "rits-api-key",
    "WATSONX_APIKEY": "watsonx-api-key",
    "AZURE_OPENAI_API_KEY": "azure-openai-api-key",
    "LITELLM_API_KEY": "litellm-api-key",
}

_LLM_ENV_TO_SLUG = SECRET_ENV_SEED_MAP


def get_slug_for_env_var(env_var: str) -> str | None:
    return SECRET_ENV_SEED_MAP.get(env_var)


async def seed_secrets_from_env() -> None:
    """Seed known LLM API key env vars into the DB secrets store on startup.

    Runs once; skips any secret already present in the DB (no overwrite).
    Silently skips if CUGA_SECRET_KEY is not set (no DB backend configured).
    """
    from cuga.backend.storage.secrets_store import _fernet, get_secret, set_secret

    if not _fernet():
        logger.debug("secrets seed: CUGA_SECRET_KEY not set, skipping DB seed")
        return

    seeded = 0
    for env_var, slug in SECRET_ENV_SEED_MAP.items():
        value = os.environ.get(env_var)
        if not value:
            continue
        try:
            existing = await get_secret(slug)
            if existing is not None:
                logger.debug("secrets seed: '{}' already exists, skipping", slug)
                continue
            await set_secret(slug, value, description=f"Auto-seeded from {env_var}", created_by="system")
            logger.info("secrets seed: seeded '{}' from env var {}", slug, env_var)
            seeded += 1
        except Exception as e:
            logger.debug("secrets seed: failed to seed '{}': {}", slug, e)

    if seeded:
        logger.info("secrets seed: seeded {} secret(s) from environment", seeded)


def seed_secrets_from_env_sync() -> None:
    """Sync wrapper — runs in a new event loop if none is running."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(seed_secrets_from_env())
    except RuntimeError:
        asyncio.run(seed_secrets_from_env())


def resolve_llm_api_key_ref() -> str:
    """Return the db:// reference for the active LLM provider's API key, or ''."""
    model_name = os.environ.get("MODEL_NAME", "").lower()
    provider_hints = {
        "groq": "groq-api-key",
        "openai": "openai-api-key",
        "gpt": "openai-api-key",
        "anthropic": "anthropic-api-key",
        "claude": "anthropic-api-key",
        "google": "google-api-key",
        "gemini": "google-api-key",
        "openrouter": "openrouter-api-key",
        "rits": "rits-api-key",
        "watsonx": "watsonx-api-key",
        "azure": "azure-openai-api-key",
        "litellm": "litellm-api-key",
    }
    for hint, slug in provider_hints.items():
        if hint in model_name:
            return f"db://{slug}"
    for env_var, slug in SECRET_ENV_SEED_MAP.items():
        if os.environ.get(env_var):
            return f"db://{slug}"
    return ""
