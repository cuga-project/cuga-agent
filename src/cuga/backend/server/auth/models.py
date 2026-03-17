from typing import Any, Optional

from pydantic import BaseModel, Field


class UserInfo(BaseModel):
    sub: str = Field(..., description="OIDC subject (unique user id)")
    email: Optional[str] = None
    name: Optional[str] = None
    roles: Optional[list[str]] = None
    raw_claims: Optional[dict[str, Any]] = None


class TokenResponse(BaseModel):
    access_token: str
    id_token: Optional[str] = None
    token_type: str = "Bearer"
    expires_in: Optional[int] = None
    refresh_token: Optional[str] = None
