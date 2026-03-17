"""Startup seeding: auto-seeds known LLM API key env vars into the DB secrets store."""

import asyncio
import os
import re

from loguru import logger

_STATIC_ENV_SEED_MAP: dict[str, str] = {
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

_DYNAMIC_PATTERN = re.compile(r"(?:^|_)(KEY|SECRET|TOKEN|PASSWORD|PASSWD|PWD)(?:$|_)", re.IGNORECASE)

# Internal prefixes to skip (avoid seeding internal/infra vars)
_SKIP_PREFIXES = (
    "CUGA_",
    "VAULT_",
    "AWS_SESSION_",
    "KUBERNETES_",
    "HOSTNAME",
    "PATH",
    "HOME",
    "USER",
    "SHELL",
    "LANG",
    "LC_",
)


def _env_var_to_slug(env_var: str) -> str:
    return env_var.lower().replace("_", "-")


def _build_dynamic_seed_map() -> dict[str, str]:
    extra: dict[str, str] = {}
    for env_var, value in os.environ.items():
        if not value:
            continue
        if env_var in _STATIC_ENV_SEED_MAP:
            continue
        if any(env_var.startswith(p) for p in _SKIP_PREFIXES):
            continue
        if _DYNAMIC_PATTERN.search(env_var):
            extra[env_var] = _env_var_to_slug(env_var)
    return extra


def _build_seed_map() -> dict[str, str]:
    return {**_STATIC_ENV_SEED_MAP, **_build_dynamic_seed_map()}


SECRET_ENV_SEED_MAP: dict[str, str] = _STATIC_ENV_SEED_MAP

_LLM_ENV_TO_SLUG = SECRET_ENV_SEED_MAP


def get_slug_for_env_var(env_var: str) -> str | None:
    return _build_seed_map().get(env_var)


def _secrets_mode() -> str:
    try:
        from cuga.config import settings

        sec = getattr(settings, "secrets", None)
        return getattr(sec, "mode", "local") if sec else "local"
    except Exception:
        return "local"


async def seed_secrets_from_env() -> None:
    """Seed env vars containing KEY/SECRET/TOKEN/PASSWORD into the DB secrets store on startup.

    Runs once; skips any secret already present in the DB (no overwrite).
    Never runs in vault mode — Vault is the source of truth there.
    Silently skips if CUGA_SECRET_KEY is not set (no DB backend configured).
    """
    from cuga.backend.storage.secrets_store import _fernet, get_secret, set_secret

    if _secrets_mode() == "vault":
        logger.debug("secrets seed: vault mode active, skipping DB seed entirely")
        return

    if not _fernet():
        logger.debug("secrets seed: CUGA_SECRET_KEY not set, skipping DB seed")
        return

    seed_map = _build_seed_map()
    seeded = 0
    for env_var, slug in seed_map.items():
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
    # Sort hints by length descending to match more specific providers first (e.g., "azure" before "gpt")
    for hint, slug in sorted(provider_hints.items(), key=lambda x: len(x[0]), reverse=True):
        if hint in model_name:
            return f"db://{slug}"
    for env_var, slug in _build_seed_map().items():
        if os.environ.get(env_var):
            return f"db://{slug}"
    return ""
