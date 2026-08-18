"""
memlib.py — shared sampling library for the CUGA memory-profiling harness.

Every measurement script in src/system_tests/profiling/memory/ imports this
module.  It is a library, not a script: nothing runs at import time and there
is no ``__main__`` block.

Stdout contract
---------------
``emit()`` writes exactly ONE JSON object as the **last line of stdout**.
All warnings and diagnostic messages go to stderr so callers can safely grab
``stdout.splitlines()[-1]`` and ``json.loads()`` it.

Runtime dependencies
--------------------
- ``psutil``  — already a runtime dep; used for RSS, USS, process tree.
- ``resource`` — stdlib (POSIX only; not available on Windows).
- No ``memray`` import — memray is optional and must NOT be imported here.
"""

from __future__ import annotations

import gc
import json
import os
import platform as _platform_mod
import resource
import sys
from datetime import datetime, timezone
from typing import Any

import psutil


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _peak_rss_mb() -> float:
    """Return peak RSS in **megabytes** for the current process.

    Unit trap: on macOS ``resource.getrusage(RUSAGE_SELF).ru_maxrss`` is in
    **bytes**; on Linux it is in **kilobytes**.  Getting this wrong produces a
    silent 1024× error (tens of GB instead of tens of MB).  We normalise here
    so callers always get MB regardless of platform.
    """
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        # macOS: ru_maxrss is bytes → divide by 1024² to get MB
        return raw / (1024 * 1024)
    else:
        # Linux (and other POSIX): ru_maxrss is kilobytes → divide by 1024
        return raw / 1024


def _rss_mb(proc: psutil.Process) -> float:
    """RSS in MB for *proc*."""
    return proc.memory_info().rss / (1024 * 1024)


def _uss_mb(proc: psutil.Process) -> float | None:
    """USS in MB for *proc*, or None if unavailable (permissions / platform)."""
    try:
        return proc.memory_full_info().uss / (1024 * 1024)
    except (psutil.AccessDenied, psutil.NoSuchProcess, AttributeError, OSError):
        return None


def _pss_mb(proc: psutil.Process) -> float | None:
    """PSS in MB for *proc*, or None if unavailable (Linux only)."""
    try:
        return proc.memory_full_info().pss / (1024 * 1024)  # type: ignore[attr-defined]
    except (psutil.AccessDenied, psutil.NoSuchProcess, AttributeError, OSError):
        return None


