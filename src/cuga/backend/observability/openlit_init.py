"""
OpenLit initialization for Cuga LLM observability.

OpenLit auto-instruments LLM calls (OpenAI, Groq, LiteLLM, LangChain, LangGraph, MCP, etc.)
and emits traces and metrics via OpenTelemetry (OTLP).

## Enable

In settings.toml:
    [observability]
    openlit = true

## Install

    pip install cuga
    # or:
    uv pip install cuga

Note: OpenLit is included as a core dependency and does not require an extra install.

## Configure OTLP endpoint

Set the environment variable (defaults to http://localhost:4318 if not set):
    OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318

Optional headers (e.g. for authenticated collectors):
    OTEL_EXPORTER_OTLP_HEADERS=Authorization=Bearer <token>

## Local testing stack

See deployment/docker-compose/openlit/ for a ready-to-use local stack:
    OTel Collector → Tempo (traces) + Prometheus (metrics) → Grafana

    cd deployment/docker-compose/openlit
    docker compose up -d
    # Then open Grafana at http://localhost:3000

## Research spike findings (openlit v1.4.0+)

- openlit.init() is pure global auto-instrumentation via monkey-patching.
  No per-request wiring needed — unlike Langfuse's callback handler approach.
- Internally uses a TRACER_SET global flag in otel/tracing.py — already idempotent.
- Instruments: openai, groq, litellm, langchain_core, langgraph, mcp, mem0, fastapi, httpx, and more.
- If otlp_endpoint=None, openlit reads OTEL_EXPORTER_OTLP_ENDPOINT from the environment automatically.
- Fully synchronous — safe to call from any context (sync or async).
"""

import logging
import os
import threading
from contextvars import ContextVar
from loguru import logger

# Logger for SessionSpanProcessor exception handling
_logger = logging.getLogger(__name__)


def _merge_otel_resource_attributes(existing: str, new_attrs: dict[str, str]) -> str:
    """
    Parse existing OTEL_RESOURCE_ATTRIBUTES, merge with new attributes by key,
    and return a deduplicated comma-separated string.

    New attributes overwrite existing ones with the same key.

    Args:
        existing: Current OTEL_RESOURCE_ATTRIBUTES value (comma-separated key=value pairs)
        new_attrs: Dictionary of new attributes to merge

    Returns:
        Merged comma-separated string of key=value pairs
    """
    # Parse existing attributes into a dict
    attrs_dict: dict[str, str] = {}
    if existing:
        for pair in existing.split(","):
            pair = pair.strip()
            if "=" in pair:
                key, value = pair.split("=", 1)
                attrs_dict[key.strip()] = value.strip()

    # Merge new attributes (overwriting existing keys)
    attrs_dict.update(new_attrs)

    # Reconstruct as comma-separated string
    return ",".join(f"{k}={v}" for k, v in attrs_dict.items())


# ---------------------------------------------------------------------------
# Set OTel env vars at MODULE LEVEL — before any other import that might
# trigger Langfuse (or another library) to call trace.set_tracer_provider().
#
# Langfuse's LangfuseResourceManager calls set_tracer_provider() at import
# time (via e2b_sandbox.py → langfuse.get_client()), creating a plain SDK
# TracerProvider that does NOT include OTEL_RESOURCE_ATTRIBUTES.  By setting
# these env vars here — and importing this module before any Langfuse import —
# we ensure that whichever library creates the TracerProvider first will pick
# up the correct resource attributes via Resource.create().
# ---------------------------------------------------------------------------

# service.name: shown in Tempo's Service column.
if not os.getenv("OTEL_SERVICE_NAME"):
    os.environ["OTEL_SERVICE_NAME"] = "cuga"

# Static resource attributes: agent.id and service.version.
# These are safe to set at module level (no settings/config needed).
# Dynamic attributes (tenant.id, service.instance.id) are added inside
# init_openlit() where settings are available.
#
# Use importlib.metadata to read the version — avoids importing the cuga package
# (which may not be fully initialized yet at this point in the import chain).
try:
    from importlib.metadata import version as _pkg_version

    _cuga_version = _pkg_version("cuga")
except Exception:
    _cuga_version = "unknown"

