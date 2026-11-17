"""Embedding models CUGA runs through fastembed that fastembed does not ship itself.

Each loads the model authors' own ONNX file from Hugging Face, so it runs on the same
onnxruntime path as fastembed's built-in models, without PyTorch.
"""

from __future__ import annotations

# model name -> (dimension, ONNX file in the model's own repository)
EXTRA_MODELS: dict[str, tuple[int, str]] = {
    # MIT licence. The int8 build published with the model (113 MB); its vectors match the fp32
    # model's (cosine 0.995 or more on English, Chinese and Hindi text).
    "intfloat/multilingual-e5-small": (384, "onnx/model_qint8_avx512_vnni.onnx"),
}


def register(model_name: str) -> None:
    """Let fastembed load ``model_name`` when it is one of ours. Safe to call more than once."""
    spec = EXTRA_MODELS.get(model_name)
    if spec is None:
        return
    from fastembed import TextEmbedding
    from fastembed.common.model_description import ModelSource, PoolingType

    if any(m["model"] == model_name for m in TextEmbedding.list_supported_models()):
        return
    dim, model_file = spec
    TextEmbedding.add_custom_model(
        model=model_name,
        pooling=PoolingType.MEAN,
        normalization=True,
        sources=ModelSource(hf=model_name),
        dim=dim,
        model_file=model_file,
    )
