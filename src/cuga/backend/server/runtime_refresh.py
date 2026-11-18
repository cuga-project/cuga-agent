"""Prepare isolated published runtimes and retire them after their last turn."""

import asyncio
from contextlib import aclosing, asynccontextmanager
from contextvars import ContextVar
from functools import wraps
from types import SimpleNamespace
from typing import Any

from loguru import logger

_turn_owners: ContextVar[list | None] = ContextVar("published_turn_owners", default=None)


def retain_runtime(owner: Any) -> None:
    if owner is None:
        return
    owners = _turn_owners.get()
    if owners is not None and not any(item is owner for item in owners):
        owners.append(owner)
        owner._active_published_turns = getattr(owner, "_active_published_turns", 0) + 1


async def close_retired_runtime(owner: Any) -> None:
    if not getattr(owner, "_runtime_retired", False) or getattr(owner, "_active_published_turns", 0):
        return
    if getattr(owner, "_runtime_closed", False):
        return
    owner._runtime_closed = True
    try:
        await owner.aclose()
    except Exception:
        logger.exception("Could not close retired published graph")
    storage = getattr(owner, "_runtime_policy_snapshot", None)
    if storage is not None:
        await storage.disconnect()


@asynccontextmanager
async def runtime_turn():
    owners = []
    token = _turn_owners.set(owners)
    try:
        yield
    finally:
        _turn_owners.reset(token)
        # Release every runtime before the first await: a cancellation during one
        # cleanup must not leave another runtime counting a turn that has ended.
        for owner in owners:
            owner._active_published_turns -= 1
        cleanups = [asyncio.ensure_future(close_retired_runtime(owner)) for owner in owners]
        if cleanups:
            await asyncio.shield(asyncio.gather(*cleanups, return_exceptions=True))


def runtime_stream(function):
    """Retain runtimes for every stream entry point, including direct /run calls."""

    @wraps(function)
    async def wrapped(*args, **kwargs):
        async with runtime_turn():
            async with aclosing(function(*args, **kwargs)) as stream:
                async for chunk in stream:
                    yield chunk

    return wrapped


async def prepare_runtime(state: Any, config: dict) -> Any:
    """Build policy and model state without changing the current runtime or env."""
    from cuga.backend.cuga_graph.policy.configurable import PolicyConfigurable
    from cuga.backend.cuga_graph.policy.storage import PolicyStorage
    from cuga.backend.cuga_graph.policy.utils import apply_policies_data_to_storage
    from cuga.backend.server.manage_routes.helpers import policies_list_from_config
    from cuga.backend.storage.policy.local import LocalPolicyStore
    from cuga.backend.llm.models import create_llm_from_config
    from cuga.config import settings

    prepared = SimpleNamespace(**vars(state))
    prepared.agent = None
    prepared.agent_id = getattr(state, "agent_id", "cuga-default")
    prepared.checkpointer = state.checkpointer
    tools = config.get("tools") or []
    prepared.tools_include_by_app = {
        tool["name"]: tool["include"]
        for tool in tools
        if tool.get("name") and isinstance(tool.get("include"), list) and tool["include"]
    } or None
    if getattr(getattr(settings, "secrets", None), "force_env", False):
        prepared.current_llm = None
    else:
        llm_cfg = config.get("llm") or {}
        try:
            prepared.current_llm = create_llm_from_config(llm_cfg)
        except Exception as exc:
            # Same fallback as apply_published_config: an unresolvable key must not
            # block startup or publish; requests then use the env/TOML model settings.
            logger.warning(
                "Failed to create LLM from published config (provider={} model={}): {}",
                llm_cfg.get("provider"),
                llm_cfg.get("model"),
                type(exc).__name__,
            )
            prepared.current_llm = None

    source = getattr(state, "policy_system", None)
    prepared.policy_system = source
    prepared.runtime_policy_snapshot = None
    if source is None:
        return prepared
    # Reuse the established policy backend, including its vector search implementation,
    # on a private in-memory database. No shared collection is cleared or repopulated.
    from cuga.backend.storage.policy.prod import ProdPolicyStore

    class SnapshotPolicyStore(LocalPolicyStore):
        async def disconnect(self) -> None:
            if self._store is not None:
                await self._store.close()
                self._store = None

    metric = "cosine" if isinstance(source.storage._backend, ProdPolicyStore) else "l2"
    storage = PolicyStorage(
        backend=SnapshotPolicyStore(":memory:", "runtime_policies", distance_metric=metric),
        embedding_dim=source.storage.embedding_dim,
    )
    storage._embedding_function = source.storage._embedding_function
    storage._embedding_initialized = True
    try:
        await storage.connect()
        await storage._create_collection()
        if config.get("policies") is not None:
            result = await apply_policies_data_to_storage(
                storage, policies_list_from_config(config["policies"]), clear_existing=False
            )
            if result["errors"]:
                raise ValueError("Could not prepare all published policies")
        else:
            # Configs that predate policy snapshots retain their current policy set.
            for policy in await source.storage.list_policies(enabled_only=False, limit=100000, strict=True):
                await storage.add_policy(policy)
        policy_system = PolicyConfigurable(storage=storage)
        policy_system.llm = source.llm
        policy_system.embedding_function = source.embedding_function
        await policy_system.initialize()
        prepared.policy_system = policy_system
        prepared.runtime_policy_snapshot = storage
        return prepared
    except BaseException:
        await storage.disconnect()
        raise