def _platform_info() -> dict[str, str]:
    return {
        "system": _platform_mod.system(),
        "machine": _platform_mod.machine(),
        "python": _platform_mod.python_version(),
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def sample(label: str, extra: dict | None = None) -> dict:
    """gc.collect(), then return a memory record for the current process.

    Args:
        label:  Human-readable checkpoint name (e.g. ``"baseline"``).
        extra:  Optional mapping merged into the returned record (top-level).

    Returns:
        A dict matching the standard memory-record schema.
    """
    gc.collect()
    proc = psutil.Process()
    record: dict[str, Any] = {
        "label": label,
        "rss_mb": _rss_mb(proc),
        "uss_mb": _uss_mb(proc),
        "peak_rss_mb": _peak_rss_mb(),
        "modules": len(sys.modules),
        "platform": _platform_info(),
        "config": capture_config(),
        "ts": _now_iso(),
    }
    if extra:
        record.update(extra)
    return record


def process_sample(pid: int, label: str) -> dict:
    """Return a memory record for an external process identified by *pid*.

    USS is ``None`` when unavailable (permissions, platform).  The call never
    raises for ``NoSuchProcess`` — it returns a record with ``None`` for all
    memory fields instead.

    Args:
        pid:   Target process ID.
        label: Human-readable label for this sample.

    Returns:
        A dict matching the standard per-process memory-record schema.
    """
    try:
        proc = psutil.Process(pid)
        gc.collect()
        return {
            "pid": pid,
            "label": label,
            "rss_mb": _rss_mb(proc),
            "uss_mb": _uss_mb(proc),
            "platform": _platform_info(),
            "ts": _now_iso(),
        }
    except psutil.NoSuchProcess:
        return {
            "pid": pid,
            "label": label,
            "rss_mb": None,
            "uss_mb": None,
            "platform": _platform_info(),
            "ts": _now_iso(),
        }


def tree_sample(root_pid: int) -> dict:
    """Walk *root_pid* and all descendants; return per-process records + totals.

    Totals use USS on macOS (where PSS is unavailable) and PSS on Linux
    (avoids double-counting shared pages across the tree).  The total is
    **never** a simple sum of RSS, which would over-count shared library pages.

    Args:
        root_pid: PID of the root process to walk.

    Returns:
        ``{"processes": [...], "total": {...}}`` where ``total`` includes the
        metric name used (``"uss"`` or ``"pss"``) and its summed value in MB.
    """
    gc.collect()
    try:
        root = psutil.Process(root_pid)
        procs = [root] + root.children(recursive=True)
    except psutil.NoSuchProcess:
        procs = []

    records: list[dict] = []
    total_uss = 0.0
    total_pss = 0.0
    total_rss = 0.0  # recorded for reference only — not used as the total
    use_pss = sys.platform.startswith("linux")

    for proc in procs:
        try:
            rec = process_sample(proc.pid, label="tree")
            records.append(rec)
            uss = rec.get("uss_mb") or 0.0
            total_uss += uss
            total_rss += rec.get("rss_mb") or 0.0
            if use_pss:
                pss = _pss_mb(proc) or 0.0
                total_pss += pss
        except psutil.NoSuchProcess:
            # Process exited mid-walk — tolerate silently.
            pass

    if use_pss:
        total_metric = "pss"
        total_value = total_pss
    else:
        total_metric = "uss"
        total_value = total_uss

    return {
        "processes": records,
        "total": {
            "metric": total_metric,
            f"{total_metric}_mb": total_value,
            "rss_mb_sum": total_rss,  # informational; do not use for budgeting
            "process_count": len(records),
        },
    }


def delta(before: dict, after: dict) -> dict:
    """Return the difference of every numeric field between *before* and *after*.

    Non-numeric and missing fields are skipped.  The result has the same keys
    as the intersection of numeric fields in both dicts.

    Args:
        before: Earlier memory record.
        after:  Later memory record.

    Returns:
        ``{key: after[key] - before[key]}`` for all shared numeric keys.
    """
    result: dict[str, Any] = {}
    for key in after:
        if key in before:
            a_val = after[key]
            b_val = before[key]
            if isinstance(a_val, (int, float)) and isinstance(b_val, (int, float)):
                result[key] = a_val - b_val
    return result


def capture_config() -> dict:
    """Return env/settings that change the memory answer.

    Reads **only env vars** so this function is safe to call inside a
    measuring subprocess without importing ``cuga.config`` (which would pull
    in the entire cuga package and skew the measurement).

    Keys captured:
    - ``llm_platform``     — derived from ``AGENT_SETTING_CONFIG`` filename
    - ``policy_enabled``   — ``DYNACONF_POLICY__ENABLED``
    - ``knowledge_enabled``— ``DYNACONF_KNOWLEDGE__ENABLED``
    - ``embeddings_provider`` — ``DYNACONF_KNOWLEDGE__EMBEDDINGS__PROVIDER``
    - ``observability``    — ``DYNACONF_OBSERVABILITY__OPENLIT``
    - ``python_malloc``    — ``PYTHONMALLOC``
    """
    # Derive LLM platform from the model config filename (e.g. "settings.openai.toml" → "openai")
    agent_cfg = os.environ.get("AGENT_SETTING_CONFIG", "settings.openai.toml")
    # Strip path, extension, and "settings." prefix to get just the platform token.
    cfg_stem = os.path.basename(agent_cfg).replace("settings.", "").rsplit(".", 1)[0]

    return {
        "llm_platform": cfg_stem or "unknown",
        "policy_enabled": os.environ.get("DYNACONF_POLICY__ENABLED", "").lower() not in ("false", "0", ""),
        "knowledge_enabled": os.environ.get("DYNACONF_KNOWLEDGE__ENABLED", "").lower() in ("true", "1"),
        "embeddings_provider": os.environ.get("DYNACONF_KNOWLEDGE__EMBEDDINGS__PROVIDER", "fastembed"),
        "observability": os.environ.get("DYNACONF_OBSERVABILITY__OPENLIT", "").lower() in ("true", "1"),
        "python_malloc": os.environ.get("PYTHONMALLOC", ""),
    }


def emit(record: dict) -> None:
    """Write *record* as a JSON object to stdout as the final output line.

    All other output (logs, progress) must go to stderr so that callers can
    reliably extract the record with ``stdout.splitlines()[-1]``.

    Args:
        record: Any JSON-serialisable dict.
    """
    print(json.dumps(record))
