from __future__ import annotations

import os
import threading
import time
from typing import Any

from loguru import logger

from .accelerator_serialization import serialized_local_accelerator


DEFAULT_MODEL_NAME = "Qwen/Qwen3-Embedding-0.6B"
_DEFAULT_BATCH_SIZE = 32
_DEFAULT_MAX_LENGTH = 512
_DEFAULT_DIMENSIONS = 512

_MODEL_LOCK = threading.RLock()
_INFERENCE_LOCK = threading.RLock()
_TOKENIZER: Any | None = None
_MODEL: Any | None = None
_TORCH: Any | None = None
_DEVICE: str | None = None
_LOADED_MODEL_NAME: str | None = None


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Invalid {}={!r}; using default {}", name, raw, default)
        return default
    if value < minimum:
        logger.warning("Out-of-range {}={!r}; using default {}", name, raw, default)
        return default
    return value


def retrieval_embeddings_enabled() -> bool:
    """Return whether automatic local retrieval embeddings are enabled.

    Embeddings are enabled by default in this archive. Set
    ``CUGA_RETRIEVAL_EMBEDDINGS=0`` (or ``off``/``false``/``no``) to keep the
    previous lexical/S-P-O-only retrieval behavior.
    """
    raw = os.getenv("CUGA_RETRIEVAL_EMBEDDINGS", "1").strip().casefold()
    return raw not in {"0", "false", "off", "no", "disabled"}


def embedding_model_name() -> str:
    return (
        os.getenv("CUGA_RETRIEVAL_EMBEDDING_MODEL", DEFAULT_MODEL_NAME).strip()
        or DEFAULT_MODEL_NAME
    )


def embedding_dimensions() -> int:
    return _env_int(
        "CUGA_RETRIEVAL_EMBEDDING_DIMENSIONS",
        _DEFAULT_DIMENSIONS,
        minimum=32,
    )


def _choose_device(torch: Any) -> str:
    requested = (
        os.getenv("CUGA_RETRIEVAL_EMBEDDING_DEVICE", "auto").strip().lower()
        or "auto"
    )
    if requested != "auto":
        return requested
    if torch.cuda.is_available():
        return "cuda"
    mps = getattr(torch.backends, "mps", None)
    if mps is not None and mps.is_available():
        return "mps"
    return "cpu"


def _get_model_bundle() -> tuple[Any, Any, Any, str]:
    global _TOKENIZER, _MODEL, _TORCH, _DEVICE, _LOADED_MODEL_NAME

    requested_model_name = embedding_model_name()
    if (
        _TOKENIZER is not None
        and _MODEL is not None
        and _TORCH is not None
        and _DEVICE is not None
        and _LOADED_MODEL_NAME == requested_model_name
    ):
        return _TOKENIZER, _MODEL, _TORCH, _DEVICE

    with _MODEL_LOCK:
        if (
            _TOKENIZER is not None
            and _MODEL is not None
            and _TORCH is not None
            and _DEVICE is not None
            and _LOADED_MODEL_NAME == requested_model_name
        ):
            return _TOKENIZER, _MODEL, _TORCH, _DEVICE

        try:
            import torch
            from transformers import AutoModel, AutoTokenizer
        except ImportError as exc:
            raise RuntimeError(
                "Local Qwen3 retrieval embeddings require torch and transformers. "
                "Install/upgrade with: uv add 'transformers>=4.51.0' torch"
            ) from exc

        device = _choose_device(torch)
        dtype = torch.float16 if device in {"mps", "cuda"} else torch.float32

        started = time.perf_counter()
        try:
            tokenizer = AutoTokenizer.from_pretrained(
                requested_model_name,
                padding_side="left",
            )
            model = AutoModel.from_pretrained(
                requested_model_name,
                torch_dtype=dtype,
            )
        except Exception as exc:
            raise RuntimeError(
                "Could not load local retrieval embedding model "
                f"{requested_model_name!r}. Qwen3 requires transformers>=4.51.0; "
                "ensure the model can be downloaded/cached."
            ) from exc

        with serialized_local_accelerator(device=device):
            model.to(device)
        model.eval()

        _TOKENIZER = tokenizer
        _MODEL = model
        _TORCH = torch
        _DEVICE = device
        _LOADED_MODEL_NAME = requested_model_name

        logger.info(
            "Local retrieval embedding model loaded: model={} device={} dtype={} "
            "load_seconds={:.3f}",
            requested_model_name,
            device,
            str(dtype).replace("torch.", ""),
            time.perf_counter() - started,
        )
        return tokenizer, model, torch, device


