"""Per-run DISK logs — every /invoke execution written as one JSON file + a daily JSONL index.

Layout (override the root with ``EVENTS_RUN_LOG_DIR``; default ``<repo>/results/run_logs``):

    results/run_logs/2026-07-15/  ─┬─ index.jsonl            ← one line per run (grep/jq-able)
                                   └─ 21-42-07_a1b2c3d4.json  ← the full record, pretty-printed

Each record: when · kind (chat|fire) · agent · source/channel · thread_id · subscription hint ·
the text IN · the answer OUT · status · latency · trace_id (links to cuga.log) · meta.
Append-only, never raises (logging must not break the run it describes)."""

from __future__ import annotations

import json
import os
import pathlib
import time
import uuid


def _root() -> pathlib.Path:
    base = os.environ.get("EVENTS_RUN_LOG_DIR", "").split(" #", 1)[0].strip()
    if not base:
        base = str(pathlib.Path(__file__).resolve().parents[4] / "results" / "run_logs")
    return pathlib.Path(base)


def dump(**record) -> str:
    """Write one run record. Returns the run-log id (also stored in the record)."""
    try:
        rid = record.setdefault("run_log_id", uuid.uuid4().hex[:12])
        record.setdefault("when", time.strftime("%Y-%m-%d %H:%M:%S"))
        day = _root() / time.strftime("%Y-%m-%d")
        day.mkdir(parents=True, exist_ok=True)
        fname = f"{time.strftime('%H-%M-%S')}_{rid}.json"
        (day / fname).write_text(json.dumps(record, indent=1, ensure_ascii=False, default=str))
        with open(day / "index.jsonl", "a") as f:
            f.write(json.dumps({k: record.get(k) for k in
                                ("run_log_id", "when", "kind", "agent", "channel", "thread_id",
                                 "subscription_id", "status", "ms", "trace_id")},
                               ensure_ascii=False, default=str) + "\n")
        return rid
    except Exception:  # noqa: BLE001
        return ""
