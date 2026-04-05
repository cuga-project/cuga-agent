import os
from typing import Any

from loguru import logger

_DEFAULT_SA_JWT = "/var/run/secrets/kubernetes.io/serviceaccount/token"


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _vault_verify(sec: Any) -> bool | str:
    skip = getattr(sec, "vault_skip_verify", False)
    if isinstance(skip, str):
        skip = skip.strip().lower() in ("1", "true", "yes", "on")
    if skip or _env_truthy("VAULT_SKIP_VERIFY"):
        logger.warning("Vault TLS verification is disabled (vault_skip_verify / VAULT_SKIP_VERIFY)")
        return False
    path = (getattr(sec, "vault_cacert", "") or os.environ.get("VAULT_CACERT") or "").strip()
    if path and os.path.isfile(path):
        return path
    if path:
        logger.debug("Vault TLS: CA file missing or not a file: {}", path)
    return True


def _vault_addr_and_auth(sec: Any) -> tuple[str, str | None]:
    addr = (getattr(sec, "vault_addr", "") or os.environ.get("VAULT_ADDR") or "").strip()
    raw = getattr(sec, "vault_auth_method", "") or os.environ.get("VAULT_AUTH_METHOD") or "token"
    method = str(raw).strip().lower() or "token"
    if method == "kubernetes":
        return addr, "kubernetes"
    if method == "token":
        return addr, "token"
    return addr, None


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
        addr, auth_method = _vault_addr_and_auth(sec)
        if not addr:
            return None
        if auth_method is None:
            configured = getattr(sec, "vault_auth_method", "") or os.environ.get("VAULT_AUTH_METHOD") or ""
            logger.debug(
                "Vault: unsupported auth method {!r} (supported: kubernetes, token)",
                str(configured).strip() or "(empty)",
            )
            return None
        verify = _vault_verify(sec)

        if auth_method == "kubernetes":
            role = (getattr(sec, "vault_k8s_role", "") or os.environ.get("VAULT_K8S_ROLE") or "").strip()
            mount = (
                getattr(sec, "vault_k8s_mount_path", "")
                or os.environ.get("VAULT_K8S_MOUNT_PATH")
                or "kubernetes"
            ).strip() or "kubernetes"
            jwt_path = (
                getattr(sec, "vault_k8s_jwt_path", "")
                or os.environ.get("VAULT_K8S_JWT_PATH")
                or _DEFAULT_SA_JWT
            ).strip() or _DEFAULT_SA_JWT
            if not role:
                logger.debug("Vault kubernetes auth: missing role (secrets.vault_k8s_role / VAULT_K8S_ROLE)")
                return None
            try:
                with open(jwt_path, encoding="utf-8") as f:
                    jwt = f.read().strip()
            except OSError as e:
                logger.debug("Vault kubernetes auth: cannot read JWT at {}: {}", jwt_path, e)
                return None
            if not jwt:
                return None
            client = hvac.Client(url=addr, verify=verify)
            client.auth.kubernetes.login(role=role, jwt=jwt, mount_point=mount)
            if not client.is_authenticated():
                logger.debug("Vault kubernetes login did not yield an authenticated client")
                return None
            return client

        elif auth_method == "token":
            token_env = getattr(sec, "vault_token_env", "VAULT_TOKEN")
            token = os.environ.get(token_env)
            if not token:
                return None
            client = hvac.Client(url=addr, token=token, verify=verify)
            if not client.is_authenticated():
                logger.debug("Vault client not authenticated")
                return None
            return client

        else:
            logger.debug("Vault: unexpected auth method {!r}", auth_method)
            return None
    except Exception as e:
        logger.debug("Vault client init failed: {}", e)
        return None


def _parse_vault_path(path: str) -> tuple[str, str | None]:
    if "#" in path:
        p, field = path.rsplit("#", 1)
        return p.strip(), field.strip() or None
    return path.strip(), None


