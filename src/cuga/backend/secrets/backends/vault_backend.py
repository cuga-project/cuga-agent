import os
from typing import Any

from loguru import logger


def _get_client():
    try:
        import hvac
    except ImportError:
        return None
    try:
        from cuga.config import settings

        sec = getattr(settings, "secrets", None)
        if not sec:
            return None
        addr = getattr(sec, "vault_addr", "") or os.environ.get("VAULT_ADDR")
        token_env = getattr(sec, "vault_token_env", "VAULT_TOKEN")
        token = os.environ.get(token_env)
        if not addr or not token:
            return None
        client = hvac.Client(url=addr, token=token)
        if not client.is_authenticated():
            logger.debug("Vault client not authenticated")
            return None
        return client
    except Exception as e:
        logger.debug("Vault client init failed: {}", e)
        return None


def _parse_vault_path(path: str) -> tuple[str, str | None]:
    if "#" in path:
        p, field = path.rsplit("#", 1)
        return p.strip(), field.strip() or None
    return path.strip(), None


def _split_mount_and_path(full_path: str, default_mount: str = "secret") -> tuple[str, str]:
    parts = full_path.strip().split("/")
    if len(parts) >= 2:
        return parts[0], "/".join(parts[1:])
    if len(parts) == 1 and parts[0]:
        return default_mount, parts[0]
    return default_mount, ""


class VaultBackend:
    scheme = "vault"

    def __init__(self):
        self._client = None

    def _client_or_none(self):
        if self._client is None:
            self._client = _get_client()
        return self._client

    def available(self) -> bool:
        return self._client_or_none() is not None

    def list(self, mount: str | None = None) -> list[str]:
        """Return a flat list of secret paths stored in Vault KV."""
        client = self._client_or_none()
        if not client:
            return []
        try:
            from cuga.config import settings

            sec = getattr(settings, "secrets", None)
            mount_point = mount or (getattr(sec, "vault_mount", "secret") if sec else "secret")
            kv_version = getattr(sec, "vault_kv_version", "") if sec else ""
        except Exception:
            mount_point = mount or "secret"
            kv_version = ""
        try:
            if str(kv_version) == "1":
                resp = client.secrets.kv.v1.list_secrets(path="", mount_point=mount_point)
                keys = (resp or {}).get("data", {}).get("keys", [])
            else:
                resp = client.secrets.kv.v2.list_secrets(path="", mount_point=mount_point)
                keys = (resp or {}).get("data", {}).get("keys", [])
            return [k.rstrip("/") for k in keys if isinstance(k, str)]
        except Exception as e:
            logger.debug("Vault list failed: {}", e)
            return []

    def set(
        self,
        path: str,
        value: str,
        *,
        field: str = "value",
        description: str | None = None,
        **kwargs: Any,
    ) -> bool:
        client = self._client_or_none()
        if not client:
            return False
        full_path, _ = _parse_vault_path(path)
        try:
            from cuga.config import settings

            sec = getattr(settings, "secrets", None)
            mount = getattr(sec, "vault_mount", "secret") if sec else "secret"
            kv_version = getattr(sec, "vault_kv_version", "") if sec else ""
        except Exception:
            mount = "secret"
            kv_version = ""
        # When the path already contains the mount (e.g. "secret/my-key"), split it.
        # When the path is just a name (e.g. "my-key"), use the configured mount.
        mount_point, secret_path = _split_mount_and_path(full_path, default_mount=mount)
        if not secret_path:
            return False
        payload: dict[str, Any] = {field: value}
        try:
            if str(kv_version) == "1":
                client.secrets.kv.v1.create_or_update_secret(
                    path=secret_path,
                    secret=payload,
                    mount_point=mount_point,
                )
            else:
                # v2 (default) — posts to /v1/{mount}/data/{path}
                client.secrets.kv.v2.create_or_update_secret(
                    path=secret_path,
                    secret=payload,
                    mount_point=mount_point,
                )
            return True
        except Exception as e:
            logger.debug("Vault write failed: {}", e)
            return False

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
        client = self._client_or_none()
        if not client:
            return None
        full_path, path_field = _parse_vault_path(path)
        key_field = path_field or field
        try:
            from cuga.config import settings

            sec = getattr(settings, "secrets", None)
            mount = getattr(sec, "vault_mount", "secret") if sec else "secret"
            kv_version = getattr(sec, "vault_kv_version", "") if sec else ""
        except Exception:
            mount = "secret"
            kv_version = ""
        mount_point, secret_path = _split_mount_and_path(full_path, default_mount=mount)
        if not secret_path:
            return None
        try:
            if str(kv_version) == "2":
                resp = client.secrets.kv.v2.read_secret_version(
                    path=secret_path,
                    mount_point=mount_point,
                )
                data = (resp or {}).get("data", {}) or {}
                payload = data.get("data", data)
            elif str(kv_version) == "1":
                resp = client.secrets.kv.v1.read_secret(
                    path=secret_path,
                    mount_point=mount_point,
                )
                payload = (resp or {}).get("data", {})
            else:
                try:
                    resp = client.secrets.kv.v2.read_secret_version(
                        path=secret_path,
                        mount_point=mount_point,
                    )
                    data = (resp or {}).get("data", {}) or {}
                    payload = data.get("data", data)
                except Exception:
                    resp = client.secrets.kv.v1.read_secret(
                        path=secret_path,
                        mount_point=mount_point,
                    )
                    payload = (resp or {}).get("data", {})
            if not isinstance(payload, dict):
                return None
            if key_field:
                return payload.get(key_field) or None
            if "value" in payload:
                return payload.get("value")
            return next(iter(payload.values()), None) if payload else None
        except Exception as e:
            logger.debug("Vault read failed: {}", e)
            return None

    def delete(self, path: str) -> bool:
        """Delete a secret from Vault KV. Returns True if deleted, False on error."""
        client = self._client_or_none()
        if not client:
            return False
        full_path, _ = _parse_vault_path(path)
        try:
            from cuga.config import settings

            sec = getattr(settings, "secrets", None)
            mount = getattr(sec, "vault_mount", "secret") if sec else "secret"
            kv_version = getattr(sec, "vault_kv_version", "") if sec else ""
        except Exception:
            mount = "secret"
            kv_version = ""
        mount_point, secret_path = _split_mount_and_path(full_path, default_mount=mount)
        if not secret_path:
            return False
        try:
            if str(kv_version) == "1":
                client.secrets.kv.v1.delete_secret(
                    path=secret_path,
                    mount_point=mount_point,
                )
            else:
                client.secrets.kv.v2.delete_metadata_and_all_versions(
                    path=secret_path,
                    mount_point=mount_point,
                )
            return True
        except Exception as e:
            logger.debug("Vault delete failed: {}", e)
            return False
