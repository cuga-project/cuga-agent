from __future__ import annotations

import hashlib
import logging
import os
import re
import unicodedata
from dataclasses import dataclass, field, fields as dc_fields
from pathlib import Path
from typing import Any

logger = logging.getLogger("cuga.knowledge")

# Directory containing the profile TOML files
_PROFILES_DIR = (
    Path(__file__).resolve().parent.parent.parent / "configurations" / "knowledge" / "knowledge_profiles"
)

VALID_PROFILES = ("speed", "standard", "balanced", "max_quality")
DOCLING_PDF_MODES = ("fast", "balanced", "accurate")
# One shared tuple for any knob shaped as "off / observe / enforce" — one
# definition prevents the two SAME-state-machine flags from drifting if
# someone adds a fourth mode to one and forgets the other. Operators
# learn one mental model: "off → never apply, dry_run → log what would
# happen, enforce → actually do it." Currently used by
# ``search_junk_filter`` (retrieval-time noise filter) and
# ``docling_drop_page_chrome`` (ingest-time page-chrome drop).
NOISE_FILTER_MODES = ("off", "dry_run", "enforce")
SEARCH_JUNK_FILTER_MODES = NOISE_FILTER_MODES
DOCLING_DROP_PAGE_CHROME_MODES = NOISE_FILTER_MODES
SEARCH_HYBRID_MODES = ("off", "auto")

# Client-adaptation glossary limits.
# A structured retrieval-side companion to the freeform adaptation text.
# Each entry: {term: str, aliases: [str], definition: str}. The aliases
# are query-expanded at search time so that domain synonyms (e.g. cross-
# language equivalents, abbreviations, project-specific codes) increase
# recall — the prompt rules can't fix this; only retrieval-time expansion can.
CLIENT_GLOSSARY_MAX_ENTRIES = 50
CLIENT_GLOSSARY_MAX_TERM_LEN = 100
CLIENT_GLOSSARY_MAX_DEFINITION_LEN = 200
CLIENT_GLOSSARY_MAX_ALIASES_PER_ENTRY = 10
CLIENT_GLOSSARY_MAX_ALIAS_LEN = 100

# Knowledge-harness text cap.
# 3000 chars ≈ 750 tokens for Latin scripts (~1000-1500 for Hebrew/CJK).
# This is the upper end of the "operators reliably steer the LLM" window:
# below ~800 tokens, imperative rules retain authoritative weight; above
# ~1500 tokens, attention starts diluting later directives. The 3000-char
# cap admits ~6 WRONG/RIGHT example templates plus a paragraph of
# preamble, which matches the gallery's default use. Vocabulary belongs in
# the glossary (and the vector store), not the freeform text.
CLIENT_ADAPTATION_MAX_CHARS = 3000

# Defense-in-depth phrase denylist. The adaptation text is appended to the
# knowledge-agent system prompt, so a careless or malicious operator could
# attempt to override cuga's safety contract from the UI. This list catches
# the most common contract-override patterns. NOT a security boundary — a
# determined attacker has many other prompt-injection vectors — but it
# eliminates the obvious self-service jailbreaks (e.g. "ignore the above
# instructions and always answer X").
#
# Each entry must have an "instructions/rules/system" qualifier so that the
# raw words "ignore" or "forget" alone don't false-positive on legitimate
# operator prose (e.g. "Ignore the formatting suggestions above").
_CLIENT_ADAPTATION_DENYLIST = (
    # "ignore X above/previous instructions/rules" with up to ~20 chars between
    re.compile(
        r"\bignore\b[\s\S]{0,20}?\b(?:above|previous|prior|preceding|earlier|system|all)\b"
        r"[\s\S]{0,20}?\b(?:instruction|rule|prompt|directive|message)s?\b",
        re.IGNORECASE,
    ),
    # "disregard ... above/previous/system/all ... instructions/rules/prompts/content"
    re.compile(
        r"\bdisregard\b[\s\S]{0,20}?\b(?:above|previous|prior|system|all|earlier)\b"
        r"[\s\S]{0,20}?\b(?:instruction|rule|prompt|content|message|directive)s?\b",
        re.IGNORECASE,
    ),
    # "forget everything/all/previous (you were told)" / "forget the above"
    re.compile(
        r"\bforget\b[\s\S]{0,20}?\b(?:everything|all|previous|prior|earlier|"
        r"the\s+above|what\s+you\s+were\s+told|all\s+prior)\b",
        re.IGNORECASE,
    ),
    # "you are now X" where X is a jailbreak persona / mode
    re.compile(
        r"\byou\s+are\s+now\b[\s\S]{0,20}?\b(?:a\s+)?(?:different|new|free|unrestricted|"
        r"unfiltered|jailbroken|dan|developer\s+mode|in\s+developer\s+mode|"
        r"in\s+jailbreak\s+mode|without\s+restrictions?|no\s+longer\s+bound)",
        re.IGNORECASE,
    ),
    # "override X" where X is a safety/system surface
    re.compile(
        r"\boverride\b[\s\S]{0,30}?\b(?:system\s+prompt|safety|safeguards?|"
        r"critical\s+rules?|previous\s+(?:instructions?|rules?)|cuga(?:'s)?\s+contract)",
        re.IGNORECASE,
    ),
    # "new system prompt:" / "new instructions follow:" etc — section-header style
    re.compile(
        r"\bnew\s+(?:system\s+prompt|instructions?\s+follow|rules?\s+follow|"
        r"directives?\s+follow)\s*[:.\-]",
        re.IGNORECASE,
    ),
)

