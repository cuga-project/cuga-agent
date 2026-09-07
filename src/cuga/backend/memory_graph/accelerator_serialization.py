from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from collections.abc import Iterator


# PyTorch MPS and MLX both submit work to Apple's Metal stack. Runtime verifier
# sources can be built concurrently in worker threads, so independent per-model
# locks are not enough: two different local models/frameworks may otherwise
# encode onto Metal at the same time. Use one process-wide lock for Apple GPU
# inference while leaving CPU/CUDA work concurrent.
_LOCAL_APPLE_ACCELERATOR_LOCK = threading.RLock()


def _serialization_enabled() -> bool:
    raw = os.getenv("CUGA_SERIALIZE_APPLE_ACCELERATOR", "1").strip().casefold()
    return raw not in {"0", "false", "off", "no", "disabled"}


@contextmanager
def serialized_local_accelerator(
    *,
    device: str | None = None,
    apple_metal: bool = False,
) -> Iterator[None]:
    """Serialize local Apple GPU inference across PyTorch MPS and MLX.

    ``device='mps'`` covers PyTorch models. ``apple_metal=True`` is used for
    MLX, whose public API does not expose a PyTorch-style device string here.
    The guard is deliberately a no-op for CPU/CUDA so non-Apple deployments do
    not lose inference concurrency.

    Set ``CUGA_SERIALIZE_APPLE_ACCELERATOR=0`` only for diagnostics/rollback.
    """
    should_lock = apple_metal or str(device or "").strip().casefold() == "mps"
    if not should_lock or not _serialization_enabled():
        yield
        return

    with _LOCAL_APPLE_ACCELERATOR_LOCK:
        yield
