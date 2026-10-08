"""Persistent, private storage for the local manager (stdlib-only bootstrap)."""

import base64
import os
from pathlib import Path
import secrets


def data_directory() -> Path:
    if os.environ.get("CUGA_DATA_DIR"):
        return Path(os.environ["CUGA_DATA_DIR"]).expanduser().resolve()
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "cuga"


def prepare_local_manager() -> Path:
    """Run before importing settings; never overwrite a key or explicit settings."""
    root = data_directory()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.environ.setdefault("CUGA_LOCAL_MANAGER", "true")
    os.environ.setdefault("CUGA_DATA_DIR", str(root))
    os.environ.setdefault("CUGA_DBS_DIR", str(root / "dbs"))
    os.environ.setdefault("CUGA_LOGGING_DIR", str(root / "logs"))
    os.environ.setdefault("DYNACONF_STORAGE__PRESERVE_CONFIGS_ON_STARTUP", "any")
    os.environ.setdefault("DYNACONF_KNOWLEDGE__PERSIST_DIR", str(root / "knowledge"))
    os.environ.setdefault("CUGA_WORKSPACE_PATH", str(root / "workspace"))
    if not os.environ.get("CUGA_SECRET_KEY"):
        key_path = root / "secret.key"
        try:
            fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "w") as key_file:
                key_file.write(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii"))
        if key_path.stat().st_mode & 0o077:
            raise RuntimeError(f"{key_path} must be private: run chmod 600 on this file")
        key = key_path.read_text().strip()
        if len(base64.urlsafe_b64decode(key)) != 32:
            raise RuntimeError(f"Invalid encryption key in {key_path}; restore your original key")
        os.environ["CUGA_SECRET_KEY"] = key
    return root
