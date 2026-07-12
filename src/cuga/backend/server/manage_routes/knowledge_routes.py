"""Knowledge config endpoints and draft knowledge PATCH."""

from typing import Any, Optional

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from loguru import logger

from cuga.backend.server.manage_routes.router import router

import asyncio

from cuga.backend.server.manage_routes.helpers import (
    agent_draft_lock,
    is_secret_field_name,
    save_draft_section_unlocked,
)
from cuga.backend.server.manage_routes.knowledge_reindex import (
    migrate_and_reindex_for_agent,
    persist_active_vector_config,
)


@router.post("/knowledge/test_embeddings")
async def test_embeddings_connection(request: Request):
    """Round-trip a single embed call to validate connectivity + auth + model.

    Body: { provider, model, api_key, base_url, extra_params }
    Returns: { ok: bool, dim?: int, latency_ms?: int, error?: str, error_class?: str }

    Used by the UI's 'Test connection' button — surfaces failures BEFORE save,
    rather than 30s into an ingest. Times out at 10s.
    """
    import asyncio as _asyncio
    import time as _time

    body = await request.json()
    provider = (body.get("provider") or "").strip()
    model = (body.get("model") or "").strip()
    api_key = (body.get("api_key") or "").strip()
    base_url = (body.get("base_url") or "").strip()
    extra_params = body.get("extra_params") or {}
    if not provider:
        raise HTTPException(status_code=400, detail="provider is required")

    from cuga.backend.knowledge.config import KnowledgeConfig
    from cuga.backend.knowledge.engine import create_embeddings
    from pathlib import Path
    import shutil
    import tempfile

    # Build a throwaway config; reuse the same factory the live engine uses so
    # the test path matches reality. The temp dir is removed in the outer
    # finally so a Test Connection call (incl. validation failures / timeouts)
    # doesn't leak a directory each time.
    tmp_dir = Path(tempfile.mkdtemp(prefix="cuga-test-emb-"))
    try:
        try:
            cfg = KnowledgeConfig(
                enabled=True,
                persist_dir=tmp_dir,
                embedding_provider=provider,
                embedding_model=model,
                embedding_api_key=api_key,
                embedding_base_url=base_url,
                embedding_extra_params=dict(extra_params),
            )
            cfg.validate()
        except (ValueError, TypeError):
            logger.exception("Knowledge embedding test config validation failed")
            return JSONResponse(
                {
                    "ok": False,
                    "error_class": "InvalidEmbeddingConfiguration",
                    "error": "Invalid knowledge embedding configuration. Check provider, model, base URL, and extra parameters.",
                }
            )

        def _do_test() -> dict[str, Any]:
            t0 = _time.monotonic()
            try:
                emb = create_embeddings(cfg)
                vec = emb.embed_query("connection test")
                dt_ms = int((_time.monotonic() - t0) * 1000)
                return {"ok": True, "dim": len(vec), "latency_ms": dt_ms}
            except Exception:
                logger.exception("Knowledge embedding connection test failed")
                return {
                    "ok": False,
                    "error_class": "EmbeddingConnectionFailed",
                    "error": "Embedding connection test failed. Check the base URL, model, and credentials.",
                }

        try:
            result = await _asyncio.wait_for(_asyncio.to_thread(_do_test), timeout=10.0)
            return JSONResponse(result)
        except _asyncio.TimeoutError:
            return JSONResponse(
                {
                    "ok": False,
                    "error_class": "Timeout",
                    "error": "Embedding call did not complete within 10 seconds. "
                    "Check the base URL is reachable and the model is correct.",
                }
            )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@router.get("/knowledge/defaults")
async def get_knowledge_defaults():
    """Return the factory-default knowledge config (dataclass defaults).

    Used by the UI's per-section "Reset to defaults" buttons. Returns the
    same shape that ``KnowledgeConfig().to_dict(include_secrets=False)``
    produces — secrets are blank, persist_dir is excluded. Crucially this is
    NOT the user's settings.toml view — it's the canonical project defaults,
    so reset semantics are predictable regardless of deployment overrides.
    """
    try:
        from cuga.backend.knowledge.config import KnowledgeConfig

        defaults = KnowledgeConfig().to_dict(include_secrets=False)
        # Drop internal fields (anything underscore-prefixed) so the UI
        # doesn't have to know about them.
        public = {k: v for k, v in defaults.items() if not k.startswith("_")}
        return JSONResponse({"defaults": public})
    except Exception as e:
        logger.error(f"Failed to compute knowledge defaults: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/knowledge/env-presets")
