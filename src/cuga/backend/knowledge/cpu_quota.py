"""The CPU limit of the container this process runs in, to size ONNX Runtime's thread pools.

ONNX Runtime sizes its thread pool from the cores it can see. In a container limited to fewer
CPUs (``docker --cpus 2``, a Kubernetes CPU limit), it still sees every core of the node, starts a
thread per core, and the CPU limit throttles them: embedding ran 1.3-2.7 times slower than with
one thread per allowed CPU.
"""

from __future__ import annotations

import functools
import logging
import math
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# cgroup v2: "<quota> <period>" or "max <period>". With a cgroup namespace (the default on v2),
# /sys/fs/cgroup is the container's own cgroup.
_CGROUP_V2_CPU_MAX = Path("/sys/fs/cgroup/cpu.max")
# cgroup v1: quota and period in microseconds; a quota of -1 means no limit.
_CGROUP_V1_DIRS = (Path("/sys/fs/cgroup/cpu"), Path("/sys/fs/cgroup/cpu,cpuacct"))


def _read_words(path: Path) -> list[str] | None:
    try:
        return path.read_text().split()
    except (OSError, ValueError):
        return None


def _quota_cpus() -> float | None:
    """The CPU quota as a number of CPUs, or None when there is no limit or no cgroup."""
    words = _read_words(_CGROUP_V2_CPU_MAX)
    if words:
        if words[0] == "max" or len(words) < 2:
            return None
        try:
            quota, period = int(words[0]), int(words[1])
        except ValueError:
            return None
        return quota / period if quota > 0 and period > 0 else None
    for directory in _CGROUP_V1_DIRS:
        quota_words = _read_words(directory / "cpu.cfs_quota_us")
        period_words = _read_words(directory / "cpu.cfs_period_us")
        if not quota_words or not period_words:
            continue
        try:
            quota, period = int(quota_words[0]), int(period_words[0])
        except ValueError:
            return None
        return quota / period if quota > 0 and period > 0 else None
    return None


@functools.lru_cache(maxsize=1)
def onnx_threads() -> int | None:
    """Threads for ONNX Runtime: the container's CPU quota rounded up, at most ``os.cpu_count()``.

    None when the process has no CPU quota (macOS, a VM, an unlimited container), so ONNX Runtime
    keeps its own default there. Read once per process.
    """
    cpus = _quota_cpus()
    if cpus is None:
        return None
    threads = max(1, math.ceil(cpus))
    visible = os.cpu_count()
    if visible:
        threads = min(threads, visible)
    logger.info("ONNX Runtime uses %d thread(s): the container's CPU limit is %.2f CPUs", threads, cpus)
    return threads
