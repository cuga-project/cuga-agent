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

from cuga.backend.observability.openlit_init import _merge_otel_resource_attributes

# ---------------------------------------------------------------------------
# NOTE: no static resource-attrs block (agent.id/service.version) here.
#
# This module imports openlit_init (above, for _merge_otel_resource_attributes),
# and Python guarantees openlit_init.py's own module body — including its own
# identical static-attrs block (same keys, same values) — always runs first as
# a side effect of that import. A second copy here would only ever be a no-op
# dedup-by-key merge on top of what openlit_init.py already set. See
# openlit_init.py's own module-level comment for the full rationale.
#
# OTEL_SERVICE_NAME *is* still set here (kept intentionally, not dead code):
# it's an idempotent `if not os.getenv(...)` check, so whichever of this
# module or openlit_init.py runs first "wins" with zero behavioral difference
# either way — unlike the static-attrs dict merge above, there's no dedup
# logic to make redundant, so keeping it costs nothing and protects against
# a hypothetical future where this module no longer imports openlit_init.
# ---------------------------------------------------------------------------

if not os.getenv("OTEL_SERVICE_NAME"):
    os.environ["OTEL_SERVICE_NAME"] = "cuga"

# @tool-decorated functions (activity_tracker/tracker.py's invoke_tool/invoke_tool_sync,
# Phase 6) call TracerWrapper.verify_initialized() on every invocation; without this,
# it prints a warning to stdout on every call whenever Traceloop is disabled (the
# default). This module's whole design (Phase 1) is to degrade silently, not spam
# stdout, when tracing isn't configured.
if not os.getenv("TRACELOOP_SUPPRESS_WARNINGS"):
    os.environ["TRACELOOP_SUPPRESS_WARNINGS"] = "true"


