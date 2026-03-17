from typing import Dict, Optional, List, Any
from pydantic import BaseModel
from loguru import logger
from cuga.backend.utils.consts import ServiceType
from cuga.backend.utils.file_utils import read_yaml_file


class Auth(BaseModel):
    """
    Authentication configuration supporting multiple auth types.

    Supported types:
    - 'header': Custom header authentication (e.g., X-API-Key)
    - 'bearer': Bearer token authentication (Authorization: Bearer <token>)
    - 'api-key': API key in query parameter
    - 'basic': Basic authentication (Authorization: Basic <base64>)
    - 'query': Custom query parameter authentication

    Examples:
        # Header auth
        Auth(type='header', value='my-api-key', key='X-API-Key')

        # Bearer token
        Auth(type='bearer', value='my-token')

        # API key in query
        Auth(type='api-key', value='my-key', key='api_key')

        # Basic auth
        Auth(type='basic', value='username:password')

        # Custom query param
        Auth(type='query', value='my-value', key='auth_token')
    """

    type: str
    value: Optional[str] = None
    key: Optional[str] = None  # Header name or query param name


class ApiOverride(BaseModel):
    """Configuration for API override"""

    operation_id: str
    description: Optional[str] = None
    drop_request_body_parameters: Optional[List[str]] = None  # Parameters to drop from request body schema
    drop_query_parameters: Optional[List[str]] = None  # Query parameters to drop from operation


class ServiceConfig(BaseModel):
    url: Optional[str] = None
    command: Optional[str] = None
    args: Optional[List[str]] = None
    env: Optional[Dict[str, str]] = None  # Environment variables for STDIO transport
    type: str = ServiceType.OPENAPI  # type of the service
    transport: Optional[str] = None  # Transport type: 'stdio', 'sse', 'http', or None (auto-detect)
    name: Optional[str] = None
    description: Optional[str] = None
    auth: Optional[Any] = None  # Auth type not defined in the snippet
    include: Optional[List[str]] = None  # List of operationIds to include
    api_overrides: Optional[List[ApiOverride]] = None  # List of API overrides
    tools: Optional[List[str]] = (
        None  # list of tools for a specific service - needed in case we get each tool separately
    )


class Service(BaseModel):
    service: Dict[str, ServiceConfig]


class MCPConfig(BaseModel):
    """Standard MCP configuration format"""

    mcpServers: Dict[str, ServiceConfig]


def load_service_configs(yaml_path: str) -> Dict[str, ServiceConfig]:
    """
    Load service configurations from a YAML file into Pydantic models.
    Supports both legacy format (list of services) and standard MCP format (mcpServers).

    Special handling:
    - If yaml_path is "none" or "None", returns empty dict (database mode)
    - Otherwise loads from YAML file

    Args:
        yaml_path: Path to the YAML configuration file, or "none" for database mode

    Returns:
        Dictionary of service configuration objects
    """
    # Handle database mode (no YAML file)
    if yaml_path and yaml_path.lower() == "none":
        logger.info("YAML config set to 'none' - using database mode")
        return {}

    try:
        data = read_yaml_file(yaml_path)
        services = {}

        if isinstance(data, dict):
            # Handle new structure with both 'services' and 'mcpServers' keys
            if 'services' in data:
                # Legacy services under 'services' key
                if data['services'] is not None:
                    for item in data['services']:
                        for service_name, config in item.items():
                            service_config = _create_service_config(service_name, config)
                            services[service_name] = service_config

            if 'mcpServers' in data:
                if data['mcpServers'] is not None:
                    # Standard MCP format
                    mcp_servers = data['mcpServers']
                    for service_name, config in mcp_servers.items():
                        service_config = _create_service_config(service_name, config, is_mcp_server=True)
                        services[service_name] = service_config
        elif isinstance(data, list):
            # Pure legacy format (list at root)
            for item in data:
                for service_name, config in item.items():
                    service_config = _create_service_config(service_name, config)
                    services[service_name] = service_config

        return services
    except Exception as e:
        logger.error(f"Failed to load service configurations from '{yaml_path}': {e}")
        logger.error(
            "Please ensure your YAML file is properly formatted with valid 'services' or 'mcpServers' structure"
        )
        raise ValueError(f"Invalid YAML configuration in '{yaml_path}': {e}")