# Unicode bidi-override codepoints — well-known prompt-smuggling vector
# (text can be reordered invisibly so what the operator sees is not what the
# LLM receives). The Unicode BIDI algorithm handles RTL languages automatically
# from character properties; explicit override codepoints are never needed for
# legitimate Hebrew/Arabic/etc. content.
_BIDI_OVERRIDE_CODEPOINTS = frozenset(
    {
        "\u202a",  # LEFT-TO-RIGHT EMBEDDING
        "\u202b",  # RIGHT-TO-LEFT EMBEDDING
        "\u202c",  # POP DIRECTIONAL FORMATTING
        "\u202d",  # LEFT-TO-RIGHT OVERRIDE
        "\u202e",  # RIGHT-TO-LEFT OVERRIDE
        "\u2066",  # LEFT-TO-RIGHT ISOLATE
        "\u2067",  # RIGHT-TO-LEFT ISOLATE
        "\u2068",  # FIRST STRONG ISOLATE
        "\u2069",  # POP DIRECTIONAL ISOLATE
    }
)

# C1 control characters (U+0080–U+009F) plus other invisible / dangerous
# control runs. Only \t, \n, \r are permitted from the C0 range.
_PERMITTED_CONTROL_CHARS = frozenset({"\t", "\n", "\r"})


class ClientAdaptationError(ValueError):
    """Raised when client_adaptation_text fails validation.

    Carries a machine-readable ``code`` so the API layer can return a
    structured 422 and the UI can render specific error affordances per
    failure mode (length / phrase / bidi / control).
    """

    def __init__(self, code: str, message: str, **detail: Any):
        super().__init__(message)
        self.code = code
        self.detail = detail

    def to_dict(self) -> dict[str, Any]:
        return {"error": self.code, "message": str(self), **self.detail}


def _validate_client_adaptation(text: str) -> None:
    """Apply enterprise safety + structural checks to adaptation text.

    Raises ClientAdaptationError with a specific ``code`` on rejection so
    callers can present actionable UX.
    """
    if not isinstance(text, str):
        raise ClientAdaptationError(
            "type_error",
            f"client_adaptation_text must be str, got {type(text).__name__}",
        )
    if len(text) > CLIENT_ADAPTATION_MAX_CHARS:
        raise ClientAdaptationError(
            "length_exceeded",
            f"client_adaptation_text exceeds {CLIENT_ADAPTATION_MAX_CHARS} chars",
            length=len(text),
            max=CLIENT_ADAPTATION_MAX_CHARS,
        )
    if "\x00" in text:
        raise ClientAdaptationError("null_byte", "client_adaptation_text must not contain null bytes")
    # Bidi-override scan
    for cp in text:
        if cp in _BIDI_OVERRIDE_CODEPOINTS:
            raise ClientAdaptationError(
                "bidi_override",
                "client_adaptation_text contains Unicode bidi-override codepoint "
                "(prompt-smuggling vector). Re-author without invisible direction "
                "controls — the Unicode BIDI algorithm handles RTL automatically.",
                codepoint=f"U+{ord(cp):04X}",
            )
    # C1 + disallowed C0 scan
    for cp in text:
        if cp in _PERMITTED_CONTROL_CHARS:
            continue
        # C0 controls (0x00-0x1F) excluding tab/lf/cr, and C1 controls (0x80-0x9F)
        if (0x00 <= ord(cp) <= 0x1F) or (0x80 <= ord(cp) <= 0x9F):
            raise ClientAdaptationError(
                "control_char",
                "client_adaptation_text contains disallowed control character. "
                "Only TAB, LF, CR are permitted.",
                codepoint=f"U+{ord(cp):04X}",
            )
    # Contract-override phrase denylist
    for pattern in _CLIENT_ADAPTATION_DENYLIST:
        m = pattern.search(text)
        if m:
            raise ClientAdaptationError(
                "contract_override_phrase",
                f"client_adaptation_text contains a phrase that attempts to "
                f"override cuga's safety contract: {m.group(0)!r}. The adaptation "
                f"is appended after the contract, not as a replacement for it.",
                phrase=m.group(0),
                pattern=pattern.pattern,
            )


