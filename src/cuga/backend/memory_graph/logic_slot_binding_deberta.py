from __future__ import annotations

import os
import threading
from typing import Any

from loguru import logger

from .accelerator_serialization import serialized_local_accelerator
from .logging_utils import memory_graph_trace_enabled

from .schemas import (
    LogicSlotBindingDecision,
    LogicSlotBindingRequest,
    LogicSlotBindingResponse,
)


MATCHER_MODEL_NAME = os.getenv(
    "CUGA_LOGIC_MATCH_MODEL",
    "cross-encoder/nli-deberta-v3-small",
).strip() or "cross-encoder/nli-deberta-v3-small"

_DEFAULT_MATCH_THRESHOLD = 0.90
_DEFAULT_MATCH_MARGIN = 0.05
_DEFAULT_MAX_CONTEXT_PATHS = 2
_DEFAULT_CONTEXT_CHAR_BUDGET = 900
_DEFAULT_MAX_LENGTH = 512

_MODEL_LOCK = threading.RLock()
_INFERENCE_LOCK = threading.RLock()
_TOKENIZER: Any | None = None
_MODEL: Any | None = None
_TORCH: Any | None = None
_DEVICE: str | None = None
_LABEL_INDICES: tuple[int, int, int] | None = None


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("Invalid {}={!r}; using default {}", name, raw, default)
        return default
    if not 0.0 <= value <= 1.0:
        logger.warning("Out-of-range {}={!r}; using default {}", name, raw, default)
        return default
    return value


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


def _choose_device(torch: Any) -> str:
    requested = os.getenv("CUGA_LOGIC_MATCH_DEVICE", "auto").strip().lower() or "auto"
    if requested != "auto":
        return requested
    if torch.cuda.is_available():
        return "cuda"
    mps = getattr(torch.backends, "mps", None)
    if mps is not None and mps.is_available():
        return "mps"
    return "cpu"


def _resolve_label_indices(model: Any) -> tuple[int, int, int]:
    mapping = {
        str(label).strip().casefold(): int(index)
        for index, label in dict(model.config.id2label).items()
    }
    try:
        return (
            mapping["entailment"],
            mapping["contradiction"],
            mapping["neutral"],
        )
    except KeyError as exc:
        raise RuntimeError(
            "DeBERTa matcher model must expose entailment/contradiction/neutral "
            f"labels; got {mapping!r}"
        ) from exc


def _get_model_bundle() -> tuple[Any, Any, Any, str, tuple[int, int, int]]:
    global _TOKENIZER, _MODEL, _TORCH, _DEVICE, _LABEL_INDICES

    if (
        _TOKENIZER is not None
        and _MODEL is not None
        and _TORCH is not None
        and _DEVICE is not None
        and _LABEL_INDICES is not None
    ):
        return _TOKENIZER, _MODEL, _TORCH, _DEVICE, _LABEL_INDICES

    with _MODEL_LOCK:
        if (
            _TOKENIZER is not None
            and _MODEL is not None
            and _TORCH is not None
            and _DEVICE is not None
            and _LABEL_INDICES is not None
        ):
            return _TOKENIZER, _MODEL, _TORCH, _DEVICE, _LABEL_INDICES

        try:
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
        except ImportError as exc:
            raise RuntimeError(
                "Local DeBERTa logic-slot matching requires torch and transformers. "
                "Install with: uv add torch transformers sentencepiece"
            ) from exc

        device = _choose_device(torch)
        try:
            tokenizer = AutoTokenizer.from_pretrained(MATCHER_MODEL_NAME)
            model = AutoModelForSequenceClassification.from_pretrained(MATCHER_MODEL_NAME)
        except Exception as exc:
            raise RuntimeError(
                "Could not load local logic-slot matcher "
                f"{MATCHER_MODEL_NAME!r}. Ensure the model can be downloaded/cached "
                "and sentencepiece is installed."
            ) from exc

        with serialized_local_accelerator(device=device):
            model.to(device)
        model.eval()
        label_indices = _resolve_label_indices(model)

        _TOKENIZER = tokenizer
        _MODEL = model
        _TORCH = torch
        _DEVICE = device
        _LABEL_INDICES = label_indices

        logger.info(
            "Local logic-slot matcher loaded: model={} device={} labels={}",
            MATCHER_MODEL_NAME,
            device,
            dict(model.config.id2label),
        )
        return tokenizer, model, torch, device, label_indices


def _clean_context_paths(paths: list[list[str]], max_paths: int) -> list[list[str]]:
    cleaned: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for path in paths:
        values = [str(item).strip() for item in path if str(item).strip()]
        if not values:
            continue
        key = tuple(values)
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(values)
        if len(cleaned) >= max_paths:
            break
    return cleaned


