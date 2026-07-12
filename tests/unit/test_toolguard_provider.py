from __future__ import annotations

from typing import Any, List

import pytest
from langchain_core.tools import BaseTool, StructuredTool

from cuga.backend.cuga_graph.nodes.cuga_lite.providers.base import (
    AppDefinition,
    ToolProviderInterface,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.providers.toolguard import (
    ToolGuardingToolProvider,
    configure_toolguard_provider,
    ensure_toolguard_provider,
    unwrap_tool_provider,
)
from cuga.backend.cuga_graph.policy.models import ToolGuard, ToolGuide
from cuga.backend.cuga_graph.policy.tool_guard.tool_guard_policy_updates import merge_tool_guards
from cuga.backend.cuga_graph.policy.tool_guard.tool_guard_runtime import ToolGuardRuntime
from cuga.backend.cuga_graph.policy.tool_guard.tool_invoker import ToolGuardInvoker


class DummyProvider(ToolProviderInterface):
    def __init__(self, tools: List[BaseTool], app_name: str = "runtime_tools") -> None:
        self.tools = tools
        self.app_name = app_name
        self.initialized = False

    async def initialize(self) -> None:
        self.initialized = True

    async def get_apps(self) -> List[AppDefinition]:
        return [AppDefinition(name=self.app_name, type="langchain")]

    async def get_tools(self, app_name: str) -> List[BaseTool]:
        if app_name != self.app_name:
            return []
        return self.tools

    async def get_all_tools(self) -> List[BaseTool]:
        return self.tools

    def add_tool(self, tool: BaseTool) -> None:
        self.tools.append(tool)


class FakeRuntime:
    def __init__(self, error: str | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def guard_tool_call(
        self,
        app_name: str,
        function_name: str,
        arguments: dict[str, Any],
    ) -> str | None:
        self.calls.append(
            {
                "app_name": app_name,
                "function_name": function_name,
                "arguments": arguments,
            }
        )
        return self.error


class FailingGuardRuntime:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def guard_toolcall(self, **_: Any) -> None:
        raise self.error


class CapturingGuardRuntime:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def guard_toolcall(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


def _make_tool_guide(
    *,
    name: str = "Guard policy",
    tool_name: str = "book_flight",
    policy_code: str = "async def guard_book_flight(api, args):\n    return None\n",
    target_apps: list[str] | None = None,
) -> ToolGuide:
    return ToolGuide(
        id=name.lower().replace(" ", "-"),
        name=name,
        description="Test guard policy",
        triggers=[],
        target_tools=[tool_name],
        target_apps=target_apps,
        guide_content="",
        tool_guards={tool_name: ToolGuard(policy_code=policy_code)},
    )


def _make_recording_tool(calls: list[dict[str, Any]]) -> StructuredTool:
    def book_flight(user_id: str, flight_id: str, passengers: int) -> str:
        """Book a flight."""
        calls.append(
            {
                "user_id": user_id,
                "flight_id": flight_id,
                "passengers": passengers,
            }
        )
        return f"booked {flight_id}"

    tool = StructuredTool.from_function(book_flight)
    tool.func._app_name = "runtime_tools"
    tool.func._operation_id = "bookFlight"
    tool.func._param_constraints = {"passengers": ["max 3 for regular members"]}
    tool.func._response_schemas = {"success": {"type": "object"}}
    return tool


@pytest.mark.asyncio
async def test_toolguard_provider_delegates_and_exposes_raw_tools() -> None:
    calls: list[dict[str, Any]] = []
    raw_tool = _make_recording_tool(calls)
    base_provider = DummyProvider([raw_tool])
    provider = ToolGuardingToolProvider(base_provider, policy_storage=None)

    assert provider.initialized is False
    await provider.initialize()
    assert provider.initialized is True

    apps = await provider.get_apps()
    assert [app.name for app in apps] == ["runtime_tools"]

    raw_tools = await provider.get_raw_tools("runtime_tools")
    guarded_tools = await provider.get_tools("runtime_tools")

    assert raw_tools == [raw_tool]
    assert guarded_tools[0] is not raw_tool
    assert guarded_tools[0].name == raw_tool.name


@pytest.mark.asyncio
async def test_toolguard_provider_is_transparent_without_policy_storage() -> None:
    calls: list[dict[str, Any]] = []
    raw_tool = _make_recording_tool(calls)
    provider = ToolGuardingToolProvider(DummyProvider([raw_tool]), policy_storage=None)

    guarded_tool = (await provider.get_tools("runtime_tools"))[0]
    result = await guarded_tool.ainvoke({"user_id": "uid_1", "flight_id": "AB12", "passengers": 2})

    assert result == "booked AB12"
    assert calls == [{"user_id": "uid_1", "flight_id": "AB12", "passengers": 2}]


@pytest.mark.asyncio
async def test_toolguard_provider_allows_when_runtime_allows() -> None:
    calls: list[dict[str, Any]] = []
    raw_tool = _make_recording_tool(calls)
    provider = ToolGuardingToolProvider(DummyProvider([raw_tool]), policy_storage=object())
    runtime = FakeRuntime(error=None)

    async def fake_get_runtime() -> FakeRuntime:
        return runtime

    provider._get_or_create_toolguard_runtime = fake_get_runtime  # type: ignore[method-assign]

    guarded_tool = (await provider.get_tools("runtime_tools"))[0]
    result = await guarded_tool.ainvoke({"user_id": "uid_1", "flight_id": "AB12", "passengers": 2})

    assert result == "booked AB12"
    assert runtime.calls == [
        {
            "app_name": "runtime_tools",
            "function_name": "book_flight",
            "arguments": {"user_id": "uid_1", "flight_id": "AB12", "passengers": 2},
        }
    ]
    assert calls == [{"user_id": "uid_1", "flight_id": "AB12", "passengers": 2}]


@pytest.mark.asyncio
async def test_toolguard_provider_blocks_without_calling_original_tool() -> None:
    calls: list[dict[str, Any]] = []
    raw_tool = _make_recording_tool(calls)
    provider = ToolGuardingToolProvider(DummyProvider([raw_tool]), policy_storage=object())
    runtime = FakeRuntime(error="regular members cannot book more than 3 passengers")

    async def fake_get_runtime() -> FakeRuntime:
        return runtime

    provider._get_or_create_toolguard_runtime = fake_get_runtime  # type: ignore[method-assign]

    guarded_tool = (await provider.get_tools("runtime_tools"))[0]
    result = await guarded_tool.ainvoke({"user_id": "uid_1", "flight_id": "AB12", "passengers": 4})

    assert result["blocked_by_policy"] is True
    assert result["policy_violation"] is True
    assert result["tool"] == "book_flight"
    assert result["app"] == "runtime_tools"
    assert "regular members cannot book more than 3 passengers" in result["error"]
    assert calls == []


def test_toolguard_provider_sync_invoke_supports_sync_tools() -> None:
    calls: list[dict[str, Any]] = []
    raw_tool = _make_recording_tool(calls)
    provider = ToolGuardingToolProvider(DummyProvider([raw_tool]), policy_storage=None)

    guarded_tool = provider._wrap_tool(raw_tool, "runtime_tools")
    result = guarded_tool.invoke({"user_id": "uid_1", "flight_id": "AB12", "passengers": 1})

    assert result == "booked AB12"
    assert calls == [{"user_id": "uid_1", "flight_id": "AB12", "passengers": 1}]


@pytest.mark.asyncio
async def test_toolguard_provider_preserves_metadata_on_wrapped_tool_function() -> None:
    calls: list[dict[str, Any]] = []
    raw_tool = _make_recording_tool(calls)
    provider = ToolGuardingToolProvider(DummyProvider([raw_tool]), policy_storage=None)

    guarded_tool = (await provider.get_tools("runtime_tools"))[0]

    assert guarded_tool.func._app_name == "runtime_tools"
    assert guarded_tool.func._operation_id == "bookFlight"
    assert guarded_tool.func._param_constraints == {"passengers": ["max 3 for regular members"]}
    assert guarded_tool.func._response_schemas == {"success": {"type": "object"}}
    assert guarded_tool.args_schema is raw_tool.args_schema


@pytest.mark.asyncio
async def test_toolguard_runtime_blocks_on_internal_guard_error() -> None:
    runtime = ToolGuardRuntime(DummyProvider([]), enable_policies=False)
    runtime._initialized = True
    runtime.tool_to_guards = {"book_flight": [_make_tool_guide()]}
    runtime._runtimes_by_app["runtime_tools"] = FailingGuardRuntime(RuntimeError("boom"))

    error = await runtime.guard_tool_call(
        app_name="runtime_tools",
        function_name="book_flight",
        arguments={"passengers": 4},
    )

    assert error is not None
    assert "Internal guard error for 'book_flight': boom" in error
    assert "Tool call blocked as a safety precaution" in error


@pytest.mark.asyncio
async def test_toolguard_runtime_blocks_when_runtime_unavailable_for_applicable_guard() -> None:
    runtime = ToolGuardRuntime(DummyProvider([]), enable_policies=False)
    runtime._initialized = True
    runtime.tool_to_guards = {"book_flight": [_make_tool_guide()]}

    async def unavailable_runtime(_: str) -> None:
        return None

    runtime._get_or_create_runtime_for_app = unavailable_runtime  # type: ignore[method-assign]

    error = await runtime.guard_tool_call(
        app_name="runtime_tools",
        function_name="book_flight",
        arguments={"passengers": 4},
    )

    assert error is not None
    assert "ToolGuard runtime unavailable for 'book_flight' in app 'runtime_tools'" in error
    assert "Tool call blocked because an applicable guard policy exists" in error


@pytest.mark.asyncio
async def test_toolguard_runtime_blocks_invalid_declared_policy_code() -> None:
    runtime = ToolGuardRuntime(DummyProvider([]), enable_policies=False)
    runtime._initialized = True
    runtime._register_policy_guards(
        _make_tool_guide(
            name="Invalid guard policy",
            policy_code="def guard_book_flight(api, args):\n    return None\n",
        )
    )

    error = await runtime.guard_tool_call(
        app_name="runtime_tools",
        function_name="book_flight",
        arguments={"passengers": 4},
    )

    assert "book_flight" not in runtime.tool_to_guards
    assert "book_flight" in runtime.invalid_tool_guards
    assert error is not None
    assert "Invalid ToolGuard policy_code for 'book_flight' in app 'runtime_tools'" in error
    assert "Invalid guard policy" in error


@pytest.mark.asyncio
async def test_toolguard_runtime_blocks_args_parameter_collision() -> None:
    capture_runtime = CapturingGuardRuntime()
    runtime = ToolGuardRuntime(DummyProvider([]), enable_policies=False)
    runtime._initialized = True
    runtime.tool_to_guards = {"tool_with_args": [_make_tool_guide(tool_name="tool_with_args")]}
    runtime._runtimes_by_app["runtime_tools"] = capture_runtime

    error = await runtime.guard_tool_call(
        app_name="runtime_tools",
        function_name="tool_with_args",
        arguments={"args": "real tool parameter"},
    )

    assert error is not None
    assert "Internal guard argument collision for 'tool_with_args'" in error
    assert capture_runtime.calls == []


@pytest.mark.asyncio
async def test_toolguard_runtime_type_casts_with_tool_args_schema() -> None:
    raw_tool = _make_recording_tool([])
    capture_runtime = CapturingGuardRuntime()
    runtime = ToolGuardRuntime(DummyProvider([raw_tool]), enable_policies=False)
    runtime._initialized = True
    runtime.tool_to_guards = {"book_flight": [_make_tool_guide()]}
    runtime._runtimes_by_app["runtime_tools"] = capture_runtime

    error = await runtime.guard_tool_call(
        app_name="runtime_tools",
        function_name="book_flight",
        arguments={"user_id": "uid_1", "flight_id": "AB12", "passengers": "4"},
    )

    assert error is None
    assert len(capture_runtime.calls) == 1
    guard_args = capture_runtime.calls[0]["args"]
    assert guard_args["passengers"] == 4
    assert isinstance(guard_args["passengers"], int)
    assert guard_args["args"].passengers == 4


@pytest.mark.asyncio
async def test_toolguard_invoker_prefers_raw_tools_when_available() -> None:
    raw_calls: list[dict[str, Any]] = []
    raw_tool = _make_recording_tool(raw_calls)
    base_provider = DummyProvider([raw_tool])
    provider = ToolGuardingToolProvider(base_provider, policy_storage=None)
    invoker = ToolGuardInvoker(provider)

    result = await invoker.invoke(
        "book_flight",
        {"user_id": "uid_1", "flight_id": "AB12", "passengers": 1},
        str,
    )

    assert result == "booked AB12"
    assert raw_calls == [{"user_id": "uid_1", "flight_id": "AB12", "passengers": 1}]
    assert invoker._tools_cache["book_flight"] is raw_tool


def test_ensure_toolguard_provider_wraps_and_reconfigures_without_double_wrapping() -> None:
    raw_tool = _make_recording_tool([])
    base_provider = DummyProvider([raw_tool])
    storage_a = object()
    storage_b = object()

    wrapped = ensure_toolguard_provider(
        base_provider,
        policy_storage=storage_a,
        cuga_folder=".cuga-a",
        enabled=True,
    )

    assert isinstance(wrapped, ToolGuardingToolProvider)
    assert wrapped.get_base_provider() is base_provider
    assert wrapped.policy_storage is storage_a
    assert wrapped.cuga_folder == ".cuga-a"
    assert wrapped.enabled is True

    wrapped_again = ensure_toolguard_provider(
        wrapped,
        policy_storage=storage_b,
        cuga_folder=".cuga-b",
        enabled=False,
    )

    assert wrapped_again is wrapped
    assert wrapped_again.policy_storage is storage_b
    assert wrapped_again.cuga_folder == ".cuga-b"
    assert wrapped_again.enabled is False


def test_configure_and_unwrap_toolguard_provider_helpers() -> None:
    raw_tool = _make_recording_tool([])
    base_provider = DummyProvider([raw_tool])
    wrapped = ensure_toolguard_provider(base_provider)
    storage = object()

    configure_toolguard_provider(
        wrapped,
        policy_storage=storage,
        cuga_folder=".custom-cuga",
        enabled=False,
    )

    assert wrapped.policy_storage is storage
    assert wrapped.cuga_folder == ".custom-cuga"
    assert wrapped.enabled is False
    assert unwrap_tool_provider(wrapped) is base_provider
    assert unwrap_tool_provider(base_provider) is base_provider


def test_merge_tool_guards_preserves_omitted_tools_and_fields() -> None:
    existing = {
        "book_flight": ToolGuard(
            violating_examples=["old violating"],
            compliance_examples=["old compliance"],
            policy_code="old policy code",
        ),
        "cancel_flight": ToolGuard(
            violating_examples=["cancel violating"],
            compliance_examples=["cancel compliance"],
            policy_code="cancel policy code",
        ),
    }

    merged = merge_tool_guards(
        existing,
        {
            "book_flight": {
                "violating_examples": ["new violating"],
            }
        },
    )

    assert set(merged) == {"book_flight", "cancel_flight"}
    assert merged["book_flight"].violating_examples == ["new violating"]
    assert merged["book_flight"].compliance_examples == ["old compliance"]
    assert merged["book_flight"].policy_code == "old policy code"
    assert merged["cancel_flight"] == existing["cancel_flight"]


@pytest.mark.asyncio
async def test_guards_disabled_skips_registration():
    """When guards_enabled=False, no guards are registered for that policy's tools."""
    from cuga.backend.cuga_graph.policy.models import (
        AlwaysTrigger,
        ToolGuard,
        ToolGuide,
    )
    from cuga.backend.cuga_graph.policy.tool_guard.tool_guard_runtime import ToolGuardRuntime

    policy = ToolGuide(
        id="p1",
        name="Test Policy",
        description="desc",
        triggers=[AlwaysTrigger()],
        enabled=True,
        guards_enabled=False,
        priority=1,
        target_tools=["book_flight"],
        guide_content="guide",
        tool_guards={
            "book_flight": ToolGuard(
                violating_examples=["bad"],
                compliance_examples=["good"],
                policy_code="async def guard_book_flight(api, args): pass",
            )
        },
    )

    runtime = ToolGuardRuntime.__new__(ToolGuardRuntime)
    runtime.tool_to_guards = {}
    runtime.invalid_tool_guards = {}
    runtime._register_policy_guards(policy)

    assert "book_flight" not in runtime.tool_to_guards
