import re
from typing import Tuple

from loguru import logger

from cuga.backend.secrets.backends.env_backend import EnvBackend

_ENV_VAR_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")


def parse_ref(ref: str) -> Tuple[str, str]:
    if not ref or not isinstance(ref, str):
        return "plain", ref or ""
    s = ref.strip()
    if s.startswith("vault://"):
        path = s[8:].strip()
        return "vault", path
    if s.startswith("db://"):
        path = s[5:].strip().split("/")[0].split("#")[0]
        return "db", path
    if s.startswith("aws://"):
        path = s[6:].strip()
        return "aws", path
    if s.startswith("env://"):
        path = s[6:].strip()
        return "env", path
    if _ENV_VAR_PATTERN.match(s):
        return "env", s
    return "plain", s


def _get_secrets_settings():
    try:
        from cuga.config import settings

        return getattr(settings, "secrets", None)
    except Exception:
        return None


def _active_backends():
    sec = _get_secrets_settings()
    if sec and getattr(sec, "force_env", False):
        return [EnvBackend()]
    mode = getattr(sec, "mode", "local") if sec else "local"
    if mode == "vault":
        try:
            from cuga.backend.secrets.backends.vault_backend import VaultBackend

            vb = VaultBackend()
            if vb.available():
                return [vb, EnvBackend()]
        except Exception:
            pass
        return [EnvBackend()]
    backends = []
    try:
        from cuga.backend.secrets.backends.vault_backend import VaultBackend

        vb = VaultBackend()
        if vb.available():
            backends.append(vb)
    except Exception:
        pass
    try:
        from cuga.backend.secrets.backends.aws_backend import AwsBackend

        ab = AwsBackend()
        if ab.available():
            backends.append(ab)
    except Exception:
        pass
    try:
        from cuga.backend.secrets.backends.db_backend import EnvOverrideBackend, DbBackend

        eo = EnvOverrideBackend()
        if eo.available():
            backends.append(eo)
        db = DbBackend()
        if db.available():
            backends.append(db)
    except Exception:
        pass
    backends.append(EnvBackend())
    return backends


def resolve_secret(
    ref: str,
    *,
    agent_id: str | None = None,
    tenant_id: str | None = None,
    instance_id: str | None = None,
) -> str | None:
    if ref is None:
        return None
    if not isinstance(ref, str):
        return str(ref) if ref else None
    ref = ref.strip()
    if not ref:
        return None
    scheme, path = parse_ref(ref)
    if scheme == "plain":
        return path
    backends = _active_backends()
    for backend in backends:
        if getattr(backend, "scheme", None) == scheme:
            try:
                val = backend.get(
                    path,
                    agent_id=agent_id,
                    tenant_id=tenant_id,
                    instance_id=instance_id,
                )
                if val is not None:
                    return val
            except Exception as e:
                logger.debug("Secret backend {} get failed: {}", backend.scheme, e)
    for backend in backends:
        try:
            val = backend.get(
                path,
                agent_id=agent_id,
                tenant_id=tenant_id,
                instance_id=instance_id,
            )
            if val is not None:
                return val
        except Exception as e:
            logger.debug("Secret backend {} get failed: {}", getattr(backend, "scheme", "?"), e)
    return None
