"""
Tool Registry Provider

Provides tools from the MCP registry (separate process).
"""

import aiohttp
import json
import asyncio
from typing import List, Dict, Any, Optional
from loguru import logger
from pydantic import create_model, Field
from langchain_core.tools import StructuredTool

from cuga.backend.tools_env.registry.utils.api_utils import get_apis, get_apps, get_registry_base_url
from cuga.backend.cuga_graph.nodes.cuga_lite.tool_provider_interface import (
    ToolProviderInterface,
    AppDefinition,
)
from cuga.config import settings


async def call_api(
    app_name: str,
    api_name: str,
    args: Dict[str, Any] = None,
    operation_id: Optional[str] = None,
    agent_id: Optional[str] = None,
):
    """Call an API tool via the registry server.

    Args:
        app_name: Name of the app/server
        api_name: Name of the API/tool
        args: Arguments to pass to the API
        operation_id: Optional original OpenAPI operationId for tracking
        agent_id: Optional agent ID for multi-agent support

    Returns:
        The API response
    """
    import time
    from cuga.backend.cuga_graph.nodes.cuga_lite.tool_call_tracker import ToolCallTracker

    if args is None:
        args = {}

    registry_base = get_registry_base_url()
    registry_host = f'{registry_base}/functions/call'

    # Add agent_id query parameter if provided
    if agent_id:
        registry_host += f'?agent_id={agent_id}'

    payload = {"function_name": api_name, "app_name": app_name, "args": args}

    timeout_seconds = getattr(settings.advanced_features, 'tool_call_timeout', 30)
    start_time = time.time()
    result = None
    error_msg = None

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                registry_host,
                json=payload,
                headers={"accept": "application/json", "Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=timeout_seconds),
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    error_msg = f"HTTP Error: {response.status} - {error_text}"
                    raise Exception(error_msg)

                response_data = await response.text()
                try:
                    result = json.loads(response_data)
                except json.JSONDecodeError:
                    result = response_data
                return result
    except asyncio.TimeoutError:
        error_msg = f"Tool call '{api_name}' timed out after {timeout_seconds} seconds"
        raise TimeoutError(error_msg)
    except Exception as e:
        if not error_msg:
            error_msg = f"Error calling API {api_name}: {str(e)}"
        raise Exception(error_msg)
    finally:
        duration_ms = (time.time() - start_time) * 1000
        ToolCallTracker.record_call(
            tool_name=api_name,
            arguments=args,
            result=result,
            app_name=app_name,
            operation_id=operation_id,
            duration_ms=duration_ms,
            error=error_msg,
        )


def _convert_openapi_params_to_json_schema(parameters: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Convert OpenAPI parameter format to JSON schema format.

    Args:
        parameters: List of parameter dicts in OpenAPI format

    Returns:
        Dict in JSON schema format with 'properties' and 'required' keys
    """
    if not isinstance(parameters, list):
        return parameters  # Already in JSON schema format

    properties = {}
    required = []

    for param in parameters:
        param_name = param.get('name', '')
        if not param_name:
            continue

        properties[param_name] = {
            'type': param.get('type', 'string'),
            'description': param.get('description', ''),
        }

        # Handle default values
        default_val = param.get('default')
        if default_val is not None:
            properties[param_name]['default'] = default_val

        # Handle constraints
        constraints = param.get('constraints', [])
        if constraints:
            properties[param_name]['constraints'] = constraints

        # Handle required
        if param.get('required', False):
            required.append(param_name)

    return {'properties': properties, 'required': required}


def create_tool_from_api_dict(
    tool_name: str, tool_def: Dict[str, Any], app_name: str, agent_id: Optional[str] = None
) -> StructuredTool:
    """Create a StructuredTool from an API definition dict.

    Args:
        tool_name: Name of the tool
        tool_def: Tool definition dict from get_apis
        app_name: Name of the app/server
        agent_id: Optional agent ID for multi-agent support

    Returns:
        StructuredTool instance with .func attribute
    """
    description = tool_def.get('description', '')
    parameters = tool_def.get('parameters', {})
    response_schemas = tool_def.get('response_schemas', {})
    operation_id = tool_def.get('operation_id')  # Original OpenAPI operationId

    # Convert OpenAPI parameter format to JSON schema format if needed
    if isinstance(parameters, list):
        parameters = _convert_openapi_params_to_json_schema(parameters)

    field_definitions = {}
    param_constraints = {}
    if isinstance(parameters, dict):
        if 'properties' in parameters:
            props = parameters['properties']
            required = parameters.get('required', [])
            for param_name, param_schema in props.items():
                param_type = param_schema.get('type', 'string')
                param_desc = param_schema.get('description', '')

                # Handle type that might be a list (e.g., ['string', 'null'])
                if isinstance(param_type, list):
                    # Take the first non-null type, or default to 'string'
                    param_type = next((t for t in param_type if t != 'null'), 'string')

                type_mapping = {
                    'string': str,
                    'integer': int,
                    'number': float,
                    'boolean': bool,
                    'array': list,
                    'object': dict,
                }
                python_type = type_mapping.get(param_type, str)

                # Store constraints for later use in prompt
                constraints = param_schema.get('constraints', [])
                if constraints:
                    param_constraints[param_name] = constraints

                if param_name in required:
                    field_definitions[param_name] = (python_type, Field(..., description=param_desc))
                else:
                    default_val = param_schema.get('default', None)
                    # Make sure default values are hashable if needed
                    if isinstance(default_val, list):
                        default_val = None  # Skip unhashable defaults
                    field_definitions[param_name] = (
                        python_type,
                        Field(default=default_val, description=param_desc),
                    )

    if field_definitions:
        InputModel = create_model(f"{tool_name}Input", **field_definitions)
    else:
        InputModel = create_model(f"{tool_name}Input")

    # Capture operation_id and agent_id in closure for the tool function
    _operation_id = operation_id
    _agent_id = agent_id

    async def tool_func(*args, **kwargs):
        try:
            # Combine positional and keyword arguments
            all_kwargs = {}
            param_names = list(field_definitions.keys()) if field_definitions else []

            # Map positional arguments to parameter names
            for i, arg in enumerate(args):
                if i < len(param_names):
                    all_kwargs[param_names[i]] = arg
                else:
                    # If more positional args than expected, add them as extra
                    all_kwargs[f"arg{i}"] = arg

            # Add keyword arguments
            all_kwargs.update(kwargs)

            # Call API with timeout (timeout is handled inside call_api)
            result = await call_api(
                app_name, tool_name, all_kwargs, operation_id=_operation_id, agent_id=_agent_id
            )
            return result
        except TimeoutError:
            raise
        except Exception as e:
            error_msg = f"Error calling {tool_name}: {str(e)}"
            logger.error(error_msg)
            return {"error": error_msg}

    tool_func.__name__ = tool_name
    tool_func.__doc__ = description

    tool = StructuredTool.from_function(
        func=tool_func, name=tool_name, description=description, args_schema=InputModel
    )

    tool.func = tool_func

    if not hasattr(tool.func, '_response_schemas'):
        tool.func._response_schemas = response_schemas

    if not hasattr(tool.func, '_param_constraints'):
        tool.func._param_constraints = param_constraints

    # Store metadata for tool call tracking
    tool.func._operation_id = operation_id
    tool.func._app_name = app_name

    return tool


class ToolRegistryProvider(ToolProviderInterface):
    """
    Tool provider that loads tools from the MCP registry.

    This provider connects to the registry server to get apps and tools.
    Tools are loaded from OpenAPI specs, MCP servers, or TRM services.
    """

    def __init__(self, app_names: Optional[List[str]] = None, agent_id: Optional[str] = None):
        """
        Initialize the registry provider.

        Args:
            app_names: Optional list of specific app names to load. If None, loads all.
            agent_id: Optional agent ID for multi-agent support
        """
        self.app_names = app_names
        self.agent_id = agent_id
        self.apps: List[AppDefinition] = []
        self.tools_cache: Dict[str, List[StructuredTool]] = {}
        self.initialized = False

    async def initialize(self):
        """Load apps from the registry."""
        logger.info("Initializing ToolRegistryProvider...")

        all_apps = await get_apps()
        if not all_apps:
            raise Exception("No apps found in registry")

        if self.app_names:
            filtered_apps = [app for app in all_apps if app.name in self.app_names]
            if not filtered_apps:
                raise Exception(f"None of the requested apps found: {self.app_names}")
            self.apps = [
                AppDefinition(
                    name=app.name, url=app.url, description=app.description, type=getattr(app, 'type', 'api')
                )
                for app in filtered_apps
            ]
        else:
            self.apps = [
                AppDefinition(
                    name=app.name, url=app.url, description=app.description, type=getattr(app, 'type', 'api')
                )
                for app in all_apps
            ]

        logger.info(f"Found {len(self.apps)} apps: {[app.name for app in self.apps]}")
        self.initialized = True

    async def get_apps(self) -> List[AppDefinition]:
        """Get list of available applications."""
        if not self.initialized:
            await self.initialize()
        return self.apps

    async def get_tools(self, app_name: str) -> List[StructuredTool]:
        """
        Get tools for a specific application.

        Args:
            app_name: Name of the application

        Returns:
            List of LangChain StructuredTool objects
        """
        if not self.initialized:
            await self.initialize()

        if app_name in self.tools_cache:
            return self.tools_cache[app_name]

        logger.info(f"Loading tools for app: {app_name}")
        api_dicts = await get_apis(app_name)

        if not api_dicts:
            logger.warning(f"No APIs found for app '{app_name}'")
            return []

        tools = []
        logger.info(f"Converting {len(api_dicts)} APIs to tools for '{app_name}'")
        for tool_name, tool_def in api_dicts.items():
            try:
                tool = create_tool_from_api_dict(tool_name, tool_def, app_name, agent_id=self.agent_id)
                tools.append(tool)
                logger.debug(f"  ✓ {tool_name}")
            except Exception as e:
                logger.warning(f"  ✗ Failed to create tool {tool_name}: {e}")
                continue

        self.tools_cache[app_name] = tools
        return tools

    async def get_all_tools(self) -> List[StructuredTool]:
        """Get all available tools from all applications."""
        if not self.initialized:
            await self.initialize()

        all_tools = []
        for app in self.apps:
            tools = await self.get_tools(app.name)
            all_tools.extend(tools)

        logger.info(f"Loaded {len(all_tools)} total tools from {len(self.apps)} apps")
        return all_tools
