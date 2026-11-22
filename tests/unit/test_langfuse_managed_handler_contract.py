"""Public managed Langfuse handler contract without external connections."""

import socket
from unittest.mock import Mock

import pytest

from cuga.backend.observability import langfuse_sdk

pytestmark = pytest.mark.unit
TRACE = "a" * 32
_real_enabled = langfuse_sdk.enabled


def config(trace_id=None):
    return {"configurable": {"langfuse_trace_id": trace_id} if trace_id else {}}


@pytest.fixture
def exporter(monkeypatch):
    def forbid_connect(*args, **kwargs):
        pytest.fail("external sockets are not permitted in managed handler tests")

    monkeypatch.setattr(socket.socket, "connect", forbid_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", forbid_connect)
    factory = Mock()
    monkeypatch.setattr(langfuse_sdk, "_client", factory)
    monkeypatch.setattr(langfuse_sdk, "enabled", lambda: True)
    monkeypatch.setattr(langfuse_sdk, "_has_current_span", lambda: False)
    return factory


@pytest.fixture
def handlers(monkeypatch):
    from langfuse import langchain

    from cuga.backend.cuga_graph.utils import langfuse_tracing

    class CallbackHandler:
        def __init__(self, *, public_key=None, trace_context=None):
            self.options = public_key, trace_context

        def on_tool_start(self, *args, **kwargs):
            return "stock start"

        def on_tool_end(self, *args, **kwargs):
            return "stock end"

        def on_tool_error(self, *args, **kwargs):
            return "stock error"

        def on_chain_start(self, *args, **kwargs):
            return "stock chain"

    monkeypatch.setattr(langchain, "CallbackHandler", CallbackHandler)
    monkeypatch.setattr(langfuse_tracing, "_langfuse_handler_classes", lambda: (CallbackHandler,))
    ancestry = {"trace_id": TRACE}
    return CallbackHandler(), langfuse_sdk.create_sdk_tool_handler(
        public_key="synthetic", trace_context=ancestry
    )


def test_managed_factory_only_suppresses_tool_methods_on_its_own_instance(exporter, handlers):
    stock, managed = handlers
    assert managed.options == ("synthetic", {"trace_id": TRACE})
    assert managed.on_chain_start() == stock.on_chain_start() == "stock chain"
    for method, expected in [
        ("on_tool_start", "stock start"),
        ("on_tool_end", "stock end"),
        ("on_tool_error", "stock error"),
    ]:
        assert getattr(managed, method)("synthetic") is None
        assert getattr(stock, method)("synthetic") == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("mixed", [True, False])
async def test_managed_handler_warns_without_mutating_callback_owners(monkeypatch, exporter, handlers, mixed):
    factory = exporter
    stock, managed = handlers
    log = Mock()
    monkeypatch.setattr(langfuse_sdk, "logger", log)
    callbacks = [stock, managed] if mixed else [managed]
    options = config(trace_id=TRACE if mixed else None)
    options["callbacks"] = callbacks
    async with langfuse_sdk.run_trace("synthetic", "test", "thread", options) as trace:
        assert trace.span is None
        assert options["callbacks"] is callbacks
    assert callbacks == ([stock, managed] if mixed else [managed])
    factory.assert_not_called()
    log.warning.assert_called_once_with(
        "CUGA tool ownership mixed: stock and managed Langfuse handlers; strict single-writer guarantee unavailable"
        if mixed
        else "CUGA managed tool observations require an active span or an explicit langfuse_trace_id; "
        "tool observations are not recorded for this run"
    )


@pytest.mark.asyncio
async def test_managed_handler_does_not_enable_tools_without_keys(monkeypatch, exporter, handlers):
    from cuga.config import settings

    _, managed = handlers
    factory = exporter
    monkeypatch.setattr(settings.advanced_features, "langfuse_tracing", True)
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.setattr(langfuse_sdk, "enabled", _real_enabled)
    options = config(trace_id=TRACE)
    options["callbacks"] = [managed]
    async with langfuse_sdk.run_trace("synthetic", "test", "thread", options) as trace:
        assert trace.span is None
        langfuse_sdk.record_tool_call("synthetic", {})
    factory.assert_not_called()
    assert options["callbacks"] == [managed]
