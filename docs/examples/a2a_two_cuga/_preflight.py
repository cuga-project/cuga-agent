"""Preflight for the A2A two-CUGA demo.

Two things this fixes that would otherwise break the demo on a stock
checkout:

1. ``.env`` — bash run scripts don't auto-load it, so we forward MODEL_NAME
   and OPENAI_API_KEY to the child process via env. We also surface them
   in this script's own output so the run scripts can see them.

2. ``cuga.db`` saved config — at lifespan startup, ``_apply_published_config``
   reads the saved row in the ``agent_configs`` table and, if its
   ``llm.model`` is empty, calls ``os.environ.pop("MODEL_NAME", None)``.
   That deletes the value the user just set. We patch the saved row's
   ``llm.model`` to MODEL_NAME so force_env writes it back instead of
   nuking it.

Run with: ``uv run python docs/examples/a2a_two_cuga/_preflight.py``.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DB_PATH = REPO_ROOT / "src" / "cuga" / "dbs" / "cuga.db"

TABLE = "agent_configs"
COLUMN = "config_json"


def _load_dotenv_into_environ() -> None:
    """Load ``.env`` keys into ``os.environ`` without overriding the shell.

    Bash does not auto-source ``.env`` for child processes; the run
    scripts already do, but we also do it here so the preflight works
    when invoked directly. Existing exported values win — we never
    clobber what the operator set in their shell.
    """
    env_file = REPO_ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        # Don't override anything the user already exported.
        os.environ.setdefault(key, val)


def _patch_saved_llm_model(model_name: str) -> None:
    """Rewrite every saved ``llm.model`` in cuga.db to ``model_name``.

    Workaround for cuga-project/cuga-agent#322: at lifespan startup,
    ``_apply_published_config`` calls ``os.environ.pop('MODEL_NAME')``
    when the saved row's ``llm.model`` is empty, wiping the operator's
    configured model. By writing ``model_name`` into the saved row we
    keep the env var alive. Idempotent — running twice is a no-op.
    """
    if not DB_PATH.exists():
        print(f"[preflight] no cuga.db at {DB_PATH} — nothing to patch")
        return
    conn = sqlite3.connect(str(DB_PATH))
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT rowid, {COLUMN} FROM {TABLE}")
        rows = cur.fetchall()
        patched = 0
        for rowid, raw in rows:
            try:
                cfg = json.loads(raw)
            except json.JSONDecodeError as exc:
                print(
                    f"[preflight] skipped rowid={rowid}: invalid JSON ({exc})",
                    file=sys.stderr,
                )
                continue
            llm = cfg.get("llm")
            if not isinstance(llm, dict):
                continue
            if (llm.get("model") or "") == model_name:
                continue
            llm["model"] = model_name
            cur.execute(
                f"UPDATE {TABLE} SET {COLUMN} = ? WHERE rowid = ?",
                (json.dumps(cfg), rowid),
            )
            patched += 1
        conn.commit()
        print(f"[preflight] patched {patched} saved row(s) in {TABLE} with model={model_name!r}")
    finally:
        conn.close()


def main() -> int:
    """Run the .env load + DB patch and report what was done."""
    _load_dotenv_into_environ()
    model_name = os.environ.get("MODEL_NAME") or os.environ.get("OPENAI_MODEL") or ""
    if not model_name:
        print("[preflight] WARNING: MODEL_NAME is unset and .env did not provide one.", file=sys.stderr)
        print("[preflight] Set MODEL_NAME (e.g. 'Azure/gpt-4.1') and re-run.", file=sys.stderr)
        return 1
    print(f"[preflight] MODEL_NAME = {model_name}")
    _patch_saved_llm_model(model_name)
    if "OPENAI_BASE_URL" in os.environ:
        print(f"[preflight] OPENAI_BASE_URL = {os.environ['OPENAI_BASE_URL']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