async def get_knowledge_env_presets():
    """Detected embedding-provider presets based on environment variables.

    Lets the UI offer "one-click apply" for providers whose credentials
    are already in the host's environment (``.env`` or shell). Returns
    ONLY booleans + suggested config — NEVER the actual env values, so
    the response is safe to surface in any logged-out or shared UI
    context. The "Apply" action on the UI side just sets
    embedding_provider + embedding_model and leaves embedding_api_key
    empty; the engine + LiteLLM then read the matching env var at
    embed-time.
    """
    import os as _os

    # Slot semantics: each entry in ``required_env`` / ``optional_env`` is
    # a single var name OR a pipe-separated alias group ("A|B"). The slot
    # is satisfied if ANY alias is set. Lets us collapse the old watsonx
    # special case (WATSONX_URL OR WATSONX_API_BASE) AND accept the
    # WATSONX_APIKEY / WATSONX_API_KEY spelling variation LiteLLM itself
    # accepts upstream.
    def _aliases(spec: str) -> list[str]:
        return spec.split("|")

    def _env_set(name: str) -> bool:
        v = (_os.environ.get(name) or "").strip()
        if not v:
            return False
        # Reject angle-bracket placeholders ("<your-key>") — common in .env
        # templates and would falsely flag the slot as ready.
        return not (v.startswith("<") and v.endswith(">"))

    def _slot_set(spec: str) -> bool:
        return any(_env_set(n) for n in _aliases(spec))

    def _slot_first_value(spec: str) -> str:
        for n in _aliases(spec):
            if _env_set(n):
                return (_os.environ.get(n) or "").strip()
        return ""

    # Local alias to ``is_secret_field_name`` (top of file). One rule
    # across env-presets / GET-redactor / PATCH-preserver — adding a
    # sixth substring updates all three call sites.
    _is_secret = is_secret_field_name

    PROVIDER_PRESETS = [
        {
            "id": "openai",
            "label": "OpenAI",
            "required_env": ["OPENAI_API_KEY"],
            "optional_env": ["OPENAI_BASE_URL"],
            "default_provider": "openai",
            "default_model": "text-embedding-3-small",
        },
        {
            "id": "openrouter",
            "label": "OpenRouter",
            "required_env": ["OPENROUTER_API_KEY"],
            "optional_env": [],
            "default_provider": "openrouter",
            "default_model": "openai/text-embedding-3-small",
        },
        {
            "id": "watsonx",
            "label": "IBM Watsonx (via LiteLLM)",
            # Aliases: LiteLLM accepts both APIKEY and API_KEY for Watsonx
            # creds AND either URL or API_BASE for the endpoint. Slot-level
            # alias support eliminates the old watsonx special case.
            "required_env": [
                "WATSONX_APIKEY|WATSONX_API_KEY",
                "WATSONX_PROJECT_ID",
                "WATSONX_URL|WATSONX_API_BASE",
            ],
            "optional_env": [],
            "default_provider": "litellm",
            # intfloat/multilingual-e5-large is a stronger general-purpose
            # default than the prior IBM slate-30m (multilingual coverage,
            # better OOTB retrieval quality on enterprise corpora).
            "default_model": "watsonx/intfloat/multilingual-e5-large",
        },
        {
            "id": "azure",
            "label": "Azure OpenAI (via LiteLLM)",
            "required_env": ["AZURE_API_KEY", "AZURE_API_BASE"],
            "optional_env": ["AZURE_API_VERSION"],
            "default_provider": "litellm",
            "default_model": "azure/text-embedding-3-small",
        },
        {
            "id": "cohere",
            "label": "Cohere (via LiteLLM)",
            "required_env": ["COHERE_API_KEY"],
            "optional_env": [],
            "default_provider": "litellm",
            "default_model": "cohere/embed-english-v3.0",
        },
        # Broader provider coverage for "many companies" — each ships
        # hidden by the UI's row filter (no env vars set → no row).
        {
            "id": "gemini",
            "label": "Google Gemini (via LiteLLM)",
            "required_env": ["GEMINI_API_KEY"],
            "optional_env": [],
            "default_provider": "litellm",
            "default_model": "gemini/text-embedding-004",
        },
        {
            "id": "voyage",
            "label": "Voyage AI (via LiteLLM)",
            "required_env": ["VOYAGE_API_KEY"],
            "optional_env": [],
            "default_provider": "litellm",
            "default_model": "voyage/voyage-3",
        },
        {
            "id": "mistral",
            "label": "Mistral AI (via LiteLLM)",
            "required_env": ["MISTRAL_API_KEY"],
            "optional_env": [],
            "default_provider": "litellm",
            "default_model": "mistral/mistral-embed",
        },
        {
            "id": "togetherai",
            "label": "Together AI (via LiteLLM)",
            "required_env": ["TOGETHERAI_API_KEY"],
            "optional_env": [],
            "default_provider": "litellm",
            "default_model": "together_ai/BAAI/bge-large-en-v1.5",
        },
        {
            "id": "jina",
            "label": "Jina AI (via LiteLLM)",
            "required_env": ["JINA_AI_API_KEY"],
            "optional_env": [],
            "default_provider": "litellm",
            "default_model": "jina_ai/jina-embeddings-v3",
        },
    ]

    presets = []
    for p in PROVIDER_PRESETS:
        all_slots = p["required_env"] + p["optional_env"]

        # env_vars: every alias name (including unfilled ones) so the UI
        # can show which specific spelling was found.
        env_vars: dict[str, bool] = {}
        # env_values: ONLY non-secret vars that are actually set. Surfaces
        # the URL / region / project-id / base path the UI needs to render
        # "what was detected" alongside the row. Credential material
        # (KEY / TOKEN / SECRET / PASSWORD / APIKEY) is filtered out.
        env_values: dict[str, str] = {}
        for slot in all_slots:
            for name in _aliases(slot):
                is_set = _env_set(name)
                env_vars[name] = is_set
                if is_set and not _is_secret(name):
                    env_values[name] = (_os.environ.get(name) or "").strip()

        ready = all(_slot_set(s) for s in p["required_env"])
        # Surface the canonical (first) name for each unset required slot.
        missing = [_aliases(s)[0] for s in p["required_env"] if not _slot_set(s)]

        presets.append(
            {
                "id": p["id"],
                "label": p["label"],
                "default_provider": p["default_provider"],
                "default_model": p["default_model"],
                "ready": ready,
                "env_vars": env_vars,
                "env_values": env_values,
                "missing": missing,
            }
        )

    # Local providers — no env detection needed; always exposed so the
    # UI can show them in the "always available" section.
    always_available = [
        {
            "id": "fastembed",
            "label": "Fastembed (local, default)",
            "default_provider": "fastembed",
            "default_model": "BAAI/bge-small-en-v1.5",
        },
        {
            "id": "ollama",
            "label": "Ollama (local)",
            "default_provider": "ollama",
            "default_model": "nomic-embed-text",
        },
    ]

    return JSONResponse({"presets": presets, "always_available": always_available})