def _validate_glossary(entries: Any) -> list[dict[str, Any]]:
    """Validate + normalize a glossary list. Returns a clean list-of-dicts.

    Schema per entry:
        {"term": str, "aliases": list[str] (optional), "definition": str (optional)}

    Raises ClientAdaptationError on any structural / safety violation.
    Permissive on shape (accepts None for omitted optional fields), strict on
    safety (same C0/C1, bidi-override, NFC rules as adaptation text).
    """
    if entries is None or entries == "":
        return []
    if not isinstance(entries, list):
        raise ClientAdaptationError(
            "glossary_type_error",
            f"client_adaptation_glossary must be a list, got {type(entries).__name__}",
        )
    if len(entries) > CLIENT_GLOSSARY_MAX_ENTRIES:
        raise ClientAdaptationError(
            "glossary_length_exceeded",
            f"glossary has {len(entries)} entries, max {CLIENT_GLOSSARY_MAX_ENTRIES}",
            count=len(entries),
            max=CLIENT_GLOSSARY_MAX_ENTRIES,
        )

    cleaned: list[dict[str, Any]] = []
    seen_terms: set[str] = set()
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ClientAdaptationError(
                "glossary_entry_type_error",
                f"glossary entry {i} must be a dict, got {type(entry).__name__}",
                index=i,
            )

        term = entry.get("term", "")
        # Silently skip entries with an empty term. The UI synthesizes
        # an empty-term row when the operator clicks "Add term" and only
        # the term field gets populated as they type — rejecting on the
        # debounced PATCH would 422 every "Add term" click. Mirrors the
        # blank-alias skip below (line ~283). Strict typing still applies:
        # a non-string term is a programmer error, not in-progress input.
        if term is None or (isinstance(term, str) and not term.strip()):
            continue
        if not isinstance(term, str):
            raise ClientAdaptationError(
                "glossary_term_type_error",
                f"glossary entry {i} term must be a string, got {type(term).__name__}",
                index=i,
            )
        term = unicodedata.normalize("NFC", term).strip()
        if len(term) > CLIENT_GLOSSARY_MAX_TERM_LEN:
            raise ClientAdaptationError(
                "glossary_term_too_long",
                f"glossary entry {i} term exceeds {CLIENT_GLOSSARY_MAX_TERM_LEN} chars",
                index=i,
            )
        # Use a case-folded key for duplicate detection but preserve display case.
        term_key = term.casefold()
        if term_key in seen_terms:
            raise ClientAdaptationError(
                "glossary_duplicate_term",
                f"glossary contains duplicate term {term!r}",
                term=term,
            )
        seen_terms.add(term_key)

        aliases_raw = entry.get("aliases", []) or []
        if not isinstance(aliases_raw, list):
            raise ClientAdaptationError(
                "glossary_aliases_type_error",
                f"glossary entry {i} aliases must be a list",
                index=i,
            )
        if len(aliases_raw) > CLIENT_GLOSSARY_MAX_ALIASES_PER_ENTRY:
            raise ClientAdaptationError(
                "glossary_too_many_aliases",
                f"glossary entry {i} has {len(aliases_raw)} aliases, "
                f"max {CLIENT_GLOSSARY_MAX_ALIASES_PER_ENTRY}",
                index=i,
            )
        aliases: list[str] = []
        for j, a in enumerate(aliases_raw):
            if not isinstance(a, str) or not a.strip():
                continue  # skip blanks silently
            a_clean = unicodedata.normalize("NFC", a).strip()
            if len(a_clean) > CLIENT_GLOSSARY_MAX_ALIAS_LEN:
                raise ClientAdaptationError(
                    "glossary_alias_too_long",
                    f"glossary entry {i} alias {j} exceeds {CLIENT_GLOSSARY_MAX_ALIAS_LEN} chars",
                    index=i,
                )
            # Re-use the same safety scanner as the adaptation body so the
            # glossary can't be a smuggling vector either.
            _scan_unsafe_chars(a_clean, where=f"glossary[{i}].aliases[{j}]")
            aliases.append(a_clean)
        # De-dup aliases (case-fold-aware) while preserving order
        seen_aliases: set[str] = set()
        deduped: list[str] = []
        for a in aliases:
            ak = a.casefold()
            if ak in seen_aliases or ak == term_key:
                continue
            seen_aliases.add(ak)
            deduped.append(a)
        aliases = deduped

        definition = entry.get("definition", "") or ""
        if not isinstance(definition, str):
            raise ClientAdaptationError(
                "glossary_definition_type_error",
                f"glossary entry {i} definition must be a string",
                index=i,
            )
        definition = unicodedata.normalize("NFC", definition).strip()
        if len(definition) > CLIENT_GLOSSARY_MAX_DEFINITION_LEN:
            raise ClientAdaptationError(
                "glossary_definition_too_long",
                f"glossary entry {i} definition exceeds {CLIENT_GLOSSARY_MAX_DEFINITION_LEN} chars",
                index=i,
            )
        _scan_unsafe_chars(term, where=f"glossary[{i}].term")
        _scan_unsafe_chars(definition, where=f"glossary[{i}].definition")

        cleaned.append({"term": term, "aliases": aliases, "definition": definition})

    return cleaned


def _scan_unsafe_chars(text: str, *, where: str) -> None:
    """Shared safety scanner (bidi + control + contract-override phrases)
    for glossary entries.

    Same rules as adaptation text; raises ClientAdaptationError with a
    `where=` location field so the UI can point at the right cell.

    Audit-finding B1 fix: previously this scanned only bidi + control chars,
    so an operator could land a jailbreak phrase like "ignore the above
    instructions" inside a glossary alias or definition and bypass the
    adaptation-text denylist entirely. The denylist now runs on EVERY
    glossary text field — term, aliases, definition.
    """
    for cp in text:
        if cp in _BIDI_OVERRIDE_CODEPOINTS:
            raise ClientAdaptationError(
                "glossary_bidi_override",
                f"{where} contains Unicode bidi-override codepoint",
                codepoint=f"U+{ord(cp):04X}",
                where=where,
            )
        if cp in _PERMITTED_CONTROL_CHARS:
            continue
        if (0x00 <= ord(cp) <= 0x1F) or (0x80 <= ord(cp) <= 0x9F):
            raise ClientAdaptationError(
                "glossary_control_char",
                f"{where} contains disallowed control character",
                codepoint=f"U+{ord(cp):04X}",
                where=where,
            )
    # Contract-override phrase denylist — same regex set as the freeform
    # adaptation text so the glossary can't smuggle a jailbreak the text-side
    # validator would have caught.
    for pattern in _CLIENT_ADAPTATION_DENYLIST:
        m = pattern.search(text)
        if m:
            raise ClientAdaptationError(
                "glossary_contract_override_phrase",
                f"{where} contains a contract-override phrase: {m.group(0)!r}",
                phrase=m.group(0),
                pattern=pattern.pattern,
                where=where,
            )


def client_adaptation_hash(text: str) -> str:
    """Stable 12-char SHA256 prefix for audit + observability.

    Hash is computed AFTER normalization so that equivalent NFC-different
    inputs produce the same hash (preventing 'mystery diffs' from copy-paste
    artifacts). Empty string hashes to the same value across calls — that's
    fine, it just means 'no adaptation set' is one well-known value.
    """
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:12]