_initialized = False  # Module-level guard: prevents redundant init on multiple calls
_init_attempted = False  # True once one init attempt (success OR failure) has happened
# this process — distinguishes "never attempted" from "attempted and failed, don't
# retry" from "succeeded" (_initialized). Without this, a failed/unavailable init
# (missing extra, or a caught construction error) would re-attempt settings read +
# directory creation + exporter construction + re-log the same warning on every
# single subsequent invoke()/stream()/etc. call, forever, for the life of the process.
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
    global _initialized, _init_attempted
    if _initialized or _init_attempted:
        return

    with _init_lock:
        # Double-check inside the lock to prevent race conditions
        if _initialized or _init_attempted:
            return
        # Mark "attempted" up front, before anything that can fail below — a
        # failed attempt (missing extra, or a caught exporter-construction
        # error) must not be retried on subsequent calls. See _init_attempted
        # module docstring above.
        _init_attempted = True

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
        # Observability must never crash the main agent path (this function runs
        # unguarded as the first thing in CugaAgent.invoke()/initialize()/stream()
        # and CugaSupervisor.invoke()) — so resolving/constructing the exporter for
        # either mode is guarded the same way as the Traceloop.init() call below.
        try:
            if exporter_kind == "file":
                # Lazy import: local_otlp_file_exporter pulls in
                # google.protobuf.json_format + opentelemetry.exporter.otlp.proto.common
                # (~58ms cold-import cost) — only worth paying in "file" mode, same
                # pattern as the "otlp" branch's own lazy imports just below.
                from cuga.backend.observability.local_otlp_file_exporter import (
                    LocalOtlpFileSpanExporter,
                )

                default_path = os.path.join(TRACES_DIR, "traceloop_spans.jsonl")
                path = getattr(obs, "traceloop_file_path", "") or default_path
                os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                exporter = LocalOtlpFileSpanExporter(path)
            else:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
                from opentelemetry.util.re import parse_env_headers

                # Deliberately do NOT pass `endpoint=` explicitly: OTLPSpanExporter's own
                # constructor, when endpoint is left None, reads OTEL_EXPORTER_OTLP_ENDPOINT
                # itself and appends the "/v1/traces" signal path automatically (per the
                # OTel spec) — the same behavior Langfuse's/every collector's docs assume
                # ("just set OTEL_EXPORTER_OTLP_ENDPOINT to the base URL"). Passing endpoint=
                # explicitly here bypasses that auto-append and silently 404s. Verified
                # empirically in Phase 3 (docs/traceloop-instrumentation-plan.md).
                headers = parse_env_headers(os.getenv("OTEL_EXPORTER_OTLP_HEADERS", "")) or None
                exporter = OTLPSpanExporter(headers=headers)
        except Exception as e:
            logger.error(f"Failed to construct Traceloop exporter (mode={exporter_kind}): {e}")
            return

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
                # SimpleSpanProcessor (file) vs BatchSpanProcessor (otlp). File mode:
                # synchronous flush so "see the trace file immediately" (Phase 1's
                # "Done when" bar) actually holds — a cheap local write, fine to do
                # inline. OTLP mode: a SimpleSpanProcessor would turn every span into
                # a blocking HTTP POST on the agent's own thread/event loop, with the
                # installed exporter retrying up to 6x with exponential backoff on
                # failure — a slow/down collector would stall the agent on every
                # span. Batched/async export avoids blocking on network I/O.
                disable_batch=(exporter_kind == "file"),
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
            return

        # A2A cross-process trace-context propagation (DP4b). Traceloop's own
        # `Instruments` enum has no FastAPI/ASGI member, so `instruments=None`
        # above doesn't cover the A2A HTTP boundary. httpx covers outbound
        # calls (delegate_task_via_a2a_sdk/fetch_agent_card); aiohttp covers
        # the legacy A2AProtocol class. Header/cookie capture stays opt-in and
        # unset here, so this doesn't reopen OpenLit's uninstrument concern.
        #
        # Runs after openlit_init.py (import order, see above) — both
        # packages patch methods in place (`wrap_function_wrapper`), so this
        # reliably wins over OpenLit's own uninstrument step regardless of
        # when httpx/aiohttp were imported.
        #
        # FastAPI isn't enabled here — see instrument_fastapi_app() below.
        try:
            from opentelemetry.instrumentation.aiohttp_client import AioHttpClientInstrumentor
            from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

            HTTPXClientInstrumentor().instrument()
            AioHttpClientInstrumentor().instrument()
        except Exception as e:
            logger.warning(f"Traceloop: failed to enable A2A HTTP instrumentors: {e}")


def instrument_fastapi_app(app) -> None:
    """
    Instrument one FastAPI app instance for A2A trace-context propagation.

    Not done via the global `FastAPIInstrumentor().instrument()` (used for
    httpx/aiohttp above): that swaps the `fastapi.FastAPI` class reference,
    which only affects code that imports `FastAPI` *after* the patch runs.
    `server/main.py` imports `FastAPI` before this module, so its app always
    binds the pre-patch class regardless of init order — confirmed
    empirically to silently drop inbound `traceparent` headers.
    `instrument_app()` wraps the given instance's middleware stack directly
    instead, sidestepping the class-identity issue entirely.

    Call once, right after the app instance is created (see
    `server/main.py`). No-op if Traceloop isn't initialized — safe to call
    unconditionally at the app-creation site.
    """
    if not _initialized:
        return
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor().instrument_app(app)
    except Exception as e:
        logger.warning(f"Traceloop: failed to instrument FastAPI app for A2A tracing: {e}")


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
        try:
            Traceloop.set_association_properties(props)
        except Exception as e:
            logger.error(f"Failed to set Traceloop task association properties: {e}")


# ---------------------------------------------------------------------------
# Initialize Traceloop at module import time (process level), mirroring
# openlit_init.py. init_traceloop() checks the settings flag before any heavy
# import, so this is cheap when the feature is off, and ensures the
# LangChain/LangGraph auto-instrumentation is active before any graph call —
# regardless of entry point (server, SDK, CLI, tests) — not just when one of
# cuga.sdk's own call sites happens to run first.
# ---------------------------------------------------------------------------
init_traceloop()
