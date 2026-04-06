from __future__ import annotations

import logging
from dataclasses import dataclass, field, fields as dc_fields
from pathlib import Path
from typing import Any

logger = logging.getLogger("cuga.knowledge")

# Directory containing the profile TOML files
_PROFILES_DIR = (
    Path(__file__).resolve().parent.parent.parent / "configurations" / "knowledge" / "knowledge_profiles"
)

VALID_PROFILES = ("speed", "standard", "balanced", "max_quality")


def knowledge_vector_backend_for_settings(settings: Any) -> str:
    """Which knowledge vector path to use: mirrors ``storage.mode`` (local | prod)."""
    mode = (getattr(getattr(settings, "storage", None), "mode", None) or "local").lower()
    if mode == "prod":
        return "storage_prod"
    return "storage_local"


def load_profile(profile_name: str) -> dict[str, Any]:
    """Load a single RAG profile from its TOML file.

    Returns a dict with keys: profile, search, chunking, instructions.
    Raises FileNotFoundError if the profile file doesn't exist.
    """
    import tomllib

    path = _PROFILES_DIR / f"{profile_name}.toml"
    if not path.exists():
        raise FileNotFoundError(f"Profile file not found: {path}")
    with open(path, "rb") as f:
        return tomllib.load(f)


def list_profiles() -> dict[str, dict[str, Any]]:
    """Load all available RAG profiles.

    Returns a dict mapping profile name to its full parsed TOML content.
    """
    profiles: dict[str, dict[str, Any]] = {}
    if not _PROFILES_DIR.is_dir():
        logger.warning("Knowledge profiles directory not found: %s", _PROFILES_DIR)
        return profiles
    for name in VALID_PROFILES:
        try:
            profiles[name] = load_profile(name)
        except Exception as e:
            logger.warning("Failed to load profile %s: %s", name, e)
    return profiles