def _contextual_text(
    proposition: str,
    context_paths: list[list[str]],
    *,
    max_paths: int,
    context_char_budget: int,
) -> str:
    """Build a compact occurrence-aware sentence-pair input for NLI.

    Context paths are broad -> near. When clipping is necessary we keep the tail,
    because the nearest parent normally carries pronoun/scope information.
    """
    proposition = str(proposition or "").strip()
    paths = _clean_context_paths(context_paths, max_paths)
    if not paths:
        return proposition

    per_path_budget = max(120, context_char_budget // max(1, len(paths)))
    rendered: list[str] = []
    for index, path in enumerate(paths, 1):
        text = " > ".join(path)
        if len(text) > per_path_budget:
            text = text[-per_path_budget:]
        rendered.append(f"Context path {index}: {text}")
    rendered.append(f"Proposition: {proposition}")
    return "\n".join(rendered)


def _candidate_texts(request: LogicSlotBindingRequest) -> tuple[str, list[str]]:
    max_paths = _env_int(
        "CUGA_LOGIC_MATCH_MAX_CONTEXT_PATHS",
        _DEFAULT_MAX_CONTEXT_PATHS,
    )
    budget = _env_int(
        "CUGA_LOGIC_MATCH_CONTEXT_CHAR_BUDGET",
        _DEFAULT_CONTEXT_CHAR_BUDGET,
        minimum=120,
    )
    node_prop = request.node_routing_text or request.node_content
    node_text = _contextual_text(
        node_prop,
        request.node_context_paths,
        max_paths=max_paths,
        context_char_budget=budget,
    )
    slot_texts = [
        _contextual_text(
            candidate.source_text,
            candidate.context_paths,
            max_paths=max_paths,
            context_char_budget=budget,
        )
        for candidate in request.candidates
    ]
    return node_text, slot_texts


def call_logic_slot_binding_model(
    request: LogicSlotBindingRequest,
) -> LogicSlotBindingResponse:
    """Match node/slot identity locally with bidirectional DeBERTa NLI.

    Positive identity requires high entailment in BOTH directions. Explicit
    negative polarity requires high contradiction in BOTH directions. Anything
    uncertain is left unresolved; false merges are intentionally penalized more
    heavily than missed bindings.
    """
    if not request.candidates:
        return LogicSlotBindingResponse()

    tokenizer, model, torch, device, label_indices = _get_model_bundle()
    entail_idx, contradiction_idx, neutral_idx = label_indices

    threshold = _env_float("CUGA_LOGIC_MATCH_THRESHOLD", _DEFAULT_MATCH_THRESHOLD)
    margin = _env_float("CUGA_LOGIC_MATCH_MARGIN", _DEFAULT_MATCH_MARGIN)
    max_length = _env_int(
        "CUGA_LOGIC_MATCH_MAX_LENGTH",
        _DEFAULT_MAX_LENGTH,
        minimum=64,
    )

    node_text, slot_texts = _candidate_texts(request)
    count = len(slot_texts)

    # One local batch evaluates every candidate in both directions.
    premises = [node_text] * count + slot_texts
    hypotheses = slot_texts + [node_text] * count

    with _INFERENCE_LOCK:
        encoded = tokenizer(
            premises,
            hypotheses,
            padding=True,
            truncation=True,
            max_length=max_length,
            return_tensors="pt",
        )
        encoded = {key: value.to(device) for key, value in encoded.items()}
        with serialized_local_accelerator(device=device):
            with torch.inference_mode():
                logits = model(**encoded).logits
                probabilities = torch.softmax(logits, dim=-1).detach().cpu()

    forward = probabilities[:count]
    backward = probabilities[count:]
    bindings: list[LogicSlotBindingDecision] = []

    for index, candidate in enumerate(request.candidates):
        entail_forward = float(forward[index, entail_idx])
        entail_backward = float(backward[index, entail_idx])
        contradiction_forward = float(forward[index, contradiction_idx])
        contradiction_backward = float(backward[index, contradiction_idx])
        neutral_forward = float(forward[index, neutral_idx])
        neutral_backward = float(backward[index, neutral_idx])

        positive_score = min(entail_forward, entail_backward)
        negative_score = min(contradiction_forward, contradiction_backward)

        value: bool | None = None
        confidence = max(positive_score, negative_score)
        if positive_score >= threshold and positive_score - negative_score >= margin:
            value = True
            confidence = positive_score
        elif negative_score >= threshold and negative_score - positive_score >= margin:
            value = False
            confidence = negative_score

        if memory_graph_trace_enabled():
            logger.info(
                "DEBERTA_LOGIC_MATCH node_id={} slot_id={} positive_score={:.6f} "
                "negative_score={:.6f} entail_fwd={:.6f} entail_rev={:.6f} "
                "contradiction_fwd={:.6f} contradiction_rev={:.6f} "
                "neutral_fwd={:.6f} neutral_rev={:.6f} threshold={:.3f} "
                "margin={:.3f} decision={}",
                request.node_id,
                candidate.slot_id,
                positive_score,
                negative_score,
                entail_forward,
                entail_backward,
                contradiction_forward,
                contradiction_backward,
                neutral_forward,
                neutral_backward,
                threshold,
                margin,
                "positive" if value is True else "negative" if value is False else "no_match",
            )

        if value is not None:
            bindings.append(
                LogicSlotBindingDecision(
                    slot_id=candidate.slot_id,
                    value=value,
                    confidence=confidence,
                )
            )

    if memory_graph_trace_enabled():
        logger.info(
            "Local DeBERTa logic-slot matching complete: node_id={} candidates={} "
            "bindings={} model={} device={} threshold={} margin={}",
            request.node_id,
            len(request.candidates),
            len(bindings),
            MATCHER_MODEL_NAME,
            device,
            threshold,
            margin,
        )
    return LogicSlotBindingResponse(bindings=bindings)
