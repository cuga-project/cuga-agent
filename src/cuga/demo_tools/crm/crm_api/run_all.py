"""Combined entry point to run both the CRM API server and MCP server concurrently."""

import threading
import time
import argparse

import httpx
import uvicorn
from fastmcp import FastMCP
from fastmcp.server.openapi import HTTPRoute, OpenAPITool, OpenAPIResource, OpenAPIResourceTemplate

from .main import app
from .database import init_db


def customize_components(
    route: HTTPRoute,
    component: OpenAPITool | OpenAPIResource | OpenAPIResourceTemplate,
) -> None:
    if isinstance(component, OpenAPITool):
        print(component.output_schema)
        component.description = f"{component.description}\n"


def create_mcp_server(api_base_url: str) -> FastMCP:
    client = httpx.AsyncClient(base_url=api_base_url)
    for attempt in range(30):
        try:
            spec = httpx.get(f"{api_base_url}/openapi.json", timeout=5.0).json()
            break
        except (httpx.RequestError, httpx.HTTPStatusError) as e:
            if attempt < 29:
                print(f"Waiting for API server (attempt {attempt + 1}/30)...")
                time.sleep(1)
            else:
                raise RuntimeError(f"Failed to fetch OpenAPI spec: {e}")
    return FastMCP.from_openapi(openapi_spec=spec, client=client, mcp_component_fn=customize_components)


def main():
    parser = argparse.ArgumentParser(description="CRM System API and MCP Server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8007)
    parser.add_argument("--mcp-port", type=int, default=8111)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    api_base_url = f"http://localhost:{args.port}"
    print("Initializing database...")
    init_db()

    api_thread = threading.Thread(
        target=lambda: uvicorn.run(app, host=args.host, port=args.port, reload=args.reload),
        daemon=True,
    )
    mcp_thread = threading.Thread(
        target=lambda: (
            time.sleep(2),
            create_mcp_server(api_base_url).run(transport="sse", port=args.mcp_port),
        ),
        daemon=True,
    )

    api_thread.start()
    mcp_thread.start()

    print(f"\nAPI server: http://{args.host}:{args.port}")
    print(f"MCP server: SSE on port {args.mcp_port}")
    print("\nPress Ctrl+C to stop.\n")

    try:
        api_thread.join()
        mcp_thread.join()
    except KeyboardInterrupt:
        print("\nShutting down...")


if __name__ == "__main__":
    main()