def client_glossary_hash(entries: list[dict[str, Any]] | None) -> str:
    """Stable 12-char SHA256 of the glossary, structure-aware.

    JSON-serialize with sorted keys + entry order preserved (operators care
    about ordering for display, but the hash is order-stable per dict because
    we sort keys; alias order is preserved because aliases are semantically
    ordered preferences).
    """
    import json as _json

    entries = entries or []
    payload = _json.dumps(entries, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


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
        logger.warning(f"Knowledge profiles directory not found: {_PROFILES_DIR}")
        return profiles
    for name in VALID_PROFILES:
        try:
            profiles[name] = load_profile(name)
        except Exception as e:
            logger.warning(f"Failed to load profile {name}: {e}")
    return profiles


@dataclass
class KnowledgeConfig:
    """Configuration for the knowledge engine."""

    enabled: bool = False
    agent_level_enabled: bool = True
    session_level_enabled: bool = True
    persist_dir: Path = field(default_factory=lambda: Path.cwd() / ".cuga" / "knowledge")

    # Embeddings
    embedding_provider: str = "fastembed"  # fastembed | huggingface | openai | ollama | openrouter
    embedding_model: str = ""  # empty = auto-detect per provider
    embedding_api_key: str = ""  # API key for openai provider (or set OPENAI_API_KEY env var)
    embedding_base_url: str = ""  # custom base URL for openai-compatible providers
    # Provider-specific extra kwargs (Azure api_version + deployment, Bedrock region, ...).
    # str->primitive (str/int/bool/float) only — keeps snapshot serialization safe.
    # See https://docs.litellm.ai/docs/embedding/supported_embedding for per-provider knobs.
    embedding_extra_params: dict[str, Any] = field(default_factory=dict)
    use_gpu: bool = (
        True  # autodetect GPU (CUDA/CoreML for fastembed+Docling, MPS/CUDA for huggingface); False forces CPU
    )
    # Hard-fail mode for operators who deploy the GPU image and want a
    # silent-CPU regression to crash the pod at startup instead of
    # silently downgrading. Pairs with the GPU image's CUGA_GPU_BUILD=1
    # env var. When True AND no GPU runtime is loaded, the engine raises
    # at __init__ instead of just warning. vLLM / TGI both default to
    # this fail-fast posture; cuga keeps it opt-in for mixed CPU/GPU
    # fleets that deploy one image everywhere.
    gpu_required: bool = False

    # Docling layout (object-detection) engine selector. Independent of
    # ``docling_pdf_mode`` — pdf_mode toggles WHICH stages run; this picks
    # WHICH BACKEND the layout stage uses.
    #   "auto"         — Docling default (ONNX via CPU on Mac, CUDA on NVIDIA)
    #   "onnx"         — explicit ONNX object-detection engine
    #   "transformers" — PyTorch / HF Transformers; respects MPS + CUDA via
    #                    ``device_map``. The only way to engage Apple GPU
    #                    for layout on a Mac today.
    docling_layout_engine: str = "auto"

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

    # Adapter batching (Steps 4-7 of issue #183 ingest perf work).
    # ``embedding_batch_size``: chunks per call to embed_documents. Smaller =
    # finer progress granularity; larger = lower per-call overhead on cloud
    # embedders. ``embedding_concurrency``: max concurrent embed sub-batches
    # when the provider is network-based (openai / ollama); local providers
    # (fastembed / huggingface) stay sequential — fastembed's ONNX runtime
    # already uses internal threads and concurrent gather wouldn't help.
    # ``vector_insert_batch_size`` caps each ``add_many`` transaction so a
    # large document doesn't blow past pgvector's command_timeout or hold the
    # HNSW write lock for too long.
    embedding_batch_size: int = 64
    embedding_concurrency: int = 4
    vector_insert_batch_size: int = 200

    # Docling parsing level (issue #183 P1). Trades parse speed against
    # extraction fidelity. Concrete mapping in engine._get_docling_converter:
    #
    #   "fast":     do_ocr=False, do_table_structure=False  — digital PDFs
    #               only, ~3-10x faster on text-heavy docs, NO scanned support.
    #   "balanced": do_ocr=False, do_table_structure=True   — most digital
    #               PDFs work well, tables extracted, ~2x faster than accurate.
    #   "accurate": do_ocr=True,  do_table_structure=True   — Docling defaults;
    #               scanned PDFs work; full fidelity. This is today's behaviour.
    #
    # Default stays "accurate" so existing users don't regress on scanned PDFs.
    # Snapshotted with the published config — to_dict / coerce_and_validate
    # round-trip preserves it.
    docling_pdf_mode: str = "accurate"

    # Ingest-time page-chrome drop (D3) — drops chunks whose
    # ``dl_meta.doc_items`` are all ``page_footer`` / ``page_header`` labels
    # (e.g. academic-paper line-number gutters). Same three-mode shape as
    # ``search_junk_filter`` so operators have one knob pattern. Default
    # "enforce" because D3 has been stable since ship; flip to "dry_run"
    # if a Docling label regression is suspected (D3 will then log what
    # it would have dropped without actually dropping), then back to
    # "enforce" once verified.
    docling_drop_page_chrome: str = "enforce"

    # Retrieval-time junk-chunk filter — backstop for formats Docling's
    # ingest-time page-chrome filter (D3) doesn't cover (plain text,
    # markdown, and pre-existing collections ingested before D3 shipped).
    # Modes:
    #   "off"      — never filter
    #   "dry_run"  — count + log what WOULD be filtered, but return everything
    #   "enforce"  — drop filtered chunks from results (default)
    #
    # Default flipped from "dry_run" to "enforce" after a production
    # trace showed the LLM was being shown 7 junk chunks the classifier
    # had already flagged (single-token "Abstract", "```" code fences,
    # academic-paper line-number runs). The rules have been observed at
    # scale and false-positive risk on real prose is bounded. Operators
    # who want the observability-without-enforcement posture during a
    # rules-tuning window can set ``"dry_run"`` explicitly.
    search_junk_filter: str = "enforce"

    # Hybrid retrieval (BM25 + dense, fused via Reciprocal Rank Fusion).
    # When ``"auto"`` the engine runs both legs in parallel and fuses
    # ranks. When the lexical leg has no signal (empty result — e.g. the
    # FTS table hasn't been created yet for a pre-upgrade collection, or
    # the pgvector backend, or the query has zero useful tokens) the
    # fusion collapses to pure-dense, so there's no regression vs the
    # dense-only path.
    #
    # Modes:
    #   "auto" — run dense + lexical in parallel, RRF-fuse (default)
    #   "off"  — dense only (skips the lexical leg entirely)
    search_hybrid_mode: str = "auto"

    # Query transformation (LLM-assisted). Off by default — it adds a pre-retrieval
    # LLM round-trip and its gain is corpus-conditional, so it is eval-gated and
    # pre-tuned per profile by the cuga team, never a client-facing switch. Needs a
    # host-injected chat model (KnowledgeEngine(config, chat_generator=...)); inert
    # without one. Search-only; NOT in vector_config_hash.
    #   "off"          — plain query only (default)
    #   "multi_query"  — LLM rewrites; original + (n-1) variants, RRF-fused
    #   "hyde"         — original query (dense+lexical) PLUS a hypothetical doc as
    #                    an extra dense leg (never replaces the real query)
    search_query_transform: str = "off"
    search_query_transform_n: int = 3  # total query legs for multi_query (original + n-1 rewrites)

    # Client adaptation. Operator-supplied markdown appended to the
    # knowledge-agent system prompt — domain glossary, verbatim-preservation
    # rules, anti-hedging rules. Empty string (default) disables the feature.
    # Not part of vector_config_hash — prompt changes don't invalidate vectors.
    # Carried with the knowledge snapshot via to_dict().
    client_adaptation_text: str = ""

    # Structured glossary — retrieval-side companion to the freeform text.
    # Each entry: {term: str, aliases: list[str], definition: str}.
    # Aliases are query-expanded at search time (see search.expand_query_with_glossary)
    # so cross-language / abbreviated / domain-specific vocabulary lifts recall
    # for tokens that a prompt rule cannot fix. Also rendered into the prompt
    # as a small table so the LLM sees the canonical terms + their aliases.
    # Validated by _validate_glossary which enforces per-field length, alias
    # count, NFC normalization, dup detection, bidi/control-char rejection.
    client_adaptation_glossary: list[dict[str, Any]] = field(default_factory=list)

    # Cross-encoder reranking — opt-in. When enabled, the top
    # ``rerank_top_k_in`` candidates (candidate_k) from dense+sparse RRF fusion
    # are passed through fastembed's TextCrossEncoder (ONNX/CPU, no torch;
    # default ``BAAI/bge-reranker-base``, Apache-2.0) which rescores each pair
    # with full bidirectional attention. We then return the requested ``limit``
    # (return_k) from the rescored set. Search-only; NOT in vector_config_hash —
    # toggling this never invalidates the vector index. NOTE: the model MUST be
    # in fastembed's TextCrossEncoder list (bge-reranker-v2-m3 is NOT — it would
    # force the torch path); light English option: Xenova/ms-marco-MiniLM-L-12-v2.
    rerank_enabled: bool = False
    rerank_top_k_in: int = 20
    rerank_model: str = "BAAI/bge-reranker-base"

    # Fields whose values are credentials. Stripped when publishing snapshots
    # so secrets never cross machines via the snapshot store.
    _SECRET_FIELDS = ("embedding_api_key",)

    # Fields excluded from ``to_dict()`` because they're non-serializable
    # (``persist_dir`` is a ``Path``).
    _NON_SERIALIZED_FIELDS = ("persist_dir",)

    def to_dict(self, include_secrets: bool = True) -> dict[str, Any]:
        """Serialize to dict for config_json storage.

        ``include_secrets=True`` (default) keeps API keys — appropriate for the
        local draft persistence, where the key needs to survive a restart on
        the same machine. ``include_secrets=False`` blanks out secret fields:
        use this for any snapshot that will be PUBLISHED or shared (cross-
        machine config history, registry-stored snapshots).
        """
        out = {
            f.name: getattr(self, f.name)
            for f in dc_fields(KnowledgeConfig)
            if f.name not in self._NON_SERIALIZED_FIELDS
        }
        if not include_secrets:
            for k in self._SECRET_FIELDS:
                if k in out and out[k]:
                    out[k] = ""
        return out

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
        # Only COSINE is implemented end-to-end today: ProdEmbeddingStore
        # creates the HNSW index with ``vector_cosine_ops`` and queries it
        # with ``<=>`` (cosine distance). Accepting "IP" or "L2" here would
        # silently use a cosine index for an inner-product or L2-distance
        # query — wrong scores, slow lookups. Reject loudly until multi-
        # metric support ships (see review comment 52, deferred to a
        # storage-layer follow-up PR).
        if self.metric_type != "COSINE":
            raise ValueError(
                f"metric_type must be COSINE (the only metric supported by "
                f"the storage backend in this release), got {self.metric_type!r}. "
                f"IP and L2 are tracked for a future storage-layer follow-up."
            )
        if self.max_ingest_workers < 1:
            raise ValueError(f"max_ingest_workers must be >= 1, got {self.max_ingest_workers}")
        if self.max_pending_tasks < 1:
            raise ValueError(f"max_pending_tasks must be >= 1, got {self.max_pending_tasks}")
        if self.embedding_provider not in (
            "fastembed",
            "huggingface",
            "openai",
            "ollama",
            "openrouter",
            "litellm",
        ):
            raise ValueError(f"Unknown embedding_provider: {self.embedding_provider}")
        # OpenRouter has no canonical default embeddings model and our policy
        # is to require an explicit one. Catch the missing-model case at
        # validation time so the error message names the right field — without
        # this, the failure surfaces later inside ``create_embeddings`` after
        # the engine has already started thinking the config is good.
        if self.embedding_provider == "openrouter" and not (self.embedding_model or "").strip():
            raise ValueError(
                "OpenRouter embedding provider requires an embedding_model. "
                "Pick a model from "
                "https://openrouter.ai/models?output_modalities=embeddings "
                "and set knowledge.embeddings.model."
            )
        # embedding_extra_params: keep payload small and JSON-safe so snapshots survive.
        if self.embedding_extra_params:
            if not isinstance(self.embedding_extra_params, dict):
                raise ValueError(
                    f"embedding_extra_params must be a dict, got {type(self.embedding_extra_params).__name__}"
                )
            for k, v in self.embedding_extra_params.items():
                if not isinstance(k, str):
                    raise ValueError(f"embedding_extra_params keys must be str, got {type(k).__name__}")
                if not isinstance(v, (str, int, float, bool)):
                    raise ValueError(
                        f"embedding_extra_params[{k!r}] must be str/int/float/bool, "
                        f"got {type(v).__name__} — nested structures are not snapshot-safe"
                    )
        if self.embedding_provider == "huggingface":
            # CUGA's huggingface path uses a tiny PyTorch wrapper built on
            # ``transformers`` + ``torch`` — both already required by Docling.
            # No optional deps to validate. The check stays here as a hook in
            # case we later move to a fatter implementation.
            try:
                import torch  # noqa: F401
                import transformers  # noqa: F401
            except ImportError as e:
                raise ValueError(
                    "HuggingFace embedding provider requires transformers + torch "
                    f"(both core CUGA deps via Docling). Import failed: {e}"
                )
        if self.embedding_provider == "litellm" and not (self.embedding_model or "").strip():
            raise ValueError(
                "LiteLLM embedding provider requires an embedding_model with a "
                "provider prefix (e.g. 'openai/text-embedding-3-small', "
                "'cohere/embed-english-v3.0', 'azure/<deployment>'). "
                "See https://docs.litellm.ai/docs/embedding/supported_embedding."
            )
        if self.max_search_attempts < 1:
            raise ValueError(f"max_search_attempts must be >= 1, got {self.max_search_attempts}")
        if self.mcp_transport not in ("http", "stdio"):
            raise ValueError(f"mcp_transport must be 'http' or 'stdio', got {self.mcp_transport}")
        if self.mcp_port < 1 or self.mcp_port > 65535:
            raise ValueError(f"mcp_port must be 1-65535, got {self.mcp_port}")
        if self.rag_profile not in VALID_PROFILES:
            raise ValueError(f"rag_profile must be one of {VALID_PROFILES}, got {self.rag_profile}")
        if self.docling_pdf_mode not in DOCLING_PDF_MODES:
            raise ValueError(
                f"docling_pdf_mode must be one of {DOCLING_PDF_MODES}, got {self.docling_pdf_mode!r}"
            )
        if self.search_junk_filter not in SEARCH_JUNK_FILTER_MODES:
            raise ValueError(
                f"search_junk_filter must be one of {SEARCH_JUNK_FILTER_MODES}, "
                f"got {self.search_junk_filter!r}"
            )
        if self.docling_drop_page_chrome not in DOCLING_DROP_PAGE_CHROME_MODES:
            raise ValueError(
                f"docling_drop_page_chrome must be one of "
                f"{DOCLING_DROP_PAGE_CHROME_MODES}, got {self.docling_drop_page_chrome!r}"
            )
        if self.search_hybrid_mode not in SEARCH_HYBRID_MODES:
            raise ValueError(
                f"search_hybrid_mode must be one of {SEARCH_HYBRID_MODES}, got {self.search_hybrid_mode!r}"
            )
        from cuga.backend.knowledge.query_transform import VALID_QUERY_TRANSFORMS

        if self.search_query_transform not in VALID_QUERY_TRANSFORMS:
            raise ValueError(
                f"search_query_transform must be one of {VALID_QUERY_TRANSFORMS}, "
                f"got {self.search_query_transform!r}"
            )
        if self.search_query_transform_n < 1 or self.search_query_transform_n > 8:
            raise ValueError(
                f"search_query_transform_n must be in [1, 8], got {self.search_query_transform_n}"
            )
        if self.docling_layout_engine not in ("auto", "onnx", "transformers"):
            raise ValueError(
                f"docling_layout_engine must be 'auto', 'onnx', or 'transformers', "
                f"got {self.docling_layout_engine!r}"
            )
        if self.embedding_batch_size < 1:
            raise ValueError(f"embedding_batch_size must be >= 1, got {self.embedding_batch_size}")
        if self.embedding_concurrency < 1:
            raise ValueError(f"embedding_concurrency must be >= 1, got {self.embedding_concurrency}")
        if self.vector_insert_batch_size < 1:
            raise ValueError(f"vector_insert_batch_size must be >= 1, got {self.vector_insert_batch_size}")
        # Client-adaptation: validate freeform text and normalize/validate
        # the structured glossary in place so callers see the cleaned form.
        _validate_client_adaptation(self.client_adaptation_text)
        self.client_adaptation_glossary = _validate_glossary(self.client_adaptation_glossary)
        # Reranker bounds: top_k_in 1..100; model name is a free string
        # (operators may point at fine-tuned variants); loading-time tests
        # reachability.
        if not isinstance(self.rerank_enabled, bool):
            raise ValueError(f"rerank_enabled must be bool, got {type(self.rerank_enabled).__name__}")
        if self.rerank_top_k_in < 1 or self.rerank_top_k_in > 100:
            raise ValueError(f"rerank_top_k_in must be in [1, 100], got {self.rerank_top_k_in}")
        if not isinstance(self.rerank_model, str) or not self.rerank_model.strip():
            raise ValueError("rerank_model must be a non-empty string")
        # Recall-ceiling contract: the dense leg's recall window IS the
        # reranker's maximum input, so a too-small ``rerank_top_k_in``
        # silently caps final recall to ``rerank_top_k_in`` no matter how
        # good the reranker is. The factor of 3 gives the reranker
        # meaningful headroom to reorder vs. the agent's per-search
        # ``default_limit``. Only enforced when rerank is enabled —
        # turning it off makes the window irrelevant.
        if self.rerank_enabled and self.rerank_top_k_in < 3 * self.default_limit:
            raise ValueError(
                f"rerank_top_k_in ({self.rerank_top_k_in}) must be at least "
                f"3 * default_limit ({3 * self.default_limit}) when rerank is "
                f"enabled. Otherwise the reranker has no headroom to reorder "
                f"and recall is silently capped at top_k_in."
            )

    def would_invalidate_vectors(self, other: "KnowledgeConfig") -> bool:
        """Returns True if changing self -> other would invalidate the
        existing vector index.

        Used by the PATCH-config UX layer to require explicit confirmation
        before a profile switch that forces re-ingest. The check is the
        same hash that ``vector_config_hash`` computes; we re-expose it
        here so callers don't have to recompute on both sides.
        """
        return self.vector_config_hash() != other.vector_config_hash()

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

        # If the caller is naming a profile, apply that profile's values to
        # the merged base BEFORE the rest of ``incoming`` is layered in.
        # This is what makes the profile "own" embedding_model / chunking /
        # rerank / docling / engine defaults — a bare ``{"rag_profile": "X"}``
        # PATCH must produce the X-profile config, not just rename the label.
        # The same PATCH may still pin individual overrides (e.g.
        # ``{"rag_profile": "balanced", "chunk_size": 1200}``); those land in
        # the loop below and supersede the profile.
        # We always apply when the key is present (rather than only when
        # switching) because ``base`` may be a bare ``KnowledgeConfig()``
        # whose default ``rag_profile`` field does NOT reflect the profile
        # TOML's values — only this code path resolves that.
        incoming_profile = incoming.get("rag_profile")
        if incoming_profile and isinstance(incoming_profile, str) and incoming_profile in VALID_PROFILES:
            try:
                pdata = load_profile(incoming_profile)
            except Exception as e:
                logger.warning(
                    "Profile switch to %r requested but TOML load failed: %s",
                    incoming_profile,
                    e,
                )
                pdata = {}
            p_emb = pdata.get("embeddings", {})
            p_chk = pdata.get("chunking", {})
            p_doc = pdata.get("docling", {})
            p_rrk = pdata.get("rerank", {})
            p_eng = pdata.get("engine", {})
            p_srh = pdata.get("search", {})
            for prof_key, target_key in (
                ("model", "embedding_model"),
                ("batch_size", "embedding_batch_size"),
                ("concurrency", "embedding_concurrency"),
            ):
                if prof_key in p_emb:
                    merged[target_key] = p_emb[prof_key]
            for prof_key, target_key in (
                ("chunk_size", "chunk_size"),
                ("chunk_overlap", "chunk_overlap"),
            ):
                if prof_key in p_chk:
                    merged[target_key] = p_chk[prof_key]
            for prof_key, target_key in (
                ("pdf_mode", "docling_pdf_mode"),
                ("layout_engine", "docling_layout_engine"),
                ("drop_page_chrome", "docling_drop_page_chrome"),
            ):
                if prof_key in p_doc:
                    merged[target_key] = p_doc[prof_key]
            for prof_key, target_key in (
                ("enabled", "rerank_enabled"),
                ("top_k_in", "rerank_top_k_in"),
                ("model", "rerank_model"),
            ):
                if prof_key in p_rrk:
                    merged[target_key] = p_rrk[prof_key]
            for prof_key, target_key in (
                ("max_ingest_workers", "max_ingest_workers"),
                ("vector_insert_batch_size", "vector_insert_batch_size"),
            ):
                if prof_key in p_eng:
                    merged[target_key] = p_eng[prof_key]
            for prof_key, target_key in (
                ("default_limit", "default_limit"),
                ("default_score_threshold", "default_score_threshold"),
                ("max_search_attempts", "max_search_attempts"),
                ("hybrid_mode", "search_hybrid_mode"),
                ("junk_filter", "search_junk_filter"),
                ("query_transform", "search_query_transform"),
                ("query_transform_n", "search_query_transform_n"),
            ):
                if prof_key in p_srh:
                    merged[target_key] = p_srh[prof_key]

        for k, v in incoming.items():
            if k not in known:
                continue
            # dict targets (e.g. embedding_extra_params) skip primitive coercion.
            if k == "embedding_extra_params":
                if v is None:
                    merged[k] = {}
                elif isinstance(v, dict):
                    merged[k] = dict(v)
                else:
                    raise ValueError(f"embedding_extra_params must be a dict, got {type(v).__name__}")
                continue
            # client_adaptation_text: normalize Unicode (NFC) so smart quotes,
            # combining accents, and other copy-paste artifacts from Word /
            # Slack don't produce mystery diffs; treat JSON null as "clear".
            if k == "client_adaptation_text":
                if v is None:
                    merged[k] = ""
                else:
                    merged[k] = unicodedata.normalize("NFC", str(v))
                continue
            # client_adaptation_glossary: validate-and-normalize structure
            # here so the merged dict carries the cleaned form straight into
            # the dataclass. None / empty / missing → empty list.
            if k == "client_adaptation_glossary":
                merged[k] = _validate_glossary(v)
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

        # HuggingFace requires extra deps. Never silently migrate — that hides
        # the user's choice and makes "I picked HF but got fastembed" debug-impossible.
        # Surface the missing-dep error at validate() time below, not here.

        # The frontend Select offers an "Auto-detect" option that submits
        # ``embedding_provider="auto"`` (KnowledgeConfig.tsx). The backend
        # has no auto provider; treat it as the documented default
        # ``fastembed`` so partial PATCHes (e.g. changing only docling_pdf_mode)
        # don't get rejected with "Unknown embedding_provider: auto".
        if merged.get("embedding_provider") == "auto":
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
        docling = kb.get("docling", {})
        adaptation = kb.get("client_adaptation", {})

        persist_dir_str = kb.get("persist_dir", "")
        persist_dir = Path(persist_dir_str) if persist_dir_str else Path.cwd() / ".cuga" / "knowledge"

        # Profile-owned defaults. The profile TOML is the source of truth
        # for these keys; settings.toml [knowledge.<section>] values act as
        # a fallback when the profile leaves a key unset. (To override the
        # profile per-key, the operator should edit the profile TOML or
        # ship a custom profile — settings.toml is intentionally the
        # fallback so switching ``rag_profile`` actually changes behavior.)
        # Owned by profile: embedding_model + batch_size + concurrency,
        # chunk_size + overlap, docling.* (pdf_mode, layout_engine,
        # drop_page_chrome), rerank.* (enabled, top_k_in, model),
        # search.* (default_limit, default_score_threshold,
        # max_search_attempts, hybrid_mode, junk_filter), engine.*
        # (max_ingest_workers, vector_insert_batch_size).
        profile_name = search.get("rag_profile", "standard")
        try:
            profile_data = load_profile(profile_name)
            profile_search = profile_data.get("search", {})
            profile_chunking = profile_data.get("chunking", {})
            profile_embeddings = profile_data.get("embeddings", {})
            profile_docling = profile_data.get("docling", {})
            profile_rerank = profile_data.get("rerank", {})
            profile_engine = profile_data.get("engine", {})
        except Exception as e:
            logger.warning(f"Failed to load profile '{profile_name}' at startup: {e}")
            profile_search = {}
            profile_chunking = {}
            profile_embeddings = {}
            profile_docling = {}
            profile_rerank = {}
            profile_engine = {}

        rerank_kb = kb.get("rerank", {})

        return cls(
            enabled=kb.get("enabled", False),
            agent_level_enabled=kb.get("agent_level_enabled", True),
            session_level_enabled=kb.get("session_level_enabled", True),
            persist_dir=persist_dir,
            embedding_provider=embeddings.get("provider", "fastembed"),
            embedding_model=profile_embeddings.get("model", embeddings.get("model", "")),
            embedding_api_key=embeddings.get("api_key", ""),
            embedding_base_url=embeddings.get("base_url", ""),
            use_gpu=embeddings.get("use_gpu", True),
            gpu_required=embeddings.get("gpu_required", False),
            embedding_extra_params=dict(embeddings.get("extra_params", {}) or {}),
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
            # CUGA_MAX_INGEST_WORKERS env wins so CLI/container ops can tune
            # ingest concurrency without editing TOML. Bad value fails loudly
            # at startup (int cast + the >=1 check in __post_init__).
            max_ingest_workers=int(
                os.environ.get("CUGA_MAX_INGEST_WORKERS")
                or profile_engine.get("max_ingest_workers", engine.get("max_ingest_workers", 2))
            ),
            max_pending_tasks=engine.get("max_pending_tasks", 10),
            embedding_batch_size=profile_embeddings.get("batch_size", embeddings.get("batch_size", 64)),
            embedding_concurrency=profile_embeddings.get("concurrency", embeddings.get("concurrency", 4)),
            vector_insert_batch_size=profile_engine.get(
                "vector_insert_batch_size", engine.get("vector_insert_batch_size", 200)
            ),
            docling_pdf_mode=profile_docling.get("pdf_mode", docling.get("pdf_mode", "accurate")),
            docling_layout_engine=profile_docling.get("layout_engine", docling.get("layout_engine", "auto")),
            docling_drop_page_chrome=profile_docling.get(
                "drop_page_chrome", docling.get("drop_page_chrome", "enforce")
            ),
            # Default must match the dataclass field (``"enforce"``, see
            # the field definition above). Earlier this defaulted to
            # ``"dry_run"``, which silently downgraded enforcement when a
            # profile omitted the key or profile loading failed. Closes
            # CodeRabbit M2.
            search_junk_filter=profile_search.get("junk_filter", search.get("junk_filter", "enforce")),
            search_hybrid_mode=profile_search.get("hybrid_mode", search.get("hybrid_mode", "auto")),
            search_query_transform=profile_search.get(
                "query_transform", search.get("query_transform", "off")
            ),
            search_query_transform_n=profile_search.get(
                "query_transform_n", search.get("query_transform_n", 3)
            ),
            rerank_enabled=profile_rerank.get("enabled", rerank_kb.get("enabled", False)),
            rerank_top_k_in=profile_rerank.get("top_k_in", rerank_kb.get("top_k_in", 20)),
            rerank_model=profile_rerank.get("model", rerank_kb.get("model", "BAAI/bge-reranker-base")),
            max_upload_size_mb=limits.get("max_upload_size_mb", 100),
            max_url_download_size_mb=limits.get("max_url_download_size_mb", 50),
            max_files_per_request=limits.get("max_files_per_request", 10),
            max_chunks_per_document=limits.get("max_chunks_per_document", 10000),
            mcp_transport=mcp.get("transport", "http"),
            mcp_port=mcp.get("port", 8113),
            # Accept both the structured [knowledge.client_adaptation] table and
            # the flat top-level key. The flat form is what to_dict() emits, so
            # an exported published snapshot inlined into settings.toml round-trips
            # without operator surgery.
            client_adaptation_text=adaptation.get("text", kb.get("client_adaptation_text", "")),
            # Audit-finding B3 fix: previously the glossary was silently
            # dropped on settings.toml round-trip. Now also accept it from the
            # structured ``[knowledge.client_adaptation.glossary]`` table or
            # the flat top-level ``client_adaptation_glossary`` key. The list
            # is normalized through ``_validate_glossary`` so each entry has
            # the canonical shape (definition defaulted to "", aliases
            # deduped) — matches what ``coerce_and_validate`` produces, so
            # round-trip is lossless from either entry point.
            client_adaptation_glossary=_validate_glossary(
                adaptation.get("glossary")
                if adaptation.get("glossary") is not None
                else kb.get("client_adaptation_glossary", [])
            ),
        )
