import os
from datetime import datetime
from importlib import util
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

from cuga.config import DBS_DIR, PACKAGE_ROOT, kaizen_settings, settings

if TYPE_CHECKING:
    from kaizen.config.filesystem import FilesystemSettings
    from kaizen.config.kaizen import KaizenConfig
    from kaizen.config.llm import llm_settings
    from kaizen.config.milvus import MilvusDBSettings
    from kaizen.frontend.client.kaizen_client import KaizenClient


FilesystemSettings = None
KaizenConfig = None
llm_settings = None
MilvusDBSettings = None
KaizenClient = None
_KAIZEN_IMPORTED = False


def _ensure_kaizen_imports() -> None:
    global FilesystemSettings, KaizenConfig, llm_settings, MilvusDBSettings, KaizenClient, _KAIZEN_IMPORTED
    if _KAIZEN_IMPORTED:
        return

    try:
        from kaizen.config.filesystem import FilesystemSettings as _FilesystemSettings
        from kaizen.config.kaizen import KaizenConfig as _KaizenConfig
        from kaizen.config.llm import llm_settings as _llm_settings
        from kaizen.config.milvus import MilvusDBSettings as _MilvusDBSettings
        from kaizen.frontend.client.kaizen_client import KaizenClient as _KaizenClient
    except ModuleNotFoundError as exc:
        if util.find_spec("kaizen") is None:
            raise RuntimeError(
                "Kaizen is required for memory features but is not installed. "
                "Install with `uv sync --extra memory` (or `pip install \"cuga[memory]\"`) and rerun."
            ) from exc

        try:
            kaizen_version = version("kaizen")
        except PackageNotFoundError:
            kaizen_version = "unknown"

        raise RuntimeError(
            "Kaizen is installed but incompatible with CUGA memory integration "
            f"(installed version: {kaizen_version}). "
            "Expected modules such as `kaizen.config`, `kaizen.frontend`, and `kaizen.schema` "
            "were not found. Install a compatible Kaizen build, "
            "or use a local checkout with `uv pip install -e ./kaizen`."
        ) from exc

    FilesystemSettings = _FilesystemSettings
    KaizenConfig = _KaizenConfig
    llm_settings = _llm_settings
    MilvusDBSettings = _MilvusDBSettings
    KaizenClient = _KaizenClient
    _KAIZEN_IMPORTED = True


class RunRecord(BaseModel):
    id: str = Field(description="Run identifier")
    created_at: datetime = Field(description="Run creation timestamp")
    ended: bool = Field(default=False)
    steps: list[Any] = Field(default_factory=list)


_KAIZEN_CLIENT: Any | None = None