def _normalize_kv_v2_data_prefix(rest: str) -> str:
    if rest.startswith("data/"):
        return rest[len("data/") :]
    return rest


def _split_mount_and_path(
    full_path: str, default_mount: str = "secret", *, kv_version: str = ""
) -> tuple[str, str]:
    parts = full_path.strip().split("/")
    if len(parts) >= 2:
        mount = parts[0]
        rest = "/".join(parts[1:])
        # hvac KV v2 prepends "data/" — strip only for v2 (v1 may use "data/" as a real path)
        if str(kv_version) != "1":
            rest = _normalize_kv_v2_data_prefix(rest)
        return mount, rest
    if len(parts) == 1 and parts[0]:
        return default_mount, parts[0]
    return default_mount, ""


def _merge_vault_secret_base(path_arg: str, vault_secret_path: str) -> str:
    path_arg = (path_arg or "").strip()
    base = (vault_secret_path or "").strip()
    if base and path_arg and "/" not in path_arg:
        return base.rstrip("/") + "/" + path_arg.lstrip("/")
    return path_arg


def _vault_list_prefix(vault_secret_path: str, mount_point: str, kv_version: str) -> str:
    if not (vault_secret_path or "").strip():
        return ""
    raw = vault_secret_path.strip().lstrip("/")
    mp = mount_point.strip("/")
    if mp and raw.startswith(mp + "/"):
        raw = raw[len(mp) + 1 :]
    if str(kv_version) != "1":
        raw = _normalize_kv_v2_data_prefix(raw)
    return raw


def _resolve_vault_path(
    secret_id: str,
    vault_secret_path: str,
    mount_point: str,
    kv_version: str,
) -> tuple[str, str, str]:
    """Return (mount_point, crud_secret_path, list_prefix).

    ``crud_secret_path`` is the path for KV read/write/delete (hvac; mount
    omitted). ``list_prefix`` is the directory path for list_secrets under the
    configured base (``vault_secret_path``). For a bare id with no slash,
    ``secret_id`` is joined with ``vault_secret_path`` like set() historically did.
    """
    mp = (mount_point or "").strip() or "secret"
    kv = str(kv_version)
    list_prefix = _vault_list_prefix(vault_secret_path, mp, kv)

    sid = (secret_id or "").strip()
    if not sid:
        return mp, "", list_prefix

    merged = _merge_vault_secret_base(sid, vault_secret_path)
    crud_mount, crud_path = _split_mount_and_path(merged, default_mount=mp, kv_version=kv)
    return crud_mount, crud_path, list_prefix


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
            secret_path = getattr(sec, "vault_secret_path", "") if sec else ""
        except Exception:
            mount_point = mount or "secret"
            kv_version = ""
            secret_path = ""

        list_mount, _, list_path = _resolve_vault_path("", secret_path, mount_point, kv_version)

        try:
            if str(kv_version) == "1":
                resp = client.secrets.kv.v1.list_secrets(path=list_path, mount_point=list_mount)
                keys = (resp or {}).get("data", {}).get("keys", [])
            else:
                resp = client.secrets.kv.v2.list_secrets(path=list_path, mount_point=list_mount)
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
            base_path = getattr(sec, "vault_secret_path", "") if sec else ""
        except Exception:
            mount = "secret"
            kv_version = ""
            base_path = ""
        mount_point, secret_path, _ = _resolve_vault_path(full_path, base_path, mount, kv_version)
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
            base_path = getattr(sec, "vault_secret_path", "") if sec else ""
        except Exception:
            mount = "secret"
            kv_version = ""
            base_path = ""
        mount_point, secret_path, _ = _resolve_vault_path(full_path, base_path, mount, kv_version)
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
            base_path = getattr(sec, "vault_secret_path", "") if sec else ""
        except Exception:
            mount = "secret"
            kv_version = ""
            base_path = ""
        mount_point, secret_path, _ = _resolve_vault_path(full_path, base_path, mount, kv_version)
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
