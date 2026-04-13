from fastapi import FastAPI
from contextlib import asynccontextmanager
import uvicorn
import argparse

from crm_api.database import init_db
from crm_api.accounts import router as accounts_router
from crm_api.leads import router as leads_router
from crm_api.contacts import router as contacts_router
from crm_api.opportunities import router as opportunities_router
import os


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    init_db()
    yield
    # Shutdown (if needed)


app = FastAPI(title="CRM System API", version="1.0.0", lifespan=lifespan)

# Include routers for different API categories
app.include_router(accounts_router)
app.include_router(leads_router)
app.include_router(contacts_router)
app.include_router(opportunities_router)


def main():
    """Main entry point for running the CRM API server"""
    parser = argparse.ArgumentParser(description="CRM System API Server")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")

    # Get port from env var with fallback to default
    env_port = os.environ.get("DYNACONF_SERVER_PORTS__CRM_API", "8007")
    print(f"[CRM Server] Reading DYNACONF_SERVER_PORTS__CRM_API from environment: {env_port}")

    parser.add_argument(
        "--port",
        type=int,
        default=int(env_port),
        help="Port to bind to",
    )
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload")

    args = parser.parse_args()

    print(f"[CRM Server] Starting CRM API server on {args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
