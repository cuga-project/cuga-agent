"""Shared FastAPI router for /api/manage endpoints."""

from fastapi import APIRouter, Depends

from cuga.backend.server.auth import require_manage_access

router = APIRouter(
    prefix="/api/manage",
    tags=["manage"],
    dependencies=[Depends(require_manage_access)],
)
