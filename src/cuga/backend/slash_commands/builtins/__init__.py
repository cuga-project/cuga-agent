"""Auto-discovery of built-in slash commands.

Each module under this package that exports a ``BUILTIN`` attribute (an
instance satisfying :class:`BuiltinCommand`) is auto-registered at server
start. Modules whose name starts with ``_`` are skipped. Adding a new built-in
is a one-file change: drop a module here, expose ``BUILTIN``, done.
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import List

from loguru import logger

from cuga.backend.slash_commands.types import BuiltinCommand


def discover_builtins() -> List[BuiltinCommand]:
    out: List[BuiltinCommand] = []
    import cuga.backend.slash_commands.builtins as pkg

    for _finder, mod_name, is_pkg in pkgutil.iter_modules(pkg.__path__):
        if is_pkg or mod_name.startswith("_"):
            continue
        try:
            mod = importlib.import_module(f"{pkg.__name__}.{mod_name}")
        except Exception:
            logger.exception(f"Failed to import built-in slash module '{mod_name}'")
            continue
        builtin = getattr(mod, "BUILTIN", None)
        if builtin is None:
            continue
        if not isinstance(builtin, BuiltinCommand):
            logger.warning(
                f"Module '{mod_name}' exports BUILTIN but it does not satisfy "
                "BuiltinCommand protocol; skipping"
            )
            continue
        out.append(builtin)
    return out