def _as_dict(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    if hasattr(raw, "to_dict"):
        return dict(raw.to_dict())
    return {}


def _resolve_path(value: Any, fallback: str) -> str:
    candidate = str(value or fallback).strip()
    path = Path(candidate)
    if not path.is_absolute():
        path = Path(PACKAGE_ROOT) / path
    return str(path.resolve())


def _get_kaizen_cfg() -> dict[str, Any]:
    return _as_dict(kaizen_settings.get("kaizen", default={}))


def _get_cuga_namespace_override() -> str | None:
    override = settings.get("memory.kaizen.namespace_id", default=None)
    if override is None:
        try:
            override = getattr(getattr(settings.memory, "kaizen", None), "namespace_id", None)
        except Exception:
            override = None
    normalized = str(override or "").strip()
    return normalized or None


def get_kaizen_namespace_id() -> str:
    # CUGA settings.toml can override namespace. Otherwise use Kaizen defaults.
    override = _get_cuga_namespace_override()
    if override:
        return override

    kaizen_cfg = _get_kaizen_cfg()
    file_namespace = str(kaizen_cfg.get("namespace_id") or "").strip()
    if file_namespace:
        return file_namespace

    _ensure_kaizen_imports()
    return KaizenConfig().namespace_id


def normalize_user_id(user_id: str | None) -> str:
    normalized = (user_id or "").strip()
    return normalized if normalized and normalized != "default" else "default"


def _ensure_kaizen_llm_env() -> None:
    """Configure Kaizen LLM settings directly (no env indirection)."""
    _ensure_kaizen_imports()

    try:
        fact_cfg = settings.memory.kaizen.fact_extraction.model
    except Exception:
        fact_cfg = None
    try:
        tips_cfg = settings.memory.kaizen.tips.model
    except Exception:
        tips_cfg = None
    try:
        conflict_cfg = settings.memory.kaizen.conflict_resolution.model
    except Exception:
        conflict_cfg = None

    kaizen_model_name = str(os.getenv("KAIZEN_MODEL_NAME") or "").strip()
    if not kaizen_model_name:
        model_name = str(os.getenv("MODEL_NAME") or "").strip()
        if model_name:
            os.environ["KAIZEN_MODEL_NAME"] = model_name
            kaizen_model_name = model_name

    default_model = kaizen_model_name or "gpt-4o"
    fact_model = str(getattr(fact_cfg, "model_name", None) or default_model)
    tips_model = str(getattr(tips_cfg, "model_name", None) or default_model)
    conflict_model = str(getattr(conflict_cfg, "model_name", None) or default_model)

    provider_platform = getattr(fact_cfg, "platform", None) or os.getenv("MODEL_PLATFORM", "openai")
    provider_platform = str(provider_platform or "openai")
    provider = provider_platform.lower()

    llm_settings.fact_extraction_model = fact_model
    llm_settings.tips_model = tips_model
    llm_settings.conflict_resolution_model = conflict_model
    llm_settings.custom_llm_provider = provider
    llm_settings.categorization_mode = str(settings.memory.categorization_mode)
    llm_settings.allow_dynamic_categories = bool(settings.memory.allow_dynamic_categories)
    llm_settings.confirm_new_categories = bool(settings.memory.confirm_new_categories)


def _parse_timeout(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _build_backend_settings() -> tuple[Literal["milvus", "filesystem"], Any]:
    _ensure_kaizen_imports()
    os.makedirs(DBS_DIR, exist_ok=True)

    kaizen_cfg = _get_kaizen_cfg()
    backend = str(kaizen_cfg.get("backend") or "milvus").strip().lower()
    if backend not in {"milvus", "filesystem"}:
        backend = "milvus"

    if backend == "filesystem":
        filesystem_cfg = _as_dict(kaizen_cfg.get("filesystem"))
        data_dir = _resolve_path(filesystem_cfg.get("data_dir"), os.path.join(DBS_DIR, "kaizen_data"))
        os.makedirs(data_dir, exist_ok=True)
        return "filesystem", FilesystemSettings(data_dir=data_dir)

    milvus_cfg = _as_dict(kaizen_cfg.get("milvus"))
    uri = _resolve_path(milvus_cfg.get("uri"), os.path.join(DBS_DIR, "entities.milvus.db"))
    sqlite_uri = _resolve_path(milvus_cfg.get("sqlite_uri"), os.path.join(DBS_DIR, "entities.sqlite.db"))
    timeout = _parse_timeout(milvus_cfg.get("timeout"))

    return "milvus", MilvusDBSettings(
        uri=uri,
        user=str(milvus_cfg.get("user") or ""),
        password=str(milvus_cfg.get("password") or ""),
        db_name=str(milvus_cfg.get("db_name") or ""),
        token=str(milvus_cfg.get("token") or ""),
        timeout=timeout,
        sqlite_uri=sqlite_uri,
        embedding_model=str(milvus_cfg.get("embedding_model") or "sentence-transformers/all-MiniLM-L6-v2"),
    )


def get_kaizen_client() -> "KaizenClient":
    global _KAIZEN_CLIENT
    if _KAIZEN_CLIENT is None:
        if not settings.advanced_features.enable_memory and not settings.advanced_features.enable_fact:
            raise RuntimeError(
                "Memory is disabled in settings. Set enable_memory = true or enable_fact = true."
            )

        _ensure_kaizen_imports()
        _ensure_kaizen_llm_env()
        backend, backend_settings = _build_backend_settings()
        kaizen_config = KaizenConfig(
            backend=backend,
            namespace_id=get_kaizen_namespace_id(),
            settings=backend_settings,
        )
        _KAIZEN_CLIENT = KaizenClient(config=kaizen_config)

    return _KAIZEN_CLIENT


async def sync_user_memory(
    *,
    user_id: str | None,
    query: str | None,
    namespace_id: str | None = None,
) -> tuple[str, dict[str, list[dict[str, Any]]]]:
    """Store current user utterance (if any) and retrieve relevant user memory."""
    kaizen_client = get_kaizen_client()
    normalized_user_id = normalize_user_id(user_id)
    resolved_namespace = namespace_id or get_kaizen_namespace_id()

    if query:
        kaizen_client.store_user_facts(
            namespace_id=resolved_namespace,
            message=query,
            user_id=normalized_user_id,
        )

    preferences = kaizen_client.retrieve_user_facts(
        namespace_id=resolved_namespace,
        user_id=normalized_user_id,
        query=query,
    )
    return normalized_user_id, preferences
