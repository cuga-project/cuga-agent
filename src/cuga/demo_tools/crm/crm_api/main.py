import argparse
import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from .database import init_db
from .accounts import router as accounts_router
from .leads import router as leads_router
from .contacts import router as contacts_router
from .opportunities import router as opportunities_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="CRM System API", version="1.0.0", lifespan=lifespan)

app.include_router(accounts_router)
app.include_router(leads_router)
app.include_router(contacts_router)
app.include_router(opportunities_router)


def main():
    parser = argparse.ArgumentParser(description="CRM System API Server")
    parser.add_argument("--host", default="0.0.0.0")
    env_port = os.environ.get("DYNACONF_SERVER_PORTS__CRM_API", "8007")
    print(f"[CRM Server] Reading DYNACONF_SERVER_PORTS__CRM_API from environment: {env_port}")
    parser.add_argument("--port", type=int, default=int(env_port))
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    print(f"[CRM Server] Starting CRM API server on {args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