# Merge static attributes using key-based deduplication
_static_attrs_dict = {
    "agent.id": "CugaAgent",
    "service.version": _cuga_version,
}
_existing = os.getenv("OTEL_RESOURCE_ATTRIBUTES", "")
os.environ["OTEL_RESOURCE_ATTRIBUTES"] = _merge_otel_resource_attributes(_existing, _static_attrs_dict)

try:
    import openlit  # type: ignore[import-untyped]
except ImportError:
    openlit = None  # type: ignore[assignment]

try:
    from opentelemetry import trace as otel_trace  # type: ignore[import-untyped]
    from opentelemetry.sdk.trace import SpanProcessor, ReadableSpan  # type: ignore[import-untyped]
    from opentelemetry.context import Context  # type: ignore[import-untyped]
except ImportError:
    otel_trace = None  # type: ignore[assignment]
    # Provide a safe no-op base class when OpenTelemetry is not available
    SpanProcessor = type("BaseSpanProcessor", (object,), {})  # type: ignore[assignment,misc]
    ReadableSpan = None  # type: ignore[assignment]
    Context = None  # type: ignore[assignment]

_initialized = False  # Module-level guard: prevents redundant log output on multiple calls
_init_lock = threading.Lock()  # Protects initialization from race conditions
_current_session_id: ContextVar[str | None] = ContextVar(
    "current_session_id", default=None
)  # Task-local session ID for SpanProcessor


class SessionSpanProcessor(SpanProcessor):  # type: ignore[misc]
    """
    OTel SpanProcessor that automatically tags all spans with session.id.

    This processor is added to the TracerProvider and runs on every span start,
    allowing us to tag spans even if they're created in different async contexts.
    """

    def on_start(self, span: "ReadableSpan", parent_context: "Context | None" = None) -> None:
        """Called when a span starts — tag it with session.id if available."""
        session_id = _current_session_id.get()
        if session_id:
            try:
                if hasattr(span, 'is_recording') and span.is_recording():
                    span.set_attribute("session.id", session_id)
            except Exception:
                # Log the exception at debug level for troubleshooting
                _logger.debug("Failed to tag span with session.id", exc_info=True)

    def on_end(self, span: "ReadableSpan") -> None:
        """Called when a span ends — no-op."""
        pass

    def shutdown(self) -> None:
        """Called on shutdown — no-op."""
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        """Called on flush — no-op."""
        return True


