"""Local manager startup and explicit provider validation."""

import os
from contextlib import asynccontextmanager, contextmanager

from fastapi import HTTPException


def manager_mode() -> bool:
    return os.environ.get("CUGA_GUIDED_SETUP", "").lower() in ("true", "1", "yes", "on")


@contextmanager
def guided_llm_context(config: dict):
    """Preserve the selected model and credential when manager saves rebuild a graph."""
    if not manager_mode():
        yield
        return
    from cuga.backend.llm.models import llm_config_context
    from cuga.backend.secrets.secret_resolver import secret_agent_context

    with llm_config_context(config), secret_agent_context("cuga-default"):
        yield


def knowledge_session_path(persist_dir):
    from pathlib import Path

    if manager_mode():
        return Path(persist_dir) / "session_knowledge.json"
    return Path.cwd() / ".cuga" / "session_knowledge.json"


def config_fingerprint(config: dict) -> str:
    import hashlib
    import json

    connection = {
        "provider": config.get("provider") or "openai",
        "model": config.get("model") or "",
        "base_url": config.get("base_url") or config.get("url") or "",
        "api_key": config.get("api_key") or "",
        "auth_type": config.get("auth_type") or "api_key",
        "auth_header_name": config.get("auth_header_name") or "Authorization",
    }
    return hashlib.sha256(json.dumps(connection, sort_keys=True).encode()).hexdigest()


def runtime_llm_fingerprint(config: dict) -> str:
    import hashlib
    import json

    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


async def validate_llm(config: dict, agent_id: str = "cuga-default") -> None:
    """Make one bounded inference request; never return provider errors or secrets."""
    import asyncio

    if not str(config.get("model") or "").strip():
        raise HTTPException(422, "Choose a model before testing the connection.")
    from cuga.backend.llm.models import create_llm_from_config
    from cuga.backend.secrets.secret_resolver import secret_agent_context

    try:
        with secret_agent_context(agent_id):
            model = create_llm_from_config({**config, "timeout": 20, "max_tokens": 32})
            await asyncio.wait_for(model.ainvoke("Reply with OK."), timeout=25)
    except Exception:
        raise HTTPException(
            400, "Connection test failed. Check the provider, model, endpoint and credential, then try again."
        ) from None


@asynccontextmanager
async def manager_lifespan(app_state, draft_state):
    """Serve configuration before any inference, browser or embedding initialization."""
    from cuga.backend.server.config_store import load_config
    from cuga.backend.server.managed_mcp import get_managed_mcp_path, write_managed_mcp_yaml

    config, version = await load_config(None)
    app_state.agent_id = "cuga-default"
    app_state.config_version = version
    write_managed_mcp_yaml(config or {}, get_managed_mcp_path())
    app_state.set_subsystem_status("policy", "starting", "Initialized when the first task starts")
    try:
        from cuga.backend.knowledge.config import KnowledgeConfig
        from cuga.backend.server.main import run_knowledge_startup

        knowledge = (config or {}).get("knowledge") or {}
        await run_knowledge_startup(
            app_state,
            KnowledgeConfig.coerce_and_validate(knowledge),
            init_fn=app_state.initialize_knowledge_engine,
        )
        yield
    finally:
        import asyncio

        for task in app_state.background_tasks:
            task.cancel()
        if app_state.background_tasks:
            await asyncio.gather(*app_state.background_tasks, return_exceptions=True)
        app_state.background_tasks.clear()
        engine = app_state.knowledge_engine
        if engine is not None:
            await engine.aclose()
            engine.shutdown()
        from cuga.backend.server.run_routes import close_graph_owners
        from cuga.backend.storage.facade import get_storage

        await close_graph_owners(
            app_state.agent,
            draft_state.agent,
            *app_state.agent_graphs_cache.values(),
            *getattr(app_state, "retired_guided_graphs", []),
        )
        app_state.agent_graphs_cache.clear()
        await get_storage().close_relational_stores()


async def ensure_default_agent(request, app_state, draft_state, use_draft):
    """Create the default graph only after the user has configured a model."""
    import asyncio

    state = draft_state if use_draft else app_state
    from cuga.backend.server.manage_routes.helpers import agent_draft_lock

    locks = app_state.agent_graph_build_locks
    lock = locks.setdefault(("cuga-default", use_draft), asyncio.Lock())
    async with lock, agent_draft_lock("cuga-default"):
        from cuga.backend.server.config_store import load_config, load_draft

        config = await load_draft() if use_draft else (await load_config(None))[0]
        llm = (config or {}).get("llm") or {}
        fingerprint = runtime_llm_fingerprint(llm)
        if state.agent is not None and getattr(state, "built_llm_fingerprint", None) == fingerprint:
            return state.agent
        if not llm.get("model"):
            raise HTTPException(409, "Complete provider setup in the manager before starting a task.")
        from cuga.backend.cuga_graph.entry_graph import CugaEntryGraph
        from cuga.backend.cuga_graph.nodes.cuga_lite.providers.combined import CombinedToolProvider
        from cuga.backend.cuga_graph.policy.configurable import create_agent_policy_system
        from cuga.backend.llm.models import create_llm_from_config, llm_config_context
        from cuga.backend.secrets.secret_resolver import secret_agent_context
        from cuga.backend.server.manage_routes.helpers import extract_agent_feature_overrides
        from cuga.backend.server.manage_routes.helpers import policies_list_from_config

        with llm_config_context(llm), secret_agent_context("cuga-default"):
            if os.getenv("CUGA_LOAD_POLICIES", "").lower() == "true":
                from cuga.configurations.instructions_manager import InstructionsManager

                InstructionsManager().set_instructions_from_one_file(os.getenv("CUGA_POLICIES_CONTENT", ""))
            policy = await create_agent_policy_system(
                agent_id="cuga-default",
                draft=use_draft,
                policies_data=policies_list_from_config((config or {}).get("policies")),
            )
            tools = (config or {}).get("tools") or []
            include = {t["name"]: t["include"] for t in tools if t.get("name") and t.get("include")} or None
            state.tools_include_by_app = include
            state.tools_include_version = 0
            graph = CugaEntryGraph(
                None,
                policy_system=policy,
                tool_provider=CombinedToolProvider(
                    get_include_by_app=lambda: (state.tools_include_by_app, state.tools_include_version),
                    agent_id="cuga-default--draft" if use_draft else "cuga-default",
                ),
                llm_config=llm,
                special_instructions=config.get("special_instructions") or None,
                **extract_agent_feature_overrides(config),
            )
            await graph.build_graph()
            state.current_llm = create_llm_from_config(llm)
        previous = state.agent
        state.policy_system = policy
        state.agent = graph
        state.built_llm_fingerprint = fingerprint
        if previous is not None:
            # Retain lifecycle ownership until shutdown: another stream may still
            # be using the previous graph while this configuration takes effect.
            retired = getattr(app_state, "retired_guided_graphs", None)
            if retired is None:
                retired = app_state.retired_guided_graphs = []
            retired.append(previous)
        return graph
