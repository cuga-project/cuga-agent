"""System tool: read_image.

Accepts a local file path (absolute, relative, or /workspace/...) or an HTTPS
URL, sends the image to the configured LLM, and returns a text description.

Primary model is tried first (the same model the agent uses for everything
else).  If it rejects the multimodal content — e.g. a model like
``openai/gpt-oss-120b`` that does not support vision — the tool automatically
falls back to the model named in the ``IMAGE_ANALYSIS_MODEL`` environment
variable (e.g. ``Azure/gpt-4o``).

This lets CUGA handle images seamlessly regardless of whether the primary
model supports vision.
"""

from __future__ import annotations

import asyncio
import base64
import os
from pathlib import Path
from typing import Optional

from langchain_core.messages import HumanMessage
from langchain_core.tools import StructuredTool
from loguru import logger
from pydantic import BaseModel, Field

# Model name substrings that indicate text-only inference — skip primary and go
# straight to IMAGE_ANALYSIS_MODEL so we don't burn the HTTP timeout for nothing.
# Only include models that are definitively text-only across all versions.
# Do NOT add families with multimodal variants (e.g. gemma-4 supports vision).
_KNOWN_NON_VISION_PATTERNS = (
    "gpt-oss",
    "falcon",
)

# Per-attempt cap for each vision LLM call. read_image can chain a primary
# attempt followed by an IMAGE_ANALYSIS_MODEL fallback; without this cap a
# single hung call could ride the model's full HTTP timeout (up to
# connections.llm_http_timeout, 61s by default) twice, blowing past the
# sandbox's own wall-clock budget for the whole code block.
_VISION_CALL_TIMEOUT_SECONDS = 35.0

_MEDIA_TYPE_MAP = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
}


def _is_url(source: str) -> bool:
    return source.startswith("http://") or source.startswith("https://")


def _resolve_path(source: str) -> Path:
    p = Path(source)
    if p.is_absolute() and p.exists():
        return p
    # /workspace/... refers to the sandbox working directory
    if source.startswith("/workspace/"):
        rel = Path(source[len("/workspace/") :])
        if rel.exists():
            return rel
    return p


def _build_data_url(source: str) -> str:
    """Return a ``data:<media>; base64,...`` URL from a local path or HTTP URL."""
    if _is_url(source):
        import urllib.request

        suffix = Path(source.split("?")[0]).suffix or ".jpg"
        dest = Path(f"_img_download{suffix}")
        req = urllib.request.Request(
            source,
            headers={"User-Agent": "Mozilla/5.0 (compatible; CugaAgent/1.0)"},
        )
        with urllib.request.urlopen(req) as resp:
            dest.write_bytes(resp.read())
        path = dest
    else:
        path = _resolve_path(source)

    if not path.exists():
        raise FileNotFoundError(f"Image not found: {source!r}")

    raw = path.read_bytes()
    encoded = base64.standard_b64encode(raw).decode("ascii")
    media_type = _MEDIA_TYPE_MAP.get(path.suffix.lower(), "image/jpeg")
    return f"data:{media_type};base64,{encoded}"