@dataclass
class KnowledgeConfig:
    """Configuration for the knowledge engine."""

    enabled: bool = False
    agent_level_enabled: bool = True
    session_level_enabled: bool = True
    persist_dir: Path = field(default_factory=lambda: Path.cwd() / ".cuga" / "knowledge")

    # Embeddings
    embedding_provider: str = "fastembed"  # fastembed | huggingface | openai | ollama
    embedding_model: str = ""  # empty = auto-detect per provider
    embedding_api_key: str = ""  # API key for openai provider (or set OPENAI_API_KEY env var)
    embedding_base_url: str = ""  # custom base URL for openai-compatible providers
    use_gpu: bool = True  # kept for backwards compat; fastembed manages acceleration internally

    # Chunking
    chunk_size: int = 1000
    chunk_overlap: int = 200

    # Postgres URL for knowledge when storage.mode=prod: defaults to storage.postgres_url;
    # set only to use a different DB than global storage.
    pgvector_connection_string: str = ""

    # Search
    rag_profile: str = "standard"  # speed | standard | balanced | max_quality
    default_limit: int = 10
    default_score_threshold: float = 0.0
    metric_type: str = "COSINE"
    max_search_attempts: int = 3  # max knowledge searches per user question

    # Engine
    max_ingest_workers: int = 2
    max_pending_tasks: int = 10

    # MCP transport
    mcp_transport: str = "http"  # http | stdio
    mcp_port: int = 8113

    # Limits
    max_upload_size_mb: int = 100
    max_url_download_size_mb: int = 50
    max_files_per_request: int = 10
    max_chunks_per_document: int = 10000

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for config_json storage (excludes persist_dir)."""
        return {f.name: getattr(self, f.name) for f in dc_fields(KnowledgeConfig) if f.name != "persist_dir"}

    def vector_config_hash(self) -> str:
        """Short hash of fields that affect vector compatibility.

        Only includes fields that change how documents are stored in the vector
        store (embedding model, chunking, metric). Search-only fields like
        default_limit or rag_profile are excluded.
        """
        import hashlib

        from cuga.config import settings

        sm = getattr(getattr(settings, "storage", None), "mode", "local") or "local"
        key = (
            f"{sm}|{self.embedding_provider}|{self.embedding_model}|"
            f"{self.chunk_size}|{self.chunk_overlap}|{self.metric_type}"
        )
        return hashlib.sha256(key.encode()).hexdigest()[:12]

    def validate(self) -> None:
        """Validate configuration values."""
        if self.chunk_size < 100:
            raise ValueError(f"chunk_size must be >= 100, got {self.chunk_size}")
        if self.chunk_overlap < 0 or self.chunk_overlap >= self.chunk_size:
            raise ValueError(f"chunk_overlap must be in [0, chunk_size), got {self.chunk_overlap}")
        if self.metric_type not in ("COSINE", "IP", "L2"):
            raise ValueError(f"metric_type must be COSINE, IP, or L2, got {self.metric_type}")
        if self.max_ingest_workers < 1:
            raise ValueError(f"max_ingest_workers must be >= 1, got {self.max_ingest_workers}")
        if self.max_pending_tasks < 1:
            raise ValueError(f"max_pending_tasks must be >= 1, got {self.max_pending_tasks}")
        if self.embedding_provider not in ("fastembed", "huggingface", "openai", "ollama"):
            raise ValueError(f"Unknown embedding_provider: {self.embedding_provider}")
        if self.max_search_attempts < 1:
            raise ValueError(f"max_search_attempts must be >= 1, got {self.max_search_attempts}")
        if self.mcp_transport not in ("http", "stdio"):
            raise ValueError(f"mcp_transport must be 'http' or 'stdio', got {self.mcp_transport}")
        if self.mcp_port < 1 or self.mcp_port > 65535:
            raise ValueError(f"mcp_port must be 1-65535, got {self.mcp_port}")
        if self.rag_profile not in VALID_PROFILES:
            raise ValueError(f"rag_profile must be one of {VALID_PROFILES}, got {self.rag_profile}")

    @staticmethod
    def coerce_and_validate(
        incoming: dict,
        base: KnowledgeConfig | None = None,
    ) -> KnowledgeConfig:
        """Coerce incoming dict types, merge with base config, and validate.

        Returns a fully validated KnowledgeConfig. Raises ValueError/TypeError on
        bad input. Unknown keys are silently ignored.
        """
        base = base or KnowledgeConfig()
        known = {f.name for f in dc_fields(KnowledgeConfig)} - {"persist_dir"}
        merged = {f.name: getattr(base, f.name) for f in dc_fields(KnowledgeConfig)}

        for k, v in incoming.items():
            if k not in known:
                continue
            target_type = type(merged[k])
            if target_type is bool:
                if isinstance(v, bool):
                    merged[k] = v
                elif str(v).lower() in ("true", "1", "yes"):
                    merged[k] = True
                elif str(v).lower() in ("false", "0", "no"):
                    merged[k] = False
                else:
                    raise ValueError(f"Invalid boolean for {k}: {v!r}")
            elif target_type is int:
                merged[k] = int(v)
            elif target_type is float:
                merged[k] = float(v)
            else:
                merged[k] = target_type(v)

        # Migrate legacy: huggingface without sentence-transformers → fastembed
        if merged.get("embedding_provider") == "huggingface":
            try:
                import sentence_transformers  # noqa: F401
            except ImportError:
                merged["embedding_provider"] = "fastembed"

        if "vector_store" in incoming:
            logger.warning(
                "knowledge.vector_store is ignored; set storage.mode in settings.toml "
                "(local = sqlite-vec, prod = Postgres pgvector)"
            )

        cfg = KnowledgeConfig(**merged)
        cfg.validate()
        return cfg

    @classmethod
    def from_settings(cls, settings) -> KnowledgeConfig:
        """Load from dynaconf settings object."""
        kb = settings.get("knowledge", {})
        if not kb:
            return cls()

        embeddings = kb.get("embeddings", {})
        chunking = kb.get("chunking", {})
        search = kb.get("search", {})
        engine = kb.get("engine", {})
        limits = kb.get("limits", {})
        mcp = kb.get("mcp", {})

        persist_dir_str = kb.get("persist_dir", "")
        persist_dir = Path(persist_dir_str) if persist_dir_str else Path.cwd() / ".cuga" / "knowledge"

        # Expand profile defaults — profile values are the base,
        # explicit settings.toml values override them.
        profile_name = search.get("rag_profile", "standard")
        try:
            profile_data = load_profile(profile_name)
            profile_search = profile_data.get("search", {})
            profile_chunking = profile_data.get("chunking", {})
        except Exception as e:
            logger.warning("Failed to load profile '%s' at startup: %s", profile_name, e)
            profile_search = {}
            profile_chunking = {}

        return cls(
            enabled=kb.get("enabled", False),
            agent_level_enabled=kb.get("agent_level_enabled", True),
            session_level_enabled=kb.get("session_level_enabled", True),
            persist_dir=persist_dir,
            embedding_provider=embeddings.get("provider", "fastembed"),
            embedding_model=embeddings.get("model", ""),
            embedding_api_key=embeddings.get("api_key", ""),
            embedding_base_url=embeddings.get("base_url", ""),
            use_gpu=embeddings.get("use_gpu", True),
            pgvector_connection_string=kb.get("pgvector_connection_string", ""),
            chunk_size=profile_chunking.get("chunk_size", chunking.get("chunk_size", 1000)),
            chunk_overlap=profile_chunking.get("chunk_overlap", chunking.get("chunk_overlap", 200)),
            rag_profile=profile_name,
            default_limit=profile_search.get("default_limit", search.get("default_limit", 10)),
            default_score_threshold=profile_search.get(
                "default_score_threshold", search.get("default_score_threshold", 0.0)
            ),
            metric_type=search.get("metric_type", "COSINE"),
            max_search_attempts=profile_search.get(
                "max_search_attempts", search.get("max_search_attempts", 3)
            ),
            max_ingest_workers=engine.get("max_ingest_workers", 2),
            max_pending_tasks=engine.get("max_pending_tasks", 10),
            max_upload_size_mb=limits.get("max_upload_size_mb", 100),
            max_url_download_size_mb=limits.get("max_url_download_size_mb", 50),
            max_files_per_request=limits.get("max_files_per_request", 10),
            max_chunks_per_document=limits.get("max_chunks_per_document", 10000),
            mcp_transport=mcp.get("transport", "http"),
            mcp_port=mcp.get("port", 8113),
        )