def init_openlit() -> None:
    """
    Initialize OpenLit auto-instrumentation if enabled in settings.

    This function is idempotent — safe to call multiple times from different
    entry points (server startup, SDK initialization, AgentRunner, etc.).

    When enabled, OpenLit instruments all LLM calls globally (LangChain, LangGraph,
    OpenAI, Groq, LiteLLM, MCP, etc.) and emits traces/metrics via OTLP.

    Configuration:
        settings.toml:  [observability] openlit = true
        env var:        OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
    """
    global _initialized
    if _initialized:
        return

    with _init_lock:
        # Double-check inside the lock to prevent race conditions
        if _initialized:
            return

        # Check if OpenLit is enabled in settings
        try:
            from cuga.config import settings

            openlit_enabled = getattr(getattr(settings, "observability", None), "openlit", False)
            if not openlit_enabled:
                return
        except Exception as e:
            logger.warning(f"OpenLit: could not read observability settings: {e}")
            return

        # Graceful no-op if openlit is not installed
        if openlit is None:
            logger.warning(
                "OpenLit observability is enabled in settings but 'openlit' is not installed. "
                "This should not happen as openlit is a core dependency. Please reinstall cuga."
            )
            return

        # Determine OTLP endpoint for logging purposes (openlit reads the env var itself)
        otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
        cuga_version = _cuga_version  # set at module level

        # Add dynamic resource attributes from settings (tenant.id, service.instance.id).
        # Static attrs (agent.id, service.version) were already set at module level.
        # These must be appended before openlit.init() creates the TracerProvider.
        tenant_id = getattr(getattr(settings, "service", None), "tenant_id", "") or ""
        instance_id = getattr(getattr(settings, "service", None), "instance_id", "") or ""
        dynamic_attrs: dict = {}
        if tenant_id:
            dynamic_attrs["tenant.id"] = tenant_id
        if instance_id:
            dynamic_attrs["service.instance.id"] = instance_id

        if dynamic_attrs:
            existing = os.getenv("OTEL_RESOURCE_ATTRIBUTES", "")
            os.environ["OTEL_RESOURCE_ATTRIBUTES"] = _merge_otel_resource_attributes(existing, dynamic_attrs)

        # Log OTEL_RESOURCE_ATTRIBUTES with only keys to avoid exposing sensitive values
        attrs = os.getenv("OTEL_RESOURCE_ATTRIBUTES", "")
        attr_keys = [p.split("=", 1)[0].strip() for p in attrs.split(",") if "=" in p]
        logger.debug(f"OpenLit: OTEL_RESOURCE_ATTRIBUTES keys={','.join(attr_keys)}")

        try:
            # Pass no otlp_endpoint argument so openlit reads OTEL_EXPORTER_OTLP_ENDPOINT
            # from the environment automatically (standard OTel pattern).
            # application_name is the OpenLit-level label; OTEL_SERVICE_NAME (set above)
            # is the OTel resource attribute that Tempo uses for the Service column.
            openlit.init(
                application_name="cuga",
                capture_message_content=False,
            )

            # Security: Uninstrument FastAPI and httpx to prevent HTTP-level spans
            # from capturing auth cookies (cuga_session JWT) or headers.
            # OpenLIT instruments these via standard OTel contrib packages.
            # We only need LLM/agent tracing (OpenAI, LangChain, LangGraph, MCP, etc.).
            try:
                from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

                FastAPIInstrumentor().uninstrument()
                logger.debug("FastAPI instrumentation disabled for security")
            except ImportError:
                pass

            try:
                from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

                HTTPXClientInstrumentor().uninstrument()
                logger.debug("httpx instrumentation disabled for security")
            except ImportError:
                pass

            # Register SessionSpanProcessor to auto-tag spans with session.id
            # This works in server mode where set_session_attribute() is called from AgentLoop
            if otel_trace is not None:
                try:
                    from opentelemetry import trace as otel_trace_module

                    trace_provider = otel_trace_module.get_tracer_provider()
                    if hasattr(trace_provider, 'add_span_processor'):
                        trace_provider.add_span_processor(SessionSpanProcessor())
                        logger.debug("SessionSpanProcessor registered for session tracking")
                except Exception as e:
                    logger.warning(f"Could not register SessionSpanProcessor: {e}")

            _initialized = True
            logger.info(
                f"✅ OpenLit observability initialized "
                f"(OTLP: {otlp_endpoint}, version: {cuga_version}, "
                f"tenant: {tenant_id or 'unset'}, instance: {instance_id or 'unset'})"
            )
        except Exception as e:
            logger.error(f"Failed to initialize OpenLit: {e}")


def set_session_attribute(session_id: str) -> None:
    """
    Set the current session ID for automatic tagging on all spans.

    This function sets a task-local session ID (via ContextVar) that the
    SessionSpanProcessor will automatically apply to all new spans in the
    current async context. This ensures each concurrent task maintains its
    own session ID without interference.

    Call this at the start of each agent invocation to enable per-session
    aggregation of token usage and latency in Grafana dashboards.

    No-op if OpenLit is not initialized (flag disabled or package not installed).

    Args:
        session_id: The conversation thread ID (e.g. thread_id from AgentRunner or SDK invoke)
    """
    if not _initialized:
        return

    # Set the task-local session ID — SessionSpanProcessor will pick it up
    _current_session_id.set(session_id)

    # Also try to tag any currently active span (best effort)
    if otel_trace is not None:
        try:
            span = otel_trace.get_current_span()
            if span and span.is_recording():
                _logger.debug("Tagging current span with session.id=<redacted>")
                span.set_attribute("session.id", session_id)
                _logger.debug("Successfully tagged current span with session.id=<redacted>")
            else:
                _logger.debug("No active recording span found to tag with session.id=<redacted>")
        except Exception:
            _logger.debug("Failed to tag current span with session.id=<redacted>", exc_info=True)


# ---------------------------------------------------------------------------
# Initialize OpenLit at module import time (process level).
# This ensures instrumentation is set up before any LLM libraries are used,
# regardless of entry point (server, SDK, CLI, tests).
# ---------------------------------------------------------------------------
init_openlit()
