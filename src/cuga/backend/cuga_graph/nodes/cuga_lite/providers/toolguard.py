"""ToolGuarding tool provider decorator.

This module provides a provider-level ToolGuard integration seam. It wraps any
ToolProviderInterface implementation and returns guarded LangChain tools from
public tool access methods while keeping raw tools available for ToolGuard
delegate calls.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, Dict, List, Tuple

from langchain_core.tools import BaseTool, StructuredTool
from loguru import logger

from cuga.backend.cuga_graph.nodes.cuga_lite.providers.base import (
    AppDefinition,
    ToolProviderInterface,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.tracking.arguments import merge_tool_call_args


_METADATA_ATTRS = (
    "_app_name",
    "_operation_id",
    "_param_constraints",
    "_response_schemas",
)

_UNSET = object()


class ToolGuardingToolProvider(ToolProviderInterface):
    """Decorator that adds ToolGuard runtime enforcement to another provider."""

    @classmethod
    def ensure_wrapped(
        cls,
        provider: ToolProviderInterface,
        *,
        policy_storage: Any = None,
        cuga_folder: str = ".cuga",
        enabled: bool = True,
    ) -> "ToolGuardingToolProvider":
        """Return a ToolGuard wrapper for provider, avoiding double wrapping."""
        if isinstance(provider, cls):
            provider.configure(
                policy_storage=policy_storage,
                cuga_folder=cuga_folder,
                enabled=enabled,
            )
            return provider

        return cls(
            provider,
            policy_storage=policy_storage,
            cuga_folder=cuga_folder,
            enabled=enabled,
        )

    def __init__(
        self,
        base_provider: ToolProviderInterface,
        *,
        policy_storage: Any = None,
        cuga_folder: str = ".cuga",
        enabled: bool = True,
    ) -> None:
        self.base_provider = base_provider
        self.policy_storage = policy_storage
        self.cuga_folder = cuga_folder
        self.enabled = enabled

        self._runtime: Any = None
        self._runtime_initialized = False
        self._runtime_lock = asyncio.Lock()
        self._guarded_tools_cache: Dict[Tuple[str, str, int], BaseTool] = {}
        self._initialized = False

    @property
    def initialized(self) -> bool:
        """Expose initialization state without hiding the wrapped provider state."""
        if hasattr(self.base_provider, "initialized"):
            return bool(getattr(self.base_provider, "initialized"))
        return self._initialized

    @initialized.setter
    def initialized(self, value: bool) -> None:
        self._initialized = value
        if hasattr(self.base_provider, "initialized"):
            try:
                setattr(self.base_provider, "initialized", value)
            except Exception:
                logger.debug("Could not propagate initialized state to base provider", exc_info=True)

    async def initialize(self) -> None:
        """Initialize the wrapped provider."""
        await self.base_provider.initialize()
        self._initialized = True

    async def get_apps(self) -> List[AppDefinition]:
        """Delegate application discovery to the wrapped provider."""
        return await self.base_provider.get_apps()

    async def get_tools(self, app_name: str) -> List[BaseTool]:
        """Return guarded tools for one app."""
        raw_tools = await self.get_raw_tools(app_name)
        if not self.enabled:
            return raw_tools
        return [self._wrap_tool(tool, app_name) for tool in raw_tools]

    async def get_all_tools(self) -> List[BaseTool]:
        """Return guarded tools for all apps."""
        apps = await self.get_apps()
        tools: List[BaseTool] = []
        for app in apps:
            tools.extend(await self.get_tools(app.name))
        return tools

    async def get_raw_tools(self, app_name: str) -> List[BaseTool]:
        """Return raw unguarded tools for one app."""
        return await self.base_provider.get_tools(app_name)

    async def get_all_raw_tools(self) -> List[BaseTool]:
        """Return raw unguarded tools for all apps."""
        apps = await self.base_provider.get_apps()
        tools: List[BaseTool] = []
        for app in apps:
            tools.extend(await self.base_provider.get_tools(app.name))
        return tools

    def get_base_provider(self) -> ToolProviderInterface:
        """Return the wrapped provider."""
        return self.base_provider

    def unwrap(self) -> ToolProviderInterface:
        """Return the wrapped provider."""
        return self.base_provider

    def reset(self) -> None:
        """Reset wrapped provider state if supported and clear ToolGuard caches."""
        if not hasattr(self.base_provider, "reset"):
            raise AttributeError(f"{type(self.base_provider).__name__} does not support reset()")
        self.base_provider.reset()
        self.invalidate_toolguard_runtime()

    def add_tool(self, tool: BaseTool) -> None:
        """Add one tool to the wrapped provider if supported."""
        if not hasattr(self.base_provider, "add_tool"):
            raise AttributeError(f"{type(self.base_provider).__name__} does not support add_tool()")
        self.base_provider.add_tool(tool)
        self.invalidate_toolguard_runtime()

    def add_tools(self, tools: List[BaseTool]) -> None:
        """Add multiple tools to the wrapped provider if supported."""
        if not hasattr(self.base_provider, "add_tools"):
            for tool in tools:
                self.add_tool(tool)
            return
        self.base_provider.add_tools(tools)
        self.invalidate_toolguard_runtime()

    def configure(
        self,
        *,
        policy_storage: Any = _UNSET,
        cuga_folder: Any = _UNSET,
        enabled: Any = _UNSET,
    ) -> None:
        """Update ToolGuard configuration and invalidate stale runtime state when needed."""
        should_invalidate = False

        if policy_storage is not _UNSET and self.policy_storage is not policy_storage:
            self.policy_storage = policy_storage
            should_invalidate = True

        if cuga_folder is not _UNSET and self.cuga_folder != cuga_folder:
            self.cuga_folder = cuga_folder
            should_invalidate = True

        if enabled is not _UNSET and self.enabled != enabled:
            self.enabled = enabled
            should_invalidate = True

        if should_invalidate:
            self.invalidate_toolguard_runtime()

    def set_policy_storage(self, policy_storage: Any) -> None:
        """Attach or replace policy storage and clear stale runtime/cache state."""
        self.configure(policy_storage=policy_storage)

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable ToolGuard enforcement."""
        self.configure(enabled=enabled)

    def invalidate_toolguard_runtime(self) -> None:
        """Drop cached runtime and guarded wrappers.

        This method is intentionally synchronous so SDK policy mutation paths can
        call it without needing an event loop. Runtime resource cleanup is best
        effort because some cleanup APIs are async.
        """
        runtime = self._runtime
        if runtime is not None and hasattr(runtime, "invoker") and hasattr(runtime.invoker, "clear_cache"):
            runtime.invoker.clear_cache()

        self._runtime = None
        self._runtime_initialized = False
        self._guarded_tools_cache.clear()

    async def _get_or_create_toolguard_runtime(self) -> Any:
        """Lazily create and initialize ToolGuardRuntime if enforcement is available."""
        if not self.enabled:
            return None
        if self.policy_storage is None:
            return None
        if self._runtime_initialized:
            return self._runtime

        async with self._runtime_lock:
            if self._runtime_initialized:
                return self._runtime

            from cuga.backend.cuga_graph.policy.tool_guard.tool_guard_runtime import (
                ToolGuardRuntime,
            )

            kwargs = {
                "tool_provider": self,
                "enable_policies": True,
                "policy_storage": self.policy_storage,
            }

            # Support the planned cuga_folder-aware runtime without requiring
            # this wrapper to change again when ToolGuardRuntime grows that arg.
            signature = inspect.signature(ToolGuardRuntime)
            if "cuga_folder" in signature.parameters:
                kwargs["cuga_folder"] = self.cuga_folder

            runtime = ToolGuardRuntime(**kwargs)
            await runtime.initialize()

            self._runtime = runtime
            self._runtime_initialized = True
            return self._runtime

    def _wrap_tool(self, tool: BaseTool, app_name: str) -> BaseTool:
        """Wrap a raw LangChain tool with ToolGuard enforcement."""
        cache_key = (app_name, tool.name, id(tool))
        cached = self._guarded_tools_cache.get(cache_key)
        if cached is not None:
            return cached

        param_names = self._get_param_names(tool)
        tool_name = tool.name
        description = tool.description or ""

        async def guarded_tool_func(*args: Any, **kwargs: Any) -> Any:
            all_kwargs = merge_tool_call_args(args, kwargs, param_names)

            runtime = await self._get_or_create_toolguard_runtime()
            if runtime is not None:
                error = await runtime.guard_tool_call(
                    app_name=app_name,
                    function_name=tool_name,
                    arguments=all_kwargs,
                )
                if error:
                    return {
                        "error": f"Tool call blocked by policy: {error}",
                        "blocked_by_policy": True,
                        "policy_violation": True,
                        "tool": tool_name,
                        "app": app_name,
                    }

            result = await tool.ainvoke(all_kwargs)
            while inspect.isawaitable(result):
                result = await result
            return result

        def guarded_tool_func_sync(*args: Any, **kwargs: Any) -> Any:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return asyncio.run(guarded_tool_func(*args, **kwargs))
            raise RuntimeError(
                f"ToolGuard-wrapped tool '{tool_name}' was invoked synchronously while an event loop is running. "
                "Use ainvoke() for async execution."
            )

        guarded_tool_func.__name__ = tool_name
        guarded_tool_func.__doc__ = description
        guarded_tool_func_sync.__name__ = tool_name
        guarded_tool_func_sync.__doc__ = description

        wrapped = StructuredTool.from_function(
            func=guarded_tool_func_sync,
            coroutine=guarded_tool_func,
            name=tool_name,
            description=description,
            args_schema=getattr(tool, "args_schema", None),
        )

        self._copy_tool_metadata(source_tool=tool, target_tool=wrapped, app_name=app_name)
        self._guarded_tools_cache[cache_key] = wrapped
        return wrapped

    def _get_param_names(self, tool: BaseTool) -> List[str]:
        """Derive parameter names from a LangChain tool args schema."""
        args_schema = getattr(tool, "args_schema", None)
        if args_schema is None:
            return []

        model_fields = getattr(args_schema, "model_fields", None)
        if model_fields:
            return list(model_fields.keys())

        legacy_fields = getattr(args_schema, "__fields__", None)
        if legacy_fields:
            return list(legacy_fields.keys())

        return []

    def _copy_tool_metadata(self, source_tool: BaseTool, target_tool: BaseTool, app_name: str) -> None:
        """Copy metadata used by Lite tracking, prompt generation, and ToolGuard."""
        source_func = getattr(source_tool, "func", None)
        target_func = getattr(target_tool, "func", None)
        target_coroutine = getattr(target_tool, "coroutine", None)

        for attr in _METADATA_ATTRS:
            value = None
            found = False
            if source_func is not None and hasattr(source_func, attr):
                value = getattr(source_func, attr)
                found = True
            elif hasattr(source_tool, attr):
                value = getattr(source_tool, attr)
                found = True

            if not found and attr == "_app_name":
                value = app_name
                found = True

            if not found:
                continue

            for target in (target_func, target_coroutine, target_tool):
                if target is None:
                    continue
                try:
                    setattr(target, attr, value)
                except Exception:
                    logger.debug(
                        "Could not copy metadata attribute %s to %s",
                        attr,
                        type(target).__name__,
                    )


