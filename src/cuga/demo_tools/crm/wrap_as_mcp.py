"""
FastMCP Example - Digital Sales API Integration

This example demonstrates how to create an MCP server from an OpenAPI specification.
The server must be running before starting the main application.
"""

import httpx
from fastmcp import FastMCP
from fastmcp.server.openapi import (
    HTTPRoute,
    OpenAPITool,
    OpenAPIResource,
    OpenAPIResourceTemplate,
)

# Configuration
API_BASE_URL = "http://localhost:8007"
OPENAPI_SPEC_URL = f"{API_BASE_URL}/openapi.json"


def customize_components(
    route: HTTPRoute,
    component: OpenAPITool | OpenAPIResource | OpenAPIResourceTemplate,
) -> None:
    """
    Customize MCP components by adding response schema information to tool descriptions.

    Args:
        route: The HTTP route being processed
        component: The MCP component to customize
    """
    if isinstance(component, OpenAPITool):
        print(component.output_schema)
        component.description = f"{component.description}\n"


def create_mcp_server() -> FastMCP:
    """
    Create and configure the MCP server from OpenAPI specification.

    Returns:
        FastMCP: Configured MCP server instance
    """
    # Create HTTP client for API communication
    client = httpx.AsyncClient(base_url=API_BASE_URL)

    # Load OpenAPI specification
    spec = httpx.get(OPENAPI_SPEC_URL).json()

    # Create MCP server from OpenAPI spec
    mcp = FastMCP.from_openapi(
        openapi_spec=spec,
        client=client,
        mcp_component_fn=customize_components,
    )

    return mcp


def main():
    """Main function to run the MCP server."""
    mcp = create_mcp_server()
    print("Starting FastMCP server...")
    print("Server must be running before starting the main application.")
    mcp.run(transport="sse", port=8111)


if __name__ == "__main__":
    main()
