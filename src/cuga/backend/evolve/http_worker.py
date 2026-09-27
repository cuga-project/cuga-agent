"""Private native Evolve HTTP/MCP host for the bundled container service.

Both transports and the retention scheduler share ONE EvolveClient. In particular,
filesystem storage must not be opened by a second process with a separate lock.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
import hmac
import json
import os

from fastapi import FastAPI, HTTPException, Request


def create_app():
    # Optional dependency: importing CUGA without the Evolve extra remains valid.
    from altk_evolve.frontend.api.memory import MemoryScope, build_memory_router
    from altk_evolve.frontend.mcp.http_transport import create_resilient_sse_app
    from altk_evolve.frontend.mcp.mcp_server import get_client, mcp
    from altk_evolve.retention.scheduler import retention_runtime

    token = os.environ.get("CUGA_EVOLVE_API_TOKEN")
    if not token:
        raise RuntimeError("The private Evolve API requires a service credential")
    mcp_app = create_resilient_sse_app(mcp)

    @asynccontextmanager
    async def lifespan(app):
        client = get_client()
        app.state.evolve_client = client
        try:
            with retention_runtime(client):
                async with mcp_app.router.lifespan_context(mcp_app):
                    yield
        finally:
            client.backend.close()
            app.state.evolve_client = None

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    def scope_dependency(request: Request):
        if not hmac.compare_digest(request.headers.get("authorization", ""), f"Bearer {token}"):
            raise HTTPException(401, "Service authentication required")
        try:
            return MemoryScope.model_validate(json.loads(request.headers.get("x-cuga-memory-scope", "")))
        except (ValueError, TypeError):
            raise HTTPException(400, "Invalid memory scope") from None

    def client_dependency(request: Request):
        client = getattr(request.app.state, "evolve_client", None)
        if client is None:
            raise HTTPException(503, "Evolve memory is unavailable")
        return client

    app.include_router(
        build_memory_router(client_dependency=client_dependency, scope_dependency=scope_dependency),
        prefix="/private",
    )
    # Native memory routes are matched first; the original MCP SSE paths stay intact.
    app.mount("/", mcp_app)
    return app


def main():
    import uvicorn

    uvicorn.run(create_app(), host="127.0.0.1", port=8201, lifespan="on", timeout_graceful_shutdown=3)


if __name__ == "__main__":
    main()
