"""Unit tests for the analyze_image system tool."""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cuga.backend.tools.image_analysis import (
    _build_data_url,
    _is_url,
    _resolve_path,
    analyze_image,
    create_analyze_image_tool,
)


# ── helpers ────────────────────────────────────────────────────────────────────


def _tiny_png_bytes() -> bytes:
    """Return the smallest valid PNG (1×1 red pixel)."""
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwADhQGAWjR9awAAAABJRU5ErkJggg=="
    )


# ── URL detection ──────────────────────────────────────────────────────────────


def test_is_url_https():
    assert _is_url("https://example.com/image.png") is True


def test_is_url_http():
    assert _is_url("http://example.com/image.png") is True


def test_is_url_path():
    assert _is_url("/workspace/photo.jpg") is False


def test_is_url_relative():
    assert _is_url("photo.png") is False


# ── path resolution ────────────────────────────────────────────────────────────


def test_resolve_path_absolute(tmp_path):
    f = tmp_path / "img.png"
    f.write_bytes(b"x")
    p = _resolve_path(str(f))
    assert p == f


def test_resolve_path_workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    f = tmp_path / "shot.png"
    f.write_bytes(b"x")
    p = _resolve_path("/workspace/shot.png")
    assert p.name == "shot.png"


# ── data URL building ──────────────────────────────────────────────────────────


def test_build_data_url_from_file(tmp_path):
    f = tmp_path / "img.png"
    f.write_bytes(_tiny_png_bytes())
    url = _build_data_url(str(f))
    assert url.startswith("data:image/png;base64,")
    # round-trip
    _, b64 = url.split(",", 1)
    assert base64.b64decode(b64) == _tiny_png_bytes()


def test_build_data_url_missing_file():
    with pytest.raises(FileNotFoundError):
        _build_data_url("/nonexistent/image.jpg")


def test_build_data_url_jpeg_media_type(tmp_path):
    f = tmp_path / "photo.jpg"
    f.write_bytes(b"\xff\xd8")
    url = _build_data_url(str(f))
    assert "image/jpeg" in url


# ── tool structure ─────────────────────────────────────────────────────────────


def test_create_tool_name_and_description():
    tool = create_analyze_image_tool()
    assert tool.name == "analyze_image"
    assert "image" in tool.description.lower()
    assert "vision" in tool.description.lower() or "visual" in tool.description.lower()


def test_create_tool_has_coroutine():
    tool = create_analyze_image_tool()
    assert tool.coroutine is not None


def test_create_tool_schema_fields():
    tool = create_analyze_image_tool()
    schema = tool.args_schema.model_json_schema()
    props = schema.get("properties", {})
    assert "image" in props
    assert "question" in props


# ── analyze_image logic ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_primary_model_used_when_it_succeeds(tmp_path):
    f = tmp_path / "img.png"
    f.write_bytes(_tiny_png_bytes())

    mock_result = MagicMock()
    mock_result.content = "A red pixel."

    mock_llm = AsyncMock()
    mock_llm.ainvoke = AsyncMock(return_value=mock_result)

    mock_mgr_instance = MagicMock()
    mock_mgr_instance.get_model.return_value = mock_llm

    with (
        patch("cuga.backend.llm.models.LLMManager", return_value=mock_mgr_instance),
        patch("cuga.config.settings"),
    ):
        result = await analyze_image(image=str(f), question="What colour is the pixel?")

    assert result == "A red pixel."
    mock_llm.ainvoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_fallback_used_when_primary_fails(tmp_path, monkeypatch):
    f = tmp_path / "img.png"
    f.write_bytes(_tiny_png_bytes())

    monkeypatch.setenv("IMAGE_ANALYSIS_MODEL", "Azure/gpt-4o")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("LITELLM_API_KEY", raising=False)
    monkeypatch.delenv("LITELLM_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    mock_llm = AsyncMock()
    mock_llm.ainvoke = AsyncMock(side_effect=RuntimeError("Vision not supported"))

    mock_mgr_instance = MagicMock()
    mock_mgr_instance.get_model.return_value = mock_llm

    fake_choice = MagicMock()
    fake_choice.message.content = "Fallback answer."
    fake_response = MagicMock()
    fake_response.choices = [fake_choice]

    with (
        patch("cuga.backend.llm.models.LLMManager", return_value=mock_mgr_instance),
        patch("cuga.config.settings"),
        patch("litellm.completion", return_value=fake_response),
    ):
        result = await analyze_image(image=str(f), question="Describe the image.")

    assert result == "Fallback answer."


@pytest.mark.asyncio
async def test_fallback_raises_when_no_model_configured(tmp_path, monkeypatch):
    f = tmp_path / "img.png"
    f.write_bytes(_tiny_png_bytes())

    monkeypatch.delenv("IMAGE_ANALYSIS_MODEL", raising=False)

    mock_llm = AsyncMock()
    mock_llm.ainvoke = AsyncMock(side_effect=RuntimeError("Vision not supported"))

    mock_mgr_instance = MagicMock()
    mock_mgr_instance.get_model.return_value = mock_llm

    with (
        patch("cuga.backend.llm.models.LLMManager", return_value=mock_mgr_instance),
        patch("cuga.config.settings"),
    ):
        with pytest.raises(RuntimeError, match="IMAGE_ANALYSIS_MODEL"):
            await analyze_image(image=str(f), question="What is this?")