def _last_token_pool(
    last_hidden_state: Any,
    attention_mask: Any,
    torch: Any,
) -> Any:
    """Pool Qwen3 embeddings using the official last-token rule."""
    left_padded = bool(
        (attention_mask[:, -1].sum() == attention_mask.shape[0]).item()
    )
    if left_padded:
        return last_hidden_state[:, -1, :]

    sequence_lengths = attention_mask.sum(dim=1) - 1
    batch_size = last_hidden_state.shape[0]
    batch_indices = torch.arange(batch_size, device=last_hidden_state.device)
    return last_hidden_state[batch_indices, sequence_lengths]


def embed_retrieval_texts_qwen(
    texts: list[str],
) -> list[list[float]]:
    """Embed retrieval texts locally with Qwen3-Embedding-0.6B in batches.

    The function returns one independent vector per input string. Qwen3's
    official last-token pooling is used, then the Matryoshka vector is truncated
    to ``CUGA_RETRIEVAL_EMBEDDING_DIMENSIONS`` (512 by default) and L2-normalized.

    The model is loaded lazily once per process and inference is serialized so
    concurrent authority-graph builds do not duplicate the ~0.6B model or fight
    for Apple-Silicon unified memory.
    """
    if not texts:
        return []

    cleaned = [str(text).strip() for text in texts]
    if any(not text for text in cleaned):
        raise ValueError("Retrieval embedding texts must be non-empty")

    tokenizer, model, torch, device = _get_model_bundle()
    batch_size = _env_int(
        "CUGA_RETRIEVAL_EMBEDDING_BATCH_SIZE",
        _DEFAULT_BATCH_SIZE,
    )
    max_length = _env_int(
        "CUGA_RETRIEVAL_EMBEDDING_MAX_LENGTH",
        _DEFAULT_MAX_LENGTH,
        minimum=32,
    )
    dimensions = embedding_dimensions()

    vectors: list[list[float]] = []
    started = time.perf_counter()

    with _INFERENCE_LOCK:
        for start in range(0, len(cleaned), batch_size):
            batch = cleaned[start : start + batch_size]
            encoded = tokenizer(
                batch,
                padding=True,
                truncation=True,
                max_length=max_length,
                return_tensors="pt",
            )
            encoded = {key: value.to(device) for key, value in encoded.items()}

            with serialized_local_accelerator(device=device):
                with torch.inference_mode():
                    outputs = model(**encoded, use_cache=False)
                    pooled = _last_token_pool(
                        outputs.last_hidden_state,
                        encoded["attention_mask"],
                        torch,
                    )

                    hidden_width = int(pooled.shape[-1])
                    if dimensions > hidden_width:
                        raise RuntimeError(
                            "Requested retrieval embedding dimension exceeds model "
                            f"hidden width: requested={dimensions} hidden={hidden_width}"
                        )

                    # Qwen3-Embedding supports Matryoshka dimensions. Truncate before
                    # normalization so cosine similarity remains correct at the chosen
                    # representation width.
                    pooled = pooled[:, :dimensions]
                    pooled = torch.nn.functional.normalize(pooled, p=2, dim=1)
                    batch_vectors = pooled.detach().to("cpu", dtype=torch.float32).tolist()

            vectors.extend(batch_vectors)

    elapsed = time.perf_counter() - started
    logger.info(
        "Post-tree retrieval embedding extraction complete: model={} device={} "
        "texts={} dimensions={} batch_size={} max_length={} seconds={:.3f}",
        embedding_model_name(),
        device,
        len(cleaned),
        dimensions,
        batch_size,
        max_length,
        elapsed,
    )
    return vectors