@router.get("/knowledge/accelerator")
async def get_knowledge_accelerator(request: Request):
    """Live hardware acceleration status for the running knowledge engine.

    Returns what the user *requested* (use_gpu flag) alongside what the
    runtime *actually loaded* — so UI can flag silent CPU fallbacks.
    """
    try:
        app_state = getattr(request.app.state, "app_state", None)
        engine = getattr(app_state, "knowledge_engine", None) if app_state else None
        if engine is None:
            return JSONResponse(
                {"available": False, "reason": "knowledge engine not initialized"},
                status_code=200,
            )
        status = engine.accelerator_status()
        return JSONResponse({"available": True, **status})
    except Exception as e:
        logger.error(f"Failed to read accelerator status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/config/draft/knowledge")
async def patch_draft_knowledge(request: Request, agent_id: Optional[str] = None):
    """Update knowledge section of draft AND apply it to the live engine.

    Knowledge config differs from tools/LLM/policies in one important way: it
    affects how the SAME documents are parsed, embedded, and stored. There's
    no "preview" semantic where you'd want the draft and live to diverge — a
    user who sets ``docling_pdf_mode = "fast"`` expects the next upload to
    use fast mode, full stop.

    So we save to draft (for crash recovery + publish snapshot) AND apply
    to the live engine immediately. Cheap fields (docling mode, batch sizes,
    rag_profile) skip the preflight by design in
    ``KnowledgeEngine.prepare_knowledge_update``; only embedding
    provider/model changes pay the dim-probe cost.

    If the live apply fails (e.g. a bad embedding key fails preflight),
    we return 400 and DO NOT save the draft — the saved-but-not-applied
    case was the previous bug.
    """
    if agent_id is None:
        agent_id = "cuga-default"
    # NOTE: previously called ``await request.is_disconnected()`` here as
    # Slice A telemetry. That call invokes ``self._receive()`` to peek at
    # the ASGI channel, which can CONSUME the first body chunk and
    # leave ``await request.json()`` below blocked indefinitely waiting
    # for a chunk that's already been eaten. Symptom: PATCH hangs at
    # body.read until the client times out with ClientDisconnect.
    # Removed — Slice B (engine generation counter) doesn't need this
    # telemetry, and FastAPI surfaces real disconnects via the normal
    # exception path anyway.
    try:
        from cuga.backend.server.config_store import _parse_agent_id
        from cuga.backend.tools_env.registry.utils.api_utils import get_registry_base_url

        data = await request.json()
        knowledge = data.get("knowledge", data)
        if not isinstance(knowledge, dict):
            raise HTTPException(status_code=400, detail="knowledge must be a dict")

        # NOTE (#396): the over-broad route-level Layer-1 pre-check that used to
        # live here — rejecting ANY PATCH while a reindex was in flight — was
        # REMOVED. It false-positived on a redundant no-op / non-vector PATCH
        # (the debounced autosave that races a Save & Reindex click), surfacing a
        # spurious "Couldn't save". The engine's precise guard
        # (apply_knowledge_config raises ReindexInProgressError only on a
        # VECTOR-affecting change) is the single source of truth and is mapped to
        # a structured 409 below; a non-vector PATCH correctly passes through to 200.

        # Imports needed inside the lock.
        from cuga.backend.server.config_store import load_draft
        from cuga.backend.knowledge.config import KnowledgeConfig
        from cuga.backend.knowledge.engine import ReindexInProgressError
        from dataclasses import fields as _dc_fields
        from cuga.backend.knowledge.config import (
            ClientAdaptationError,
            client_adaptation_hash,
            client_glossary_hash,
        )

        # The READ-MERGE-VALIDATE-APPLY-SAVE sequence below MUST run inside
        # the per-agent lock — otherwise two concurrent same-section PATCHes
        # both read the pre-PATCH draft, both compute their own ``filtered``
        # against stale ``existing_knowledge``, and the later writer's save
        # wipes the earlier's section change (cross-section + same-section
        # LMW races are both closed by this single critical section).
        # ``save_draft_section_unlocked`` is used inside because asyncio.Lock
        # is non-reentrant.
        live_state = getattr(request.app.state, "app_state", None)
        live_engine = getattr(live_state, "knowledge_engine", None) if live_state else None
        live_apply_result: dict[str, Any] | None = None
        async with agent_draft_lock(str(agent_id)):
            existing_draft = await load_draft(agent_id) or {}
            existing_knowledge = existing_draft.get("knowledge", {})

            # Preserve stored secrets on empty incoming (GET redacts to "";
            # naive merge would wipe). Explicit non-empty overwrites normally.
            for _k in list(knowledge.keys()):
                if is_secret_field_name(_k) and knowledge[_k] == "":
                    knowledge.pop(_k, None)

            merged = {**existing_knowledge, **knowledge}
            known_fields = {f.name for f in _dc_fields(KnowledgeConfig)} - {"persist_dir"}
            filtered = {k: v for k, v in merged.items() if k in known_fields}

            # Capture pre-patch hashes for audit-log diff (B2 finding —
            # glossary changes used to be invisible).
            _prev_adapt_hash = client_adaptation_hash(existing_knowledge.get("client_adaptation_text", ""))
            _prev_gloss_hash = client_glossary_hash(existing_knowledge.get("client_adaptation_glossary", []))

            # Validate via shared helper (same coercion + validation as engine apply).
            # ClientAdaptationError carries a machine-readable code + detail dict so
            # the UI can render specific affordances per failure mode.
            try:
                validated = KnowledgeConfig.coerce_and_validate(filtered)
            except ClientAdaptationError as cae:
                raise HTTPException(status_code=422, detail=cae.to_dict())
            except (ValueError, TypeError) as ve:
                raise HTTPException(status_code=400, detail=str(ve))

            if live_engine is not None:
                try:
                    # ``apply_knowledge_config`` calls ``prepare_knowledge_update``
                    # which, on embedding-provider/model change, runs a synchronous
                    # ``embed_query("test")`` preflight — a network round-trip to
                    # the embeddings API. Without ``to_thread`` that round-trip
                    # blocks the event loop and stalls every other request for
                    # the duration. Per Sami's review (Dec 2026).
                    live_apply_result = await asyncio.to_thread(live_engine.apply_knowledge_config, filtered)
                    logger.info(
                        f"Live engine knowledge config applied: "
                        f"embedding_changed={live_apply_result.get('embedding_changed')}, "
                        f"chunking_changed={live_apply_result.get('chunking_changed')}, "
                        f"metric_changed={live_apply_result.get('metric_changed')}, "
                        f"reindex_recommended={live_apply_result.get('reindex_recommended')}"
                    )
                except (ValueError, TypeError) as ve:
                    raise HTTPException(status_code=400, detail=f"Engine validation failed: {ve}")
                except ReindexInProgressError as rip_err:
                    # Layer 2: engine refused a vector-affecting change while a
                    # reindex was in flight. Layer 1's 409 should have caught
                    # this earlier; reach this branch only if (a) the reindex
                    # started in the small window AFTER our pre-check, or (b)
                    # an SDK consumer reached apply_knowledge_config directly.
                    # Map to the same shape the FE handles for Layer 1.
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "error": "reindex_in_progress",
                            "collections": sorted(live_engine._reindex_in_progress),
                            "message": str(rip_err)
                            or "Re-index is running. Wait for it to finish before changing knowledge settings.",
                        },
                    )
                except Exception as live_err:
                    # Preflight network/auth errors land here (e.g. embedding API rejected).
                    # IMPORTANT: distinguish two cases:
                    #   (a) USER-supplied a key that the provider rejected — they
                    #       made an explicit choice and the choice is broken.
                    #       Block save, surface error.
                    #   (b) ENV-VAR fallback failed — the user didn't supply a key
                    #       (just switched provider, or relying on server env). The
                    #       failure is about deployment state, not the user's input.
                    #       Soft-fail: save the config, return 200 with a warning
                    #       so the UI can show a toast without blocking. The user
                    #       can fix it by entering their own key or running Test
                    #       connection.
                    # Read from ``filtered`` (the merged config), not the raw
                    # incoming body (Sami review): a redacted/empty key in the
                    # PATCH is dropped and the STORED key is preserved in
                    # ``filtered`` after merge. Reading ``knowledge`` here would
                    # misclassify a real user key as "no key" and wrongly take
                    # the env-var soft-fail path on a provider switch.
                    _user_supplied_key = bool((filtered.get("embedding_api_key") or "").strip())
                    _provider = (filtered.get("embedding_provider") or "").lower()
                    _is_credentialed = _provider in ("openai", "openrouter", "litellm")
                    _err_str = str(live_err)
                    _looks_like_auth = any(
                        s in _err_str
                        for s in (
                            "401",
                            "Unauthorized",
                            "Invalid API",
                            "Incorrect API",
                            "AuthenticationError",
                        )
                    )
                    if _is_credentialed and not _user_supplied_key and _looks_like_auth:
                        logger.warning(
                            f"Live engine preflight failed via env-var fallback (no user key "
                            f"supplied) — soft-failing so the user can continue editing: {live_err}"
                        )
                        live_apply_result = {
                            "embedding_changed": False,
                            "chunking_changed": False,
                            "metric_changed": False,
                            "reindex_recommended": False,
                            "dim_changed": False,
                            "previous_dim": None,
                            "new_dim": None,
                            "_preflight_warning": (
                                "Environment-variable API key was rejected by the provider. "
                                "Settings saved, but ingest will fail until you set a valid key "
                                "or fix the env var. Use Test connection to verify."
                            ),
                        }
                        # Do NOT raise — fall through to save the draft so the user
                        # can keep editing without the toast-storm.
                    else:
                        # Keep provider detail in the log only; don't echo it in
                        # the HTTP body — a provider error can carry a credentialed
                        # base_url / request context. Matches the sanitized 500s.
                        logger.warning(f"Live engine knowledge apply failed: {live_err!r}")
                        raise HTTPException(
                            status_code=400,
                            detail=(
                                "Live knowledge engine rejected the new config. "
                                "Check the embedding provider, key, and model and try again."
                            ),
                        ) from None

            # Save to draft (lock-free variant — we already hold the lock).
            # Post-apply ordering means we only persist configs the engine
            # accepted; the draft serves crash recovery + publish snapshot.
            full_draft = await save_draft_section_unlocked(agent_id, "knowledge", filtered)

            # ADOPT-EXISTING-COLLECTION: keep the ACTIVE pointer consistent with
            # the embedder we just applied. /documents + retrieval resolve via
            # app_state.knowledge_config_hash; if that stays on the OLD collection
            # after applying a config whose embedder maps to an ALREADY-BUILT
            # collection, the user sees the wrong (or zero) documents — the
            # "imported config, no documents" report. When the applied embedder's
            # collection already exists AND is populated, make it active
            # immediately (no reindex — vectors are already there with the
            # matching embedder). A NEW embedder (no collection yet) leaves the
            # pointer alone — the reindex + deferred flip builds and flips it.
            # Never flip while a reindex is in flight (the strict flip owns the
            # pointer then). Persist so it survives restart.
            try:
                if (
                    live_state is not None
                    and live_engine is not None
                    and not live_engine._reindex_in_progress
                ):
                    _adopt_hash = live_engine._config.vector_config_hash()
                    _cur_hash = getattr(live_state, "knowledge_config_hash", None)
                    if _adopt_hash and _adopt_hash != _cur_hash:
                        import re as _re_adopt

                        _adopt_base = f"kb_agent_{_re_adopt.sub(r'[^a-zA-Z0-9_]', '_', str(agent_id))}"
                        _adopt_coll = f"{_adopt_base}_{_adopt_hash}"
                        _existing_docs = await live_engine.list_documents(_adopt_coll)
                        if _existing_docs:
                            live_state.knowledge_config_hash = _adopt_hash
                            logger.info(
                                f"Adopted existing collection {_adopt_coll} ({len(_existing_docs)} docs) "
                                f"as active after config apply (hash {_cur_hash} -> {_adopt_hash}); "
                                f"no reindex needed."
                            )
                            await persist_active_vector_config(agent_id, live_engine, _adopt_hash)
                            if isinstance(live_apply_result, dict):
                                live_apply_result["reindex_recommended"] = False
                                live_apply_result["adopted_existing_collection"] = True
                                live_apply_result["active_document_count"] = len(_existing_docs)
            except Exception as _adopt_err:  # noqa: BLE001 — never break the PATCH
                logger.warning(f"Adopt-existing-collection check failed (non-fatal): {_adopt_err}")

        # Audit log: diff old vs new adaptation hash (NEVER the text itself —
        # PII + prompt-IP). Lets SREs answer "when did the adaptation change"
        # without snapshot-by-snapshot diffing.
        _new_adapt_hash = client_adaptation_hash(validated.client_adaptation_text)
        _new_gloss_hash = client_glossary_hash(validated.client_adaptation_glossary)
        if _prev_adapt_hash != _new_adapt_hash:
            logger.info(
                "cuga.knowledge.adaptation_patched",
                extra={
                    "cuga_knowledge_adaptation_old_hash": _prev_adapt_hash,
                    "cuga_knowledge_adaptation_new_hash": _new_adapt_hash,
                    "cuga_knowledge_adaptation_new_len": len(validated.client_adaptation_text),
                    "cuga_knowledge_agent_id": str(agent_id),
                    "cuga_knowledge_source": "patch_draft",
                },
            )
        if _prev_gloss_hash != _new_gloss_hash:
            logger.info(
                "cuga.knowledge.glossary_patched",
                extra={
                    "cuga_knowledge_glossary_old_hash": _prev_gloss_hash,
                    "cuga_knowledge_glossary_new_hash": _new_gloss_hash,
                    "cuga_knowledge_glossary_new_count": len(validated.client_adaptation_glossary),
                    "cuga_knowledge_agent_id": str(agent_id),
                    "cuga_knowledge_source": "patch_draft",
                },
            )

        # Store draft knowledge config so Try-It-Out can use it for search behavior.
        # Per-agent isolation: shared draft_app_state holds a DICT keyed by
        # base agent_id (stripped of any "--draft" suffix). The legacy
        # singular attribute is kept in sync for back-compat with any reader
        # that still expects it on the same instance.
        state = getattr(request.app.state, "draft_app_state", None)
        if state:
            try:
                base_agent_id_for_key = _parse_agent_id(str(agent_id))
                cfgs = getattr(state, "draft_knowledge_configs", None)
                if not isinstance(cfgs, dict):
                    cfgs = {}
                    state.draft_knowledge_configs = cfgs
                cfgs[base_agent_id_for_key] = validated
                # Back-compat shadow: keep singular attr writing the last patch
                # so any reader still using the singular name sees this agent's
                # data when it's the only configured agent.
                state.draft_knowledge_config = validated
            except Exception as draft_state_err:
                logger.warning(f"Failed to update draft knowledge app state: {draft_state_err}")
        if state:
            try:
                base_agent_id = _parse_agent_id(str(agent_id))
                draft_agent_id = f"{base_agent_id}--draft"
                registry_url = get_registry_base_url()
                async with httpx.AsyncClient() as client:
                    r = await client.post(f"{registry_url}/reload?agent_id={draft_agent_id}", timeout=10.0)
                    r.raise_for_status()
            except Exception as reload_err:
                logger.warning(f"Failed to reload registry for PATCH knowledge: {reload_err}")

            try:
                draft_agent = getattr(state, "agent", None)
                if draft_agent:
                    tp = getattr(draft_agent, "tool_provider", None)
                    if tp is not None and hasattr(tp, "reset"):
                        tp.reset()
                    llm_cfg = (full_draft or {}).get("llm") or {}
                    draft_agent.llm_config = llm_cfg if llm_cfg else None
                    await draft_agent.build_graph()
            except Exception as rebuild_err:
                logger.warning(f"Failed to rebuild draft agent graph after knowledge PATCH: {rebuild_err}")

        response: dict[str, Any] = {
            "status": "success",
            "version": "draft",
            "agent_id": agent_id,
            "live_applied": live_engine is not None,
        }
        if live_apply_result:
            # Expose the engine's change flags so the UI can show the
            # "reindex recommended" banner without an extra round-trip.
            response["live_changes"] = {
                k: live_apply_result.get(k)
                for k in (
                    "embedding_changed",
                    "chunking_changed",
                    "metric_changed",
                    "reindex_recommended",
                    "dim_changed",
                    "previous_dim",
                    "new_dim",
                    # Set when the applied config's collection was already built
                    # and adopted as active (no reindex needed). The UI uses
                    # these to clear the reindex banner + show the doc count.
                    "adopted_existing_collection",
                    "active_document_count",
                )
            }
            # If the engine soft-failed (e.g. bad env-var key on provider
            # switch), surface the warning so the UI can toast it without
            # blocking the save.
            _pf_warn = live_apply_result.get("_preflight_warning")
            if _pf_warn:
                response["preflight_warning"] = _pf_warn
            # When the embedding dim changed, existing vectors are no
            # longer compatible. We DO NOT auto-trigger reindex anymore —
            # users typically tweak several settings before they're
            # ready, and running migration+reindex on every PATCH burns
            # CPU and makes the UI feel hyperactive. Instead the UI
            # shows a "Re-index recommended" banner driven by
            # ``knowledgeReindexNeeded`` (snapshot diff in ManagePage) +
            # this response's ``auto_reindex.triggered=false`` signal.
            # The user clicks Re-index when they're done editing — that
            # call lands on ``POST /api/manage/knowledge/reindex_for_config``
            # which invokes ``migrate_and_reindex_for_agent``.
            if live_apply_result.get("dim_changed") and live_engine is not None:
                response["auto_reindex"] = {
                    "triggered": False,
                    "reason": "manual_required",
                    "dim_change": f"{live_apply_result.get('previous_dim')} -> {live_apply_result.get('new_dim')}",
                }
        return JSONResponse(response)
    except HTTPException:
        raise
    except Exception as e:
        # ``logger.exception`` captures the full traceback. The prior
        # ``logger.error(f"...: {e}")`` swallowed everything when
        # ``str(e)`` was empty (some libs raise bare Exception() with
        # no args) and left us no breadcrumb to diagnose. Include
        # repr(e) so we at least see the class name when str is empty.
        # Full detail (incl. embedding-API errors, paths, partial key material)
        # goes to the LOG only; the client gets a generic message (Sami review /
        # CodeQL — don't leak internals in the HTTP body).
        logger.exception(f"Failed to patch draft knowledge: {e!r}")
        raise HTTPException(
            status_code=500,
            detail="Failed to save knowledge settings. Check the server logs for details.",
        ) from None


