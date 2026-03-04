"""Secrets API: list, create, update, delete overrides; config and resolve."""

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel

from cuga.backend.secrets import resolve_secret
from cuga.backend.server.auth import require_auth
from cuga.backend.server.auth.models import UserInfo
from cuga.backend.storage import secrets_store

router = APIRouter(
    prefix="/api/secrets",
    tags=["secrets"],
    dependencies=[Depends(require_auth)],
)

DEFAULT_USER_ID = "local"


def _user_id(user: Optional[UserInfo]) -> str:
    return (user.sub if user else None) or DEFAULT_USER_ID


class SecretCreate(BaseModel):
    id: str
    value: str
    description: Optional[str] = None
    tags: Optional[dict[str, Any]] = None
    agent_id: Optional[str] = None
    version: Optional[str] = None


class SecretUpdate(BaseModel):
    value: str
    description: Optional[str] = None
    tags: Optional[dict[str, Any]] = None
    agent_id: Optional[str] = None
    version: Optional[str] = None


@router.get("")
async def list_secrets(
    agent_id: Optional[str] = None,
    current_user: Optional[UserInfo] = Depends(require_auth),
) -> dict[str, Any]:
    """List secret metadata (no values). Includes env-var-backed secrets and DB overrides."""
    try:
        import os
        from cuga.config import settings
        from cuga.backend.secrets.seed import _build_seed_map

        SECRET_ENV_SEED_MAP = _build_seed_map()

        sec = getattr(settings, "secrets", None)
        mode = getattr(sec, "mode", "local") if sec else "local"
        force_env = getattr(sec, "force_env", False) if sec else False

        db_items = await secrets_store.list_secrets(agent_id=agent_id)
        db_ids = {item["id"] for item in db_items}

        vault_items = []
        if mode == "vault":
            try:
                from cuga.backend.secrets.backends.vault_backend import VaultBackend

                vb = VaultBackend()
                if vb.available():
                    for key in vb.list():
                        if key not in db_ids:
                            vault_items.append(
                                {
                                    "id": key,
                                    "description": "Stored in Vault",
                                    "source": "vault",
                                    "agent_id": "*",
                                }
                            )
            except Exception:
                pass

        env_items = []
        if mode == "local" or force_env:
            all_ids = db_ids | {v["id"] for v in vault_items}
            for env_var, slug in SECRET_ENV_SEED_MAP.items():
                if os.environ.get(env_var) and slug not in all_ids:
                    env_items.append(
                        {
                            "id": slug,
                            "description": f"From environment variable {env_var}",
                            "source": "env",
                            "agent_id": "*",
                        }
                    )

        items = db_items + vault_items + env_items
        return {
            "secrets": items,
            "overrides": items,
            "mode": mode,
            "force_env": force_env,
        }
    except Exception:
        logger.exception("Failed to list secrets")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/config")
async def get_secrets_config() -> dict[str, Any]:
    """Return mode and force_env for UI."""
    try:
        from cuga.config import settings

        sec = getattr(settings, "secrets", None)
        mode = getattr(sec, "mode", "local") if sec else "local"
        force_env = getattr(sec, "force_env", False) if sec else False
        return {"mode": mode, "force_env": force_env}
    except Exception:
        logger.exception("Failed to get secrets config")
        raise HTTPException(status_code=500, detail="Internal server error")


def _secrets_mode() -> str:
    try:
        from cuga.config import settings

        sec = getattr(settings, "secrets", None)
        return getattr(sec, "mode", "local") if sec else "local"
    except Exception:
        return "local"


def _vault_write(secret_id: str, value: str, description: str | None = None) -> bool:
    try:
        from cuga.backend.secrets.backends.vault_backend import VaultBackend

        vb = VaultBackend()
        if vb.available():
            return vb.set(secret_id, value, description=description)
    except Exception:
        pass
    return False


