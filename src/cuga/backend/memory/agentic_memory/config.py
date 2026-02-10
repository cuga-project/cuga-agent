from typing import Any
from cuga.config import settings, DBS_DIR
from cuga.backend.llm.models import LLMManager

import os
from dynaconf import Dynaconf  # type: ignore

llm_manager = LLMManager()


def _sanitize_vector_store_config(raw: dict) -> dict:
    cfg = dict(raw or {})
    # Coerce embedding_model_dims to int when possible
    dims = cfg.get("embedding_model_dims")
    if isinstance(dims, str) and dims.isdigit():
        cfg["embedding_model_dims"] = int(dims)
    return cfg


def get_config(namespace_id: str | None = None):
    # Check if memory is enabled
    if not settings.advanced_features.enable_memory:
        raise RuntimeError(
            "Memory is disabled in settings. Set enable_memory = true in settings.toml to use memory features."
        )

    # Use cached Dynaconf instance to avoid duplication and key normalization issues
    # settings = _get_settings()

    # The memory.toml groups relevant settings under [mem0_config]
    root = dict[Any, Any](settings.get("mem0_config", {}) or {})

    # Defensive deep copies and sanitization of nested configs
    vs = root.get("vector_store", {})
    vs_cfg = _sanitize_vector_store_config((vs or {}).get("config", {}))
    # Default milvus url/path under DBS_DIR if not provided
    default_milvus = os.path.join(DBS_DIR, "milvus_memory.db")
    vs_url = os.environ.get('WXO_MILVUS_URI', vs_cfg.get("url", default_milvus))
    # If the configured url is a relative filesystem path, resolve it under DBS_DIR
    if isinstance(vs_url, str) and not (vs_url.startswith("http://") or vs_url.startswith("https://")):
        if not os.path.isabs(vs_url):
            vs_url = os.path.join(DBS_DIR, vs_url)
        # Ensure the parent directory exists for Milvus-lite local storage
        parent_dir = os.path.dirname(vs_url)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
    vs_cfg["url"] = vs_url
    vs_cfg["token"] = (
        f'{os.environ.get("MILVUS_USERNAME", "root")}:{os.environ.get("MILVUS_PASSWORD", "Milvus")}'
    )

    # Prefer explicit namespace_id, then env var, then file/default
    vs_cfg["collection_name"] = namespace_id or os.environ.get(
        "MILVUS_COLLECTION", vs_cfg.get("collection_name", "memory")
    )
    cfg: dict = {}
    cfg["vector_store"] = {
        "provider": (vs or {}).get("provider", "milvus"),
        "config": vs_cfg,
    }

    llm_model = llm_manager.get_model(settings.memory.mem0.model)

    # Keep llm config as-is (mem0 handles it), but ensure structure
    cfg["llm"] = {
        "provider": settings.memory.mem0.model.platform,
        "config": {
            "model": llm_model.model_name,
            "temperature": llm_model.temperature,
            "max_tokens": llm_model.max_tokens,
        },
    }

    if hasattr(llm_model, "platform"):
        cfg["llm"]["config"]["openai_base_url"] = llm_model.openai_api_base

    emb = root.get("embedder", {})
    emb_cfg = (emb or {}).get("config", {})
    cfg["embedder"] = {
        "provider": (emb or {}).get("provider", "huggingface"),
        "config": emb_cfg,
    }

    # Top-level defaults from [mem0_config]
    # Default history db under DBS_DIR if not provided; resolve relative paths under DBS_DIR
    history_path = root.get("history_db_path", os.path.join(DBS_DIR, "mem0_history.db"))
    if isinstance(history_path, str) and not os.path.isabs(history_path):
        history_path = os.path.join(DBS_DIR, history_path)
    cfg["history_db_path"] = history_path
    cfg["version"] = root.get("version", "v1.1")
    return cfg


def get_tips_extractor_config():
    tips_extractor_config = settings.tips_extractor_config
    tips_extractor_config_dict = tips_extractor_config.to_dict()
    tips_extractor_config_dict.update(settings.memory.tips_extractor.model.to_dict())
    return Dynaconf(settings_files=[], environments=False, tips_extractor_config=tips_extractor_config_dict)


def _resolve_milvus_config():
    """
    Resolve milvus configuration to handle nested model structures.

    Model-specific files (settings.openai.toml, etc.) define configs as:
    [memory.milvus.fact_extraction.model]

    This function flattens the structure so code can access:
    milvus_config.fact_extraction (instead of milvus_config.fact_extraction.model)
    """
    from dynaconf.utils.boxing import DynaBox

    # Get the base milvus config from settings.memory.milvus
    base_config = (
        settings.memory.milvus
        if hasattr(settings, 'memory') and hasattr(settings.memory, 'milvus')
        else settings.milvus
    )

    # Create a new config dict to hold the flattened structure
    resolved = {}

    # Copy over any direct attributes (like max_num_messages, embedding_model)
    for key, value in base_config.items():
        if isinstance(value, dict) and 'model' in value:
            # If the value has a 'model' sub-key, flatten it
            resolved[key] = value['model']
        else:
            # Otherwise, keep it as-is
            resolved[key] = value

    return DynaBox(resolved)


# Backward compatible module-level variables
<<<<<<< HEAD
# Only initialize if memory is enabled
if settings.advanced_features.enable_memory:
    config = get_config()
    milvus_config = settings.milvus
    tips_extractor_config = get_tips_extractor_config().tips_extractor_config
else:
    config = None
    milvus_config = None
    tips_extractor_config = None
=======
config = get_config()
milvus_config = _resolve_milvus_config()
tips_extractor_config = get_tips_extractor_config().tips_extractor_config
>>>>>>> a2578ba (Initial commit for personalization)