@router.post("/knowledge/reindex_for_config")
async def reindex_for_config_change(request: Request, agent_id: Optional[str] = None):
    """User-triggered migration + reindex after a config change that requires it.

    Replaces the prior auto-trigger on the PATCH lifecycle: the user clicks
    "Re-index" in the UI when they're ready, and this endpoint migrates files
    to the current vector_config_hash dir + re-embeds with the active engine
    config. Returns the same ``{triggered, target, collections}`` shape the
    PATCH used to return so the frontend's existing reindex-tile arming code
    works without changes.
    """
    if agent_id is None:
        agent_id = "cuga-default"
    live_state = getattr(request.app.state, "app_state", None)
    live_engine = getattr(live_state, "knowledge_engine", None) if live_state else None
    if live_engine is None:
        raise HTTPException(status_code=503, detail="Knowledge engine not ready")

    # Reject if a reindex is already in flight for this agent. Without this,
    # a rapid double-click on Re-index hits engine.reindex's own busy check
    # and the migration helper maps it to a generic ``reindex_failed`` toast
    # — confusing for what is really a "please wait" condition. Same shape
    # as patch_draft_knowledge's Layer 1 guard so the FE can reuse handling.
    import re as _re_guard

    _sanitized_pre = _re_guard.sub(r"[^a-zA-Z0-9_]", "_", str(agent_id))
    _prefix_pre = f"kb_agent_{_sanitized_pre}"
    in_flight = sorted(
        c for c in live_engine._reindex_in_progress if c == _prefix_pre or c.startswith(f"{_prefix_pre}_")
    )
    if in_flight:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "reindex_in_progress",
                "collections": in_flight,
                "message": "Re-index is already running for this agent. Wait for it to finish.",
            },
        )

    try:
        # Serialize against a concurrent publish / PATCH for this agent — they
        # all mutate engine._config; the reindex must start under the same lock
        # so it can't interleave with a publish's commit (CR-E). The deferred
        # flip spawned inside is fire-and-forget and takes the lock later.
        async with agent_draft_lock(str(agent_id)):
            # Re-check now that we're serialized — two Re-index clicks (or a
            # concurrent publish) can both pass the pre-lock guard above; only
            # the first to acquire the lock should start a reindex.
            in_flight = sorted(
                c
                for c in live_engine._reindex_in_progress
                if c == _prefix_pre or c.startswith(f"{_prefix_pre}_")
            )
            if in_flight:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "reindex_in_progress",
                        "collections": in_flight,
                        "message": "Re-index is already running for this agent. Wait for it to finish.",
                    },
                )
            result = await migrate_and_reindex_for_agent(agent_id, live_engine, live_state)
        return JSONResponse(result)
    except Exception as e:
        # Generic client message; full detail (may include embedding-API
        # errors / paths) stays in the log only (Sami review / CodeQL).
        logger.exception(f"reindex_for_config_change failed: {e!r}")
        raise HTTPException(
            status_code=500,
            detail="Re-index could not be started. Check the server logs for details.",
        ) from None
