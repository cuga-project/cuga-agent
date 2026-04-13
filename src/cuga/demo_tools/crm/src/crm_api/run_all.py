"""
Combined entry point to run both the CRM API server and MCP server concurrently.
"""

import threading
import time
import argparse
import uvicorn
import httpx
from fastmcp import FastMCP
from fastmcp.server.openapi import (
    HTTPRoute,
    OpenAPITool,
    OpenAPIResource,
    OpenAPIResourceTemplate,
)

from crm_api.main import app
from crm_api.database import init_db


def customize_components(
    route: HTTPRoute,
    component: OpenAPITool | OpenAPIResource | OpenAPIResourceTemplate,
) -> None:
    """Customize MCP components by adding response schema information to tool descriptions."""
    if isinstance(component, OpenAPITool):
        print(component.output_schema)
        component.description = f"{component.description}\n"


def create_mcp_server(api_base_url: str) -> FastMCP:
    """Create and configure the MCP server from OpenAPI specification."""
    client = httpx.AsyncClient(base_url=api_base_url)

    max_retries = 30
    retry_delay = 1

    for attempt in range(max_retries):
        try:
            spec = httpx.get(f"{api_base_url}/openapi.json", timeout=5.0).json()
            break
        except (httpx.RequestError, httpx.HTTPStatusError) as e:
            if attempt < max_retries - 1:
                print(f"Waiting for API server to be ready (attempt {attempt + 1}/{max_retries})...")
                time.sleep(retry_delay)
            else:
                raise RuntimeError(f"Failed to fetch OpenAPI spec after {max_retries} attempts: {e}")

    mcp = FastMCP.from_openapi(
        openapi_spec=spec,
        client=client,
        mcp_component_fn=customize_components,
    )

    return mcp


def run_api_server(host: str, port: int, reload: bool):
    """Run the FastAPI server."""
    print(f"Starting CRM API server on {host}:{port}...")
    uvicorn.run(app, host=host, port=port, reload=reload)


def run_mcp_server(api_base_url: str, mcp_port: int):
    """Run the MCP server."""
    time.sleep(2)
    print(f"Starting MCP server on port {mcp_port}...")
    mcp = create_mcp_server(api_base_url)
    mcp.run(transport="sse", port=mcp_port)


def main():
    """Main function to run both servers concurrently."""
    parser = argparse.ArgumentParser(description="CRM System API and MCP Server")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind API server to")
    parser.add_argument("--port", type=int, default=8007, help="Port to bind API server to")
    parser.add_argument("--mcp-port", type=int, default=8111, help="Port to bind MCP server to")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload for API server")

    args = parser.parse_args()

    api_base_url = f"http://localhost:{args.port}"

    print("Initializing database...")
    init_db()

    api_thread = threading.Thread(
        target=run_api_server, args=(args.host, args.port, args.reload), daemon=True
    )

    mcp_thread = threading.Thread(target=run_mcp_server, args=(api_base_url, args.mcp_port), daemon=True)

    api_thread.start()
    mcp_thread.start()

    print("\nBoth servers are starting...")
    print(f"API server: http://{args.host}:{args.port}")
    print(f"MCP server: SSE transport on port {args.mcp_port}")
    print("\nPress Ctrl+C to stop both servers.\n")

    try:
        api_thread.join()
        mcp_thread.join()
    except KeyboardInterrupt:
        print("\nShutting down servers...")


if __name__ == "__main__":
    main()
