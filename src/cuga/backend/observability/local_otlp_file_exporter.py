"""
In-process, no-network OTLP-JSON file span exporter.

See DP11 in docs/traceloop-instrumentation-spec.md: this is CUGA's
zero-infrastructure way to capture Traceloop spans locally (no OTel
Collector, no Docker, nothing else running). It writes standard OTLP JSON —
the same wire format the official OTel Collector's ``fileexporter`` would
produce — using the real, shared encoding function the official OTLP
exporters use internally, so the output is portable to anything that speaks
OTLP rather than a bespoke shape.

Each ``export()`` call encodes the given spans as one ``ExportTraceServiceRequest``
and appends it as a single JSON line to the target file.
"""

import threading

from google.protobuf.json_format import MessageToJson
from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult


class LocalOtlpFileSpanExporter(SpanExporter):
    """SpanExporter that appends OTLP-JSON lines to a local file.

    No collector, no network call — spans are encoded via the same
    ``encode_spans`` helper the real OTLP exporters use, then serialized with
    ``MessageToJson`` and appended as one line per export batch.
    """

    def __init__(self, file_path: str):
        self._path = file_path
        self._lock = threading.Lock()

    def export(self, spans) -> SpanExportResult:
        request = encode_spans(spans)
        line = MessageToJson(request, indent=None)
        with self._lock:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True