async def load_service_configs_from_db(agent_id: str = "cuga-default") -> Dict[str, ServiceConfig]:
    """
    Load service configurations from database for a specific agent.
    Reads from agent_configs table which stores the full config including tools.

    Args:
        agent_id: The agent ID to load tools for (default: "cuga-default")

    Returns:
        Dictionary of service configuration objects
    """
    try:
        from cuga.backend.server.managed_mcp import get_tools_from_agent_config

        tools = await get_tools_from_agent_config(agent_id)
        services = {}

        for tool in tools:
            name = tool.get("name")
            if not name:
                continue

            # Create ServiceConfig from tool data
            service_config = _create_service_config(
                name, tool, is_mcp_server=tool.get("type", "mcp").lower() == "mcp"
            )
            services[name] = service_config

        logger.info(f"Loaded {len(services)} services from database for agent '{agent_id}'")
        return services
    except Exception as e:
        logger.error(f"Failed to load service configurations from database for agent '{agent_id}': {e}")
        return {}


def _create_service_config(service_name: str, config: dict, is_mcp_server: bool = False) -> ServiceConfig:
    """Helper function to create ServiceConfig from config dictionary"""
    # Create ServiceConfig with optional auth
    auth_cfg = config.get('auth')
    auth = None
    if auth_cfg:
        auth = Auth(type=auth_cfg['type'], value=auth_cfg.get('value'), key=auth_cfg.get('key'))

    # Handle 'environment' field - support both 'env' and 'environment' keys
    env_config = config.get('env') or config.get('environment')

    # If environment contains headers (like x-api-key) and no auth is configured,
    # convert the first header to auth configuration for HTTP/SSE transports
    if env_config and not auth and isinstance(env_config, dict):
        # Check if this is likely an HTTP/SSE transport (has URL, no command)
        if config.get('url') and not config.get('command'):
            # Convert first header-like key to auth
            for key, value in env_config.items():
                if isinstance(value, str):
                    # Common API key header patterns
                    if key.lower() in ['x-api-key', 'api-key', 'apikey', 'authorization']:
                        if key.lower() == 'authorization':
                            # Check if it's a bearer token
                            if value.startswith('Bearer '):
                                auth = Auth(type='bearer', value=value.replace('Bearer ', ''))
                            else:
                                auth = Auth(type='header', key='Authorization', value=value)
                        else:
                            auth = Auth(type='header', key=key, value=value)
                        break

    # Auto-detect service type if not explicitly specified
    service_type = config.get('type')
    if not service_type:
        if is_mcp_server:
            # Services from mcpServers section are MCP servers
            service_type = ServiceType.MCP_SERVER
        elif config.get('command'):
            # If service has a command, it's an MCP server
            service_type = ServiceType.MCP_SERVER
        elif config.get('tools'):
            # If service has tools list, it's a TRM service
            service_type = ServiceType.TRM
        else:
            # Default to OpenAPI if it has a URL
            service_type = ServiceType.OPENAPI
    else:
        # Normalize the service type to match ServiceType enum values
        service_type_lower = service_type.lower()
        if service_type_lower in ['mcp', 'mcp_server', 'mcp-server']:
            service_type = ServiceType.MCP_SERVER
        elif service_type_lower in ['trm', 'tool-runtime-manager', 'tool_runtime_manager']:
            service_type = ServiceType.TRM
        elif service_type_lower in ['openapi', 'open-api', 'open_api']:
            service_type = ServiceType.OPENAPI
        # If it's already a ServiceType enum, keep it as is
        elif not isinstance(service_type, ServiceType):
            # Try to match it to a ServiceType value
            try:
                service_type = ServiceType(service_type)
            except ValueError:
                # If no match, default based on other config
                if config.get('command'):
                    service_type = ServiceType.MCP_SERVER
                elif config.get('tools'):
                    service_type = ServiceType.TRM
                else:
                    service_type = ServiceType.OPENAPI

    service_config = ServiceConfig(
        name=service_name,
        description=config.get('description'),
        url=config.get('url'),
        command=config.get('command'),
        args=config.get('args'),
        env=env_config,
        transport=config.get('transport'),
        auth=auth,
        include=config.get('include'),
        type=service_type,
        tools=config.get('tools'),
    )

    if 'api_overrides' in config:
        api_overrides = [ApiOverride(**override) for override in config['api_overrides']]
        service_config.api_overrides = api_overrides

    return service_config


# # Example usage
# if __name__ == "__main__":
#     services = load_service_configs("services.yaml")
#     for service in services:
#         for name, config in service.items():
#             print(f"Service: {name}")
#             print(f"  URL: {config.url}")
#             if config.auth:
#                 print(f"  Auth Type: {config.auth.type}")
#             print()
