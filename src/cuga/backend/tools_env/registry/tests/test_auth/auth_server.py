#!/usr/bin/env python3
"""
Lightweight FastAPI server for testing authentication.
Supports multiple authentication methods: header, bearer, api-key, basic, and query.
"""

from fastapi import FastAPI, HTTPException, Header, Query
from pydantic import BaseModel
from typing import Optional, List
import base64

app = FastAPI(
    title="Auth Test API",
    description="Simple API for testing various authentication methods",
    version="1.0.0",
)

# Fixed, and deliberately so: mcp_servers_auth_test.yaml and README.md hardcode
# these same values on the client side, so making the server side configurable
# would just desynchronise the two and 401 the whole suite.
VALID_API_KEY = "test-secret-key"  # pragma: allowlist secret
VALID_BEARER_TOKEN = "test-bearer-token"  # pragma: allowlist secret
VALID_BASIC_AUTH = "testuser:testpass"  # pragma: allowlist secret


class Item(BaseModel):
    id: int
    name: str
    description: str


items_db: List[Item] = [
    Item(id=1, name="Item One", description="First test item"),
    Item(id=2, name="Item Two", description="Second test item"),
]


@app.get("/health")
async def health_check():
    """Public health check endpoint - no auth required"""
    return {"status": "healthy", "message": "Auth Test API is running"}


@app.get("/items/header-auth", operation_id="getItemsHeaderAuth")
async def get_items_header_auth(x_api_key: Optional[str] = Header(None)):
    """
    Get items with header authentication.
    Requires: X-API-Key header with value 'test-secret-key'
    """
    if not x_api_key or x_api_key != VALID_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key header")
    return {"items": items_db, "auth_method": "header"}


@app.get("/items/bearer-auth", operation_id="getItemsBearerAuth")
async def get_items_bearer_auth(authorization: Optional[str] = Header(None)):
    """
    Get items with bearer token authentication.
    Requires: Authorization: Bearer test-bearer-token
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization format, expected 'Bearer <token>'")

    token = authorization.replace("Bearer ", "")
    if token != VALID_BEARER_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid bearer token")

    return {"items": items_db, "auth_method": "bearer"}


@app.get("/items/api-key-query", operation_id="getItemsApiKeyQuery")
async def get_items_api_key_query(api_key: Optional[str] = Query(None)):
    """
    Get items with API key in query parameter.
    Requires: ?api_key=test-secret-key
    """
    if not api_key or api_key != VALID_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing api_key query parameter")
    return {"items": items_db, "auth_method": "api-key"}


@app.get("/items/basic-auth", operation_id="getItemsBasicAuth")
async def get_items_basic_auth(authorization: Optional[str] = Header(None)):
    """
    Get items with basic authentication.
    Requires: Authorization: Basic base64(testuser:testpass)
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    if not authorization.startswith("Basic "):
        raise HTTPException(
            status_code=401, detail="Invalid authorization format, expected 'Basic <credentials>'"
        )

    try:
        encoded = authorization.replace("Basic ", "")
        decoded = base64.b64decode(encoded).decode()
        if decoded != VALID_BASIC_AUTH:
            raise HTTPException(status_code=401, detail="Invalid credentials")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid basic auth encoding")

    return {"items": items_db, "auth_method": "basic"}


@app.get("/items/custom-query", operation_id="getItemsCustomQuery")
async def get_items_custom_query(auth_token: Optional[str] = Query(None)):
    """
    Get items with custom query parameter.
    Requires: ?auth_token=test-secret-key
    """
    if not auth_token or auth_token != VALID_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing auth_token query parameter")
    return {"items": items_db, "auth_method": "query"}


@app.post("/items", operation_id="createItem")
async def create_item(item: Item, x_api_key: Optional[str] = Header(None)):
    """
    Create a new item with header authentication.
    Requires: X-API-Key header with value 'test-secret-key'
    """
    if not x_api_key or x_api_key != VALID_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key header")

    if any(i.id == item.id for i in items_db):
        raise HTTPException(status_code=400, detail="Item with this ID already exists")

    items_db.append(item)
    return {"item": item, "auth_method": "header", "message": "Item created successfully"}


if __name__ == "__main__":
    import uvicorn

    print("\nAuth Test API Server starting on http://localhost:8002")
    # Which methods are wired up, never the values. Echoing the credentials here
    # put them in CI logs and terminal scrollback for no benefit — anyone who
    # needs them can read mcp_servers_auth_test.yaml or README.md.
    print("Enabled auth methods: X-API-Key, Bearer token, Basic auth, query key\n")
    uvicorn.run(app, host="0.0.0.0", port=8002, log_level="info")