@router.post("")
async def create_secret(
    body: SecretCreate,
    current_user: Optional[UserInfo] = Depends(require_auth),
) -> dict[str, Any]:
    """Create or upsert a secret override. Writes to Vault when mode=vault."""
    try:
        mode = _secrets_mode()
        if mode == "vault":
            ok = _vault_write(body.id, body.value, description=body.description)
            if not ok:
                raise HTTPException(
                    status_code=503,
                    detail="Vault unavailable or write failed. Check VAULT_ADDR and VAULT_TOKEN.",
                )
            ref = f"vault://secret/{body.id}#value"
            return {"ref": ref, "id": body.id}
        # Check if secret exists and verify ownership before allowing create/update
        meta = await secrets_store.get_secret_metadata(body.id)
        if meta:
            creator = meta.get("created_by") or ""
            if creator and _user_id(current_user) != creator:
                raise HTTPException(status_code=403, detail="Only the creator can update this secret")
        await secrets_store.set_secret(
            body.id,
            body.value,
            description=body.description,
            tags=body.tags,
            agent_id=body.agent_id or "*",
            version=body.version or "*",
            created_by=_user_id(current_user),
        )
        ref = f"db://{body.id}"
        return {"ref": ref, "id": body.id}
    except HTTPException:
        raise
    except RuntimeError as e:
        if "CUGA_SECRET_KEY" in str(e):
            raise HTTPException(status_code=503, detail=str(e))
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.exception("Failed to create secret")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.put("/{secret_id}")
async def update_secret(
    secret_id: str,
    body: SecretUpdate,
    current_user: Optional[UserInfo] = Depends(require_auth),
) -> dict[str, Any]:
    """Update secret value. Writes to Vault when mode=vault."""
    try:
        mode = _secrets_mode()
        if mode == "vault":
            ok = _vault_write(secret_id, body.value, description=body.description)
            if not ok:
                raise HTTPException(status_code=503, detail="Vault unavailable or write failed.")
            return {"ref": f"vault://secret/{secret_id}#value", "id": secret_id}

        # Get scope from body or default to "*"
        agent_id = body.agent_id or "*"
        version = body.version or "*"

        # Fetch metadata with the same scope
        meta = await secrets_store.get_secret_metadata(
            secret_id,
            agent_id=agent_id,
            version=version,
        )
        if not meta:
            raise HTTPException(status_code=404, detail="Secret not found")
        creator = meta.get("created_by") or ""
        if creator and _user_id(current_user) != creator:
            raise HTTPException(status_code=403, detail="Only the creator can update this secret")

        # Update with the same scope
        await secrets_store.set_secret(
            secret_id,
            body.value,
            description=body.description if body.description is not None else meta.get("description"),
            tags=body.tags if body.tags is not None else meta.get("tags"),
            agent_id=agent_id,
            version=version,
            created_by=creator,
        )
        return {"ref": f"db://{secret_id}", "id": secret_id}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to update secret")
        raise HTTPException(status_code=500, detail="Internal server error")


def _vault_delete(secret_id: str) -> bool:
    """Delete a secret from Vault. Returns True if deleted."""
    try:
        from cuga.backend.secrets.backends.vault_backend import VaultBackend

        vb = VaultBackend()
        return vb.available() and vb.delete(secret_id)
    except Exception:
        return False


@router.delete("/{secret_id}")
async def delete_secret(
    secret_id: str,
    agent_id: Optional[str] = None,
    version: Optional[str] = None,
    current_user: Optional[UserInfo] = Depends(require_auth),
) -> dict[str, Any]:
    """Delete a secret override. In vault mode deletes from Vault; in local mode from DB (creator only)."""
    try:
        mode = _secrets_mode()
        if mode == "vault":
            ok = _vault_delete(secret_id)
            if not ok:
                raise HTTPException(status_code=404, detail="Secret not found")
            return {"deleted": secret_id}

        # Get scope from query parameters or default to "*"
        scope_agent_id = agent_id or "*"
        scope_version = version or "*"

        # Fetch metadata with the same scope
        meta = await secrets_store.get_secret_metadata(
            secret_id,
            agent_id=scope_agent_id,
            version=scope_version,
        )
        if not meta:
            raise HTTPException(status_code=404, detail="Secret not found")
        creator = meta.get("created_by") or ""
        if creator and _user_id(current_user) != creator:
            raise HTTPException(status_code=403, detail="Only the creator can delete this secret")

        # Delete with the same scope
        ok = await secrets_store.delete_secret(
            secret_id,
            agent_id=scope_agent_id,
            version=scope_version,
        )
        if not ok:
            raise HTTPException(status_code=404, detail="Secret not found")
        return {"deleted": secret_id}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to delete secret")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/resolve")
async def resolve_secret_debug(
    body: dict[str, str],
    current_user: Optional[UserInfo] = Depends(require_auth),
) -> dict[str, Any]:
    """Resolve a secret reference and return masked value (e.g. sk-••••)."""
    ref = body.get("ref") or body.get("name")
    if not ref:
        raise HTTPException(status_code=400, detail="Provide 'ref' or 'name'")
    val = resolve_secret(ref)
    if val is None:
        return {"resolved": False, "masked": None}
    if len(val) <= 4:
        masked = "••••"
    else:
        masked = val[:4] + "••••" + (val[-2] if len(val) > 6 else "")
    return {"resolved": True, "masked": masked}