async def read_image(image: str, question: str) -> str:
    """Analyze an image and return a text answer.

    Tries the primary model first; falls back to IMAGE_ANALYSIS_MODEL if the
    primary model does not support vision.

    Args:
        image: Absolute path, workspace-relative path (/workspace/file.png),
               or HTTPS URL of the image to analyze.
        question: What to extract or describe from the image.

    Returns:
        The model's textual answer about the image.
    """
    data_url = _build_data_url(image)
    multimodal_content = [
        {"type": "image_url", "image_url": {"url": data_url}},
        {"type": "text", "text": question},
    ]

    # ── attempt 1: primary model ────────────────────────────────────────────
    # Skip if the primary model is a known text-only model — hitting it would
    # just waste the full HTTP timeout before falling back to IMAGE_ANALYSIS_MODEL.
    _skip_primary = False
    try:
        from cuga.config import settings as _settings

        _primary_name = (_settings.agent.code.model.get("model_name") or "").lower()
        if any(pat in _primary_name for pat in _KNOWN_NON_VISION_PATTERNS):
            logger.info(
                f"read_image: primary model {_primary_name!r} is a known non-vision model, "
                "skipping directly to IMAGE_ANALYSIS_MODEL"
            )
            _skip_primary = True
    except Exception:
        pass

    if not _skip_primary:
        try:
            from cuga.backend.llm.models import LLMManager
            from cuga.config import settings

            primary_llm = LLMManager().get_model(settings.agent.code.model)
            msg = HumanMessage(content=multimodal_content)
            result = await asyncio.wait_for(primary_llm.ainvoke([msg]), timeout=_VISION_CALL_TIMEOUT_SECONDS)
            text = result.content if isinstance(result.content, str) else str(result.content)
            logger.info("read_image: primary model succeeded")
            return text
        except asyncio.TimeoutError:
            logger.info(
                f"read_image: primary model did not respond within {_VISION_CALL_TIMEOUT_SECONDS}s, "
                "falling back to IMAGE_ANALYSIS_MODEL"
            )
        except Exception as exc:
            logger.info(
                f"read_image: primary model rejected vision content ({type(exc).__name__}: {exc}), "
                "falling back to IMAGE_ANALYSIS_MODEL"
            )

    # ── attempt 2: IMAGE_ANALYSIS_MODEL fallback ────────────────────────────
    import litellm

    litellm.drop_params = True

    fallback_model = os.environ.get("IMAGE_ANALYSIS_MODEL", "").strip()
    if not fallback_model:
        raise RuntimeError(
            "Primary model does not support vision and IMAGE_ANALYSIS_MODEL is not set. "
            "Add IMAGE_ANALYSIS_MODEL=<model> to your .env to enable image analysis."
        )

    api_key: Optional[str] = os.environ.get("LITELLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    base_url: Optional[str] = os.environ.get("LITELLM_BASE_URL") or os.environ.get("OPENAI_BASE_URL")

    if not api_key:
        raise RuntimeError(
            "No API key available for IMAGE_ANALYSIS_MODEL fallback. Set LITELLM_API_KEY or OPENAI_API_KEY."
        )

    litellm_image_content = {"type": "image_url", "image_url": {"url": data_url}}
    completion_args: dict = {
        "model": fallback_model,
        "messages": [
            {
                "role": "user",
                "content": [
                    litellm_image_content,
                    {"type": "text", "text": question},
                ],
            }
        ],
        "max_tokens": 1024,
        "api_key": api_key,
    }
    if base_url:
        completion_args["base_url"] = base_url.rstrip("/")
        completion_args["custom_llm_provider"] = "openai"

    loop = asyncio.get_event_loop()
    try:
        response = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: litellm.completion(**completion_args)),
            timeout=_VISION_CALL_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as exc:
        raise RuntimeError(
            f"IMAGE_ANALYSIS_MODEL {fallback_model!r} did not respond within "
            f"{_VISION_CALL_TIMEOUT_SECONDS}s."
        ) from exc
    text = response.choices[0].message.content
    logger.info(f"read_image: fallback model {fallback_model!r} succeeded")
    return text


class _ReadImageInput(BaseModel):
    image: str = Field(
        ...,
        description=("Path to the image file (absolute, relative, or /workspace/filename) or an HTTPS URL."),
    )
    question: str = Field(
        ...,
        description="What to analyze, describe, or extract from the image.",
    )


def create_read_image_tool(resolve_workspace_path: Optional[callable] = None) -> StructuredTool:
    """Return a StructuredTool wrapping :func:`read_image`.

    Args:
        resolve_workspace_path: Optional callable that translates a virtual
            ``/workspace/...`` path to the real host path for the active
            thread's sandbox (e.g. a per-thread native/local sandbox root).
            Without it, ``read_image`` can only find files relative to the
            backend process's own working directory, which is wrong whenever
            the file was produced inside a per-thread sandbox workspace.
    """
    if resolve_workspace_path is not None:

        async def _read_image_with_resolved_path(image: str, question: str) -> str:
            return await read_image(resolve_workspace_path(image), question)

        coroutine = _read_image_with_resolved_path
    else:
        coroutine = read_image

    return StructuredTool.from_function(
        coroutine=coroutine,
        name="read_image",
        description=(
            "Analyze or describe an image. "
            "Use this whenever the user references an image file or URL, asks to "
            "describe a photo, extract text from a screenshot, read a chart/diagram, "
            "or answer any question that requires visual understanding. "
            "Accepts an image path (/workspace/file.png, absolute path, or URL) and "
            "a question. Returns a text description or answer."
        ),
        args_schema=_ReadImageInput,
    )
