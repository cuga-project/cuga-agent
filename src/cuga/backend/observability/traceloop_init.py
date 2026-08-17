"""
Traceloop initialization for Cuga LLM observability.

Traceloop auto-instruments LLM/agent calls (OpenAI, LangChain, LangGraph, MCP,
and ~35 other providers) and emits OpenTelemetry traces. It is CUGA's path to
trace-shaped observability output (as opposed to OpenLit, used today for
metrics-oriented Grafana dashboards).

## Enable

In settings.toml:
    [observability]
    traceloop = true

## Install

Traceloop is an optional extra — not installed by default:
    pip install cuga[observability-traceloop]
    # or:
    uv pip install "cuga[observability-traceloop]"

## Exporter

Two exporter modes, via settings.toml [observability] traceloop_exporter:
    "file" (default) — writes OTLP-JSON lines to a local file. No collector,
        no Docker, nothing else running. Defaults to
        <TRACES_DIR>/traceloop_spans.jsonl; override via
        [observability] traceloop_file_path.
    "otlp" — sends spans over OTLP/HTTP to a real collector, configured via
        the standard env vars:
            OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
            OTEL_EXPORTER_OTLP_HEADERS=Authorization=Bearer <token>

## Content capture

Traceloop's own default already captures prompt/completion content on spans.
``TRACELOOP_TRACE_CONTENT`` is deliberately left unset here so it stays at
that on-by-default behavior — not an oversight, see
docs/traceloop-instrumentation-plan.md (Phase 12 docs).

## TracerProvider ownership

init_traceloop() always calls Traceloop.init() unconditionally when the flag
is on — it deliberately does NOT guard on whether a TracerProvider already
exists. Traceloop's own SDK already attaches its span processor to whatever
TracerProvider is active (its own new one, or OpenLit's, or Langfuse's)
rather than requiring exclusive ownership — adding an existing-provider
early-return guard here (as some other codebases' wrappers do) would silently
defeat that attach behavior and produce a no-op. See DP3 in
docs/traceloop-instrumentation-spec.md.
"""

import os
import threading

from loguru import logger

from cuga.backend.observability.local_otlp_file_exporter import LocalOtlpFileSpanExporter
from cuga.backend.observability.openlit_init import _merge_otel_resource_attributes

# ---------------------------------------------------------------------------
# Set OTEL_SERVICE_NAME / OTEL_RESOURCE_ATTRIBUTES at MODULE LEVEL — before any
# other import that might trigger some other library to call
# trace.set_tracer_provider() first. Same rationale as openlit_init.py's own
# comment: whichever library creates the TracerProvider first should see
# correct resource attributes. Safe to repeat here even when openlit_init.py
# already did this (dedup by key via _merge_otel_resource_attributes).
# ---------------------------------------------------------------------------

if not os.getenv("OTEL_SERVICE_NAME"):
    os.environ["OTEL_SERVICE_NAME"] = "cuga"

try:
    from importlib.metadata import version as _pkg_version

    _cuga_version = _pkg_version("cuga")
except Exception:
    _cuga_version = "unknown"

_static_attrs_dict = {
    "agent.id": "CugaAgent",
    "service.version": _cuga_version,
}
_existing_resource_attrs = os.getenv("OTEL_RESOURCE_ATTRIBUTES", "")
os.environ["OTEL_RESOURCE_ATTRIBUTES"] = _merge_otel_resource_attributes(
    _existing_resource_attrs, _static_attrs_dict
)


_initialized = False  # Module-level guard: prevents redundant init on multiple calls
_init_lock = threading.Lock()  # Protects initialization from race conditions


def init_traceloop() -> None:
    """
    Initialize Traceloop tracing if enabled in settings.

    Idempotent — safe to call multiple times from different entry points
    (CugaAgent.initialize/invoke/stream, CugaSupervisor.invoke, etc.).

    Deliberately does NOT guard on an existing TracerProvider (see module
    docstring / DP3) — always calls Traceloop.init() when enabled, relying on
    Traceloop's own SDK to attach to whatever provider is already active.

    Configuration:
        settings.toml:  [observability] traceloop = true
        settings.toml:  [observability] traceloop_exporter = "file" | "otlp"
        settings.toml:  [observability] traceloop_file_path = "<path>"     # "file" mode only
        env var:        OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318  # "otlp" mode only
        env var:        OTEL_EXPORTER_OTLP_HEADERS=...                     # "otlp" mode only
    """
    global _initialized
    if _initialized:
        return

    with _init_lock:
        # Double-check inside the lock to prevent race conditions
        if _initialized:
            return

        try:
            from cuga.config import settings, TRACES_DIR
        except Exception as e:
            logger.warning(f"Traceloop: could not read observability settings: {e}")
            return

        obs = getattr(settings, "observability", None)
        if not getattr(obs, "traceloop", False):
            return

        # Dynamic resource attributes from settings (tenant.id, service.instance.id).
        # Static attrs (agent.id, service.version) were already set at module level.
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

        exporter_kind = getattr(obs, "traceloop_exporter", "file")
        if exporter_kind == "file":
            default_path = os.path.join(TRACES_DIR, "traceloop_spans.jsonl")
            path = getattr(obs, "traceloop_file_path", "") or default_path
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            exporter = LocalOtlpFileSpanExporter(path)
        else:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            from opentelemetry.util.re import parse_env_headers

            endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
            headers = parse_env_headers(os.getenv("OTEL_EXPORTER_OTLP_HEADERS", "")) or None
            exporter = OTLPSpanExporter(endpoint=endpoint, headers=headers)

        try:
            from traceloop.sdk import Traceloop
        except ImportError:
            logger.warning(
                "Traceloop observability is enabled in settings but 'traceloop-sdk' is not "
                "installed. Install it via: pip install cuga[observability-traceloop]"
            )
            return

        try:
            Traceloop.init(
                app_name="cuga",
                exporter=exporter,
                disable_batch=True,  # flush promptly, especially for local file mode
                instruments=None,  # enable everything (DP4) — no allow-list
                block_instruments=None,  # block nothing — no REQUESTS/URLLIB3 exclusion
            )
            # NOTE: deliberately NOT checking `isinstance(get_tracer_provider(), SdkTracerProvider)`
            # and returning early — see module docstring / DP3. Always call Traceloop.init();
            # its own init_tracer_provider() already attaches to an existing provider
            # correctly if one exists (OpenLit's or Langfuse's).
            _initialized = True
            logger.info(f"✅ Traceloop observability initialized (exporter={exporter_kind})")
        except Exception as e:
            logger.error(f"Failed to initialize Traceloop: {e}")


def set_task_association_properties(
    task_id: str | None = None,
    benchmark: str | None = None,
    difficulty: str | None = None,
    session_id: str | None = None,
) -> None:
    """
    Tag the current trace with task/eval correlation properties.

    No-op if Traceloop is not initialized (flag disabled, package not
    installed, or init_traceloop() not yet called/successful).
    """
    if not _initialized:
        return

    from traceloop.sdk import Traceloop

    props = {
        k: v
        for k, v in {
            "task_id": task_id,
            "benchmark": benchmark,
            "difficulty": difficulty,
            "session_id": session_id,
        }.items()
        if v is not None
    }
    if props:
        Traceloop.set_association_properties(props)
