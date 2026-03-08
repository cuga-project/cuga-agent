import re
from typing import Any

from cuga.backend.storage.secrets_store import get_secret_sync

_ENV_VAR_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")


def _path_to_slug(path: str) -> str:
    if _ENV_VAR_PATTERN.match(path):
        return path.lower().replace("_", "-")
    return path


class EnvOverrideBackend:
    """Resolves env var names from DB overrides (by slug) before falling back to os.environ."""

    scheme = "env"

    def available(self) -> bool:
        from cuga.backend.storage.secrets_store import _fernet

        return _fernet() is not None

    def get(
        self,
        path: str,
        *,
        field: str | None = None,
        agent_id: str | None = None,
        tenant_id: str | None = None,
        instance_id: str | None = None,
        **kwargs: Any,
    ) -> str | None:
        slug = _path_to_slug(path)
        return get_secret_sync(
            slug,
            tenant_id=tenant_id,
            instance_id=instance_id,
            agent_id=agent_id,
        )


class DbBackend:
    scheme = "db"

    def available(self) -> bool:
        from cuga.backend.storage.secrets_store import _fernet

        return _fernet() is not None

    def get(
        self,
        path: str,
        *,
        field: str | None = None,
        agent_id: str | None = None,
        tenant_id: str | None = None,
        instance_id: str | None = None,
        **kwargs: Any,
    ) -> str | None:
        slug = _path_to_slug(path)
        return get_secret_sync(
            slug,
            tenant_id=tenant_id,
            instance_id=instance_id,
            agent_id=agent_id,
        )
