"""Shared embedding service for storage layer. Policy and other consumers use this."""

import asyncio
import os
from typing import Any, Callable, Dict, List, Optional

from loguru import logger

from cuga.config import settings

_embedding_model_cache: Dict[str, Any] = {}

LOCAL_MODEL_DIMS = {
    "all-MiniLM-L6-v2": 384,
    "all-mpnet-base-v2": 768,
    "BAAI/bge-small-en-v1.5": 384,
    "BAAI/bge-base-en-v1.5": 768,
    "BAAI/bge-large-en-v1.5": 1024,
}


def get_embedding_config() -> Dict[str, Any]:
    """Get embedding config from settings.storage.embedding."""
    emb = getattr(settings, "storage", None) and getattr(settings.storage, "embedding", None)
    if not emb:
        return {
            "provider": "local",
            "model": "all-MiniLM-L6-v2",
            "dim": 384,
            "base_url": None,
            "api_key": None,
        }
    return {
        "provider": getattr(emb, "provider", "local"),
        "model": getattr(emb, "model", "all-MiniLM-L6-v2"),
        "dim": getattr(emb, "dim", 384),
        "base_url": (getattr(emb, "base_url", None) or "").strip() or None,
        "api_key": (getattr(emb, "api_key", None) or "").strip() or None,
    }


def get_embedding_dimension(
    provider: str = "auto",
    model_name: Optional[str] = None,
    base_url: Optional[str] = None,
    configured_dim: Optional[int] = None,
) -> int:
    """Get embedding dimension for provider/model. Use configured_dim when base_url is set."""
    if base_url and configured_dim:
        return configured_dim
    if provider == "openai":
        return 1536
    if provider == "local":
        return LOCAL_MODEL_DIMS.get(model_name or "all-MiniLM-L6-v2", 384)
    return 1536


async def create_embedding_function(
    provider: Optional[str] = None,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    dim: Optional[int] = None,
) -> tuple[Optional[Callable[[str], Any]], int]:
    """
    Create async embedding function. Returns (embed_fn, dimension).
    All params override config from settings.storage.embedding.
    """
    cfg = get_embedding_config()
    p = provider or cfg["provider"]
    m = model or cfg["model"]
    bu = base_url if base_url is not None else cfg["base_url"]
    ak = api_key if api_key is not None else cfg["api_key"]
    d = dim or cfg["dim"]

    if p == "openai":
        fn = await _create_openai(bu, ak, m)
    elif p == "local":
        fn = await _create_local(m)
    else:
        fn = await _create_openai(bu, ak, m)
        if not fn:
            logger.info("OpenAI not available, trying local embedding model...")
            fn = await _create_local(m)

    if not fn:
        return None, d

    if bu:
        actual_dim = d
    else:
        actual_dim = get_embedding_dimension("local" if (p == "auto" or p == "local") else p, m, bu, d)
        if actual_dim != d:
            d = actual_dim

    return fn, d


async def _create_openai(
    base_url: Optional[str],
    api_key: Optional[str],
    model: str,
) -> Optional[Callable]:
    try:
        from langchain_openai import OpenAIEmbeddings

        key = api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            logger.warning("OPENAI_API_KEY or embedding api_key not found")
            return None

        kwargs: Dict[str, Any] = {"model": model or "text-embedding-3-small", "api_key": key}
        if base_url:
            kwargs["base_url"] = base_url.rstrip("/")

        embeddings = OpenAIEmbeddings(**kwargs)

        async def embed_text(text: str) -> List[float]:
            return await embeddings.aembed_query(text)

        base_info = f" (base_url={base_url})" if base_url else ""
        logger.info(f"✅ OpenAI embedding function created (model={model}{base_info})")
        return embed_text

    except ImportError:
        logger.warning("langchain_openai not installed, cannot create OpenAI embeddings")
        return None
    except Exception as e:
        logger.error(f"Failed to create OpenAI embedding function: {e}")
        return None


async def _create_local(model_name: str) -> Optional[Callable]:
    try:
        from sentence_transformers import SentenceTransformer

        cache_key = model_name
        if cache_key in _embedding_model_cache:
            logger.info(f"Using cached embedding model: {model_name}")
            model = _embedding_model_cache[cache_key]
        else:
            logger.info(f"Loading local embedding model: {model_name}")
            model = SentenceTransformer(model_name)
            _embedding_model_cache[cache_key] = model

        logger.info(f"✅ Local embedding model loaded (dim={model.get_sentence_embedding_dimension()})")

        async def embed_text(text: str) -> List[float]:
            loop = asyncio.get_event_loop()
            emb = await loop.run_in_executor(None, lambda: model.encode(text, convert_to_numpy=True))
            return emb.tolist()

        return embed_text

    except ImportError as e:
        logger.warning(f"sentence-transformers not installed: {e}")
        return None
    except Exception as e:
        logger.error(f"Failed to create local embedding function: {e}")
        return None