def ensure_toolguard_provider(
    provider: ToolProviderInterface,
    *,
    policy_storage: Any = None,
    cuga_folder: str = ".cuga",
    enabled: bool = True,
) -> ToolGuardingToolProvider:
    """Wrap a provider with ToolGuard, or reconfigure it if already wrapped."""
    return ToolGuardingToolProvider.ensure_wrapped(
        provider,
        policy_storage=policy_storage,
        cuga_folder=cuga_folder,
        enabled=enabled,
    )


def configure_toolguard_provider(
    provider: ToolProviderInterface,
    *,
    policy_storage: Any = _UNSET,
    cuga_folder: Any = _UNSET,
    enabled: Any = _UNSET,
) -> None:
    """Configure provider if it is ToolGuard-wrapped."""
    if isinstance(provider, ToolGuardingToolProvider):
        provider.configure(
            policy_storage=policy_storage,
            cuga_folder=cuga_folder,
            enabled=enabled,
        )


def invalidate_toolguard_provider(provider: ToolProviderInterface) -> None:
    """Invalidate ToolGuard runtime/cache if provider supports it."""
    if hasattr(provider, "invalidate_toolguard_runtime"):
        provider.invalidate_toolguard_runtime()


def unwrap_tool_provider(provider: ToolProviderInterface) -> ToolProviderInterface:
    """Return the base provider when provider is ToolGuard-wrapped."""
    if isinstance(provider, ToolGuardingToolProvider):
        return provider.get_base_provider()
    return provider
