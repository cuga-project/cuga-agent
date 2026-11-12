"""Resolve the SQLite file used by the local durable checkpointer (issue #774).

The checkpointer gets its own file next to ``storage.local_db_path``'s default
(``DBS_DIR/cuga.db``) so LangGraph writes do not contend for the SQLite write
lock held by ``LocalRelationalStore``.
"""

from pathlib import Path
from typing import Any

CHECKPOINT_DB_FILENAME = "checkpoints.db"


def _dbs_dir() -> Path:
    # Read ``cuga.config.DBS_DIR`` at call time: it is the single source of the
    # databases directory (``CUGA_DBS_DIR`` or the package ``dbs/``), the same
    # value ``storage.facade._local_db_path`` uses for the default ``cuga.db``.
    import cuga.config as cuga_config

    return Path(cuga_config.DBS_DIR)


def resolve_checkpoint_db_path(storage_settings: Any) -> Path:
    """Return the checkpoint SQLite path for ``settings.storage`` and create its parent.

    - A leading ``~`` in ``storage.checkpoint_db_path`` is expanded first.
    - ``storage.checkpoint_db_path`` set and absolute: used as is.
    - ``storage.checkpoint_db_path`` set and relative: resolved against ``DBS_DIR``,
      matching how ``[policy].policy_db_path`` is resolved.
    - unset or blank: ``DBS_DIR / "checkpoints.db"``.
    """
    configured = (getattr(storage_settings, "checkpoint_db_path", "") or "").strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute():
            path = _dbs_dir() / path
    else:
        path = _dbs_dir() / CHECKPOINT_DB_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
