"""
Convert CUGA tools to Toolguard's OpenAPI Specification format.

This module provides functionality to convert tools from CUGA's ToolProviderInterface
or CombinedToolProvider into Toolguard's OpenAPI format, which can then be converted
to ToolInfo objects for policy enforcement.

Supports complex return types including:
- Pydantic models (automatically converted to JSON schema)
- List[PydanticModel] (arrays with item schemas)
- Dict[str, Any] (nested object structures)
- Simple types (str, int, bool, etc.)
"""

from typing import Any, Dict, List, Union, get_origin, get_args
import inspect

from langchain_core.tools import StructuredTool
from loguru import logger
from pydantic import BaseModel, Field

from cuga.backend.cuga_graph.nodes.cuga_lite.combined_tool_provider import CombinedToolProvider
from cuga.backend.cuga_graph.nodes.cuga_lite.tool_provider_interface import ToolProviderInterface

try:
    from toolguard.buildtime.utils.open_api import (
        Components,
        Info,
        MediaType,
        OpenAPI,
        Operation,
        Parameter,
        ParameterIn,
        PathItem,
        RequestBody,
        Response,
        Server,
    )
    from toolguard.buildtime.utils.jschema import JSchema, JSONSchemaTypes
except ImportError as e:
    raise ImportError(
        "toolguard is required for this module. Install it with: pip install toolguard"
    ) from e


def _extract_json_schema_from_pydantic(args_schema) -> Dict[str, Any]:
    """
    Extract JSON schema from a Pydantic model.
    
    Args:
        args_schema: Pydantic model class
        
    Returns:
        JSON schema dictionary
    """
    if args_schema is None:
        return {"type": "object", "properties": {}, "required": []}
    
    try:
        # Get the JSON schema from the Pydantic model
        schema = args_schema.model_json_schema()
        return schema
    except Exception as e:
        logger.warning(f"Failed to extract JSON schema from args_schema: {e}")
        return {"type": "object", "properties": {}, "required": []}


def _extract_return_type_schema(tool: StructuredTool) -> Dict[str, Any]:
    """
    Extract JSON schema from a tool's return type annotation.
    
    Supports:
    - Pydantic models (BaseModel subclasses)
    - List[PydanticModel]
    - Dict[str, Any]
    - Simple types (str, int, bool, float)
    
    Args:
        tool: StructuredTool instance
        
    Returns:
        JSON schema dictionary representing the return type
    """
    try:
        # Get the function from the tool
        if not hasattr(tool, "func"):
            logger.debug(f"Tool {tool.name} has no func attribute")
            return {"type": "object", "description": "Tool execution result"}
        
        func = tool.func
        if func is None:
            logger.debug(f"Tool {tool.name} func is None")
            return {"type": "object", "description": "Tool execution result"}
        
        # Get the return type annotation
        sig = inspect.signature(func)
        return_annotation = sig.return_annotation
        
        if return_annotation is inspect.Signature.empty:
            logger.debug(f"Tool {tool.name} has no return type annotation")
            return {"type": "object", "description": "Tool execution result"}
        
        # Handle string annotations (forward references)
        if isinstance(return_annotation, str):
            logger.debug(f"Tool {tool.name} has string return annotation: {return_annotation}")
            return {"type": "object", "description": f"Returns {return_annotation}"}
        
        # Check if it's a Pydantic model
        if inspect.isclass(return_annotation) and issubclass(return_annotation, BaseModel):
            logger.debug(f"Tool {tool.name} returns Pydantic model: {return_annotation.__name__}")
            schema = return_annotation.model_json_schema()
            # Remove $defs if present and inline them
            if "$defs" in schema:
                defs = schema.pop("$defs")
                # Resolve any $ref references
                schema = _resolve_schema_refs(schema, defs)
            return schema
        
        # Check if it's a List type
        origin = get_origin(return_annotation)
        if origin is list or origin is List:
            args = get_args(return_annotation)
            if args:
                item_type = args[0]
                # Check if list item is a Pydantic model
                if inspect.isclass(item_type) and issubclass(item_type, BaseModel):
                    logger.debug(f"Tool {tool.name} returns List[{item_type.__name__}]")
                    item_schema = item_type.model_json_schema()
                    # Remove $defs and inline
                    if "$defs" in item_schema:
                        defs = item_schema.pop("$defs")
                        item_schema = _resolve_schema_refs(item_schema, defs)
                    return {
                        "type": "array",
                        "items": item_schema,
                        "description": f"List of {item_type.__name__} objects"
                    }
                else:
                    # Simple list type
                    return {
                        "type": "array",
                        "items": {"type": _python_type_to_json_type(item_type)},
                        "description": f"List of {item_type.__name__ if hasattr(item_type, '__name__') else str(item_type)}"
                    }
            return {"type": "array", "description": "List of items"}
        
        # Check if it's a Dict type
        if origin is dict or origin is Dict:
            logger.debug(f"Tool {tool.name} returns Dict")
            return {
                "type": "object",
                "description": "Dictionary with dynamic keys",
                "additionalProperties": True
            }
        
        # Handle simple types
        simple_type = _python_type_to_json_type(return_annotation)
        if simple_type != "object":
            logger.debug(f"Tool {tool.name} returns simple type: {simple_type}")
            return {"type": simple_type, "description": f"Returns {simple_type}"}
        
        # Default fallback
        logger.debug(f"Tool {tool.name} has unhandled return type: {return_annotation}")
        return {"type": "object", "description": "Tool execution result"}
        
    except Exception as e:
        logger.warning(f"Failed to extract return type schema for tool {tool.name}: {e}")
        return {"type": "object", "description": "Tool execution result"}


def _python_type_to_json_type(python_type) -> str:
    """Convert Python type to JSON schema type string."""
    type_mapping = {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
        list: "array",
        dict: "object",
    }
    return type_mapping.get(python_type, "object")


def _resolve_schema_refs(schema: Dict[str, Any], defs: Dict[str, Any]) -> Dict[str, Any]:
    """
    Resolve $ref references in a schema by inlining definitions.
    
    Args:
        schema: Schema that may contain $ref
        defs: Definitions to resolve references from
        
    Returns:
        Schema with references resolved
    """
    if isinstance(schema, dict):
        if "$ref" in schema:
            # Extract the reference name
            ref = schema["$ref"]
            if ref.startswith("#/$defs/"):
                def_name = ref.split("/")[-1]
                if def_name in defs:
                    # Return the resolved definition (recursively resolve it too)
                    return _resolve_schema_refs(defs[def_name], defs)
            return schema
        else:
            # Recursively resolve in nested structures
            return {k: _resolve_schema_refs(v, defs) for k, v in schema.items()}
    elif isinstance(schema, list):
        return [_resolve_schema_refs(item, defs) for item in schema]
    else:
        return schema


def _dict_schema_to_jschema(schema_dict: Dict[str, Any]) -> JSchema:
    """
    Convert a dictionary JSON schema to a JSchema object.
    
    Args:
        schema_dict: Dictionary containing JSON schema
        
    Returns:
        JSchema object
    """
    # Extract basic properties
    schema_type = schema_dict.get("type", "object")
    description = schema_dict.get("description", "")
    
    # Map string type to JSONSchemaTypes enum
    try:
        json_type = JSONSchemaTypes(schema_type)
    except ValueError:
        json_type = JSONSchemaTypes.object
    
    # Build JSchema kwargs
    jschema_kwargs = {
        "type": json_type,
        "description": description,
    }
    
    # Handle properties for object types
    if "properties" in schema_dict:
        properties = {}
        for prop_name, prop_schema in schema_dict["properties"].items():
            # Recursively convert nested schemas
            if isinstance(prop_schema, dict):
                properties[prop_name] = _dict_schema_to_jschema(prop_schema)
            else:
                properties[prop_name] = prop_schema
        jschema_kwargs["properties"] = properties
    
    # Handle required fields
    if "required" in schema_dict:
        jschema_kwargs["required"] = schema_dict["required"]
    
    # Handle array items
    if "items" in schema_dict:
        items_schema = schema_dict["items"]
        if isinstance(items_schema, dict):
            jschema_kwargs["items"] = _dict_schema_to_jschema(items_schema)
        else:
            jschema_kwargs["items"] = items_schema
    
    # Handle additionalProperties for dynamic objects
    if "additionalProperties" in schema_dict:
        jschema_kwargs["additionalProperties"] = schema_dict["additionalProperties"]
    
    return JSchema(**jschema_kwargs)


def _convert_tool_to_operation(tool: StructuredTool, app_name: str) -> tuple[str, Operation]:
    """
    Convert a single CUGA StructuredTool to an OpenAPI Operation.
    
    Args:
        tool: LangChain StructuredTool instance
        app_name: Name of the application/service providing the tool
        
    Returns:
        Tuple of (operation_id, Operation)
    """
    # Extract tool metadata
    tool_name = tool.name
    description = tool.description or ""
    
    # Get operation_id from tool metadata if available
    operation_id = getattr(tool.func, "_operation_id", None) if hasattr(tool, "func") else None
    if not operation_id:
        operation_id = f"{app_name}_{tool_name}"
    
    # Extract parameters from the tool's args_schema
    parameters: List[Parameter] = []
    request_body: RequestBody | None = None
    
    if tool.args_schema:
        schema = _extract_json_schema_from_pydantic(tool.args_schema)
        properties = schema.get("properties", {})
        required_fields = schema.get("required", [])
        
        # Convert properties to OpenAPI parameters or request body
        # For simplicity, we'll use query parameters for simple types
        # and request body for complex objects
        body_properties = {}
        
        for param_name, param_schema in properties.items():
            param_type = param_schema.get("type", "string")
            param_desc = param_schema.get("description", "")
            is_required = param_name in required_fields
            
            # Simple types go as query parameters
            if param_type in ["string", "integer", "number", "boolean"]:
                # Map string type to JSONSchemaTypes enum
                json_type = JSONSchemaTypes(param_type)
                parameters.append(
                    Parameter(
                        name=param_name,
                        description=param_desc,
                        **{"in": ParameterIn.query},  # Use dict unpacking for 'in' keyword
                        required=is_required,
                        schema=JSchema(
                            type=json_type,
                            description=param_desc,
                        ),
                    )
                )
            else:
                # Complex types go in request body
                body_properties[param_name] = param_schema
        
        # Create request body if there are complex parameters
        if body_properties:
            request_body = RequestBody(
                description="Request body parameters",
                required=True,
                content={
                    "application/json": MediaType(
                        schema=JSchema(
                            type=JSONSchemaTypes.object,
                            properties=body_properties,
                            required=[k for k in body_properties.keys() if k in required_fields],
                        )
                    )
                },
            )
    
    # Create response schema by extracting return type information
    logger.debug(f"Extracting return type schema for tool: {tool_name}")
    return_schema = _extract_return_type_schema(tool)
    
    # Convert the return schema dict to JSchema
    # We need to handle the schema conversion properly
    response_jschema = _dict_schema_to_jschema(return_schema)
    
    # Create response with the extracted schema
    responses: Dict[str, Union[Response, Any]] = {
        "200": Response(
            description="Successful response",
            content={
                "application/json": MediaType(
                    schema=response_jschema
                )
            },
        )
    }
    
    # Create the operation
    # Cast parameters list to match expected type
    params_list: List[Union[Parameter, Any]] = parameters if parameters else []
    operation = Operation(
        operationId=operation_id,
        summary=tool_name,
        description=description,
        parameters=params_list if params_list else None,
        requestBody=request_body,
        responses=responses,
        tags=[app_name],
    )
    
    return operation_id, operation


async def cuga_tools_to_oas(
    tool_provider: Union[ToolProviderInterface, CombinedToolProvider],
    title: str = "CUGA Tools API",
    version: str = "1.0.0",
    description: str = "OpenAPI specification for CUGA tools",
) -> OpenAPI:
    """
    Convert all tools from a CUGA tool provider to Toolguard's OpenAPI format.
    
    This function extracts all tools from the provided tool provider and converts
    them into a complete OpenAPI specification that can be used with Toolguard
    for policy enforcement.
    
    Args:
        tool_provider: CUGA tool provider (ToolProviderInterface or CombinedToolProvider)
        title: Title for the OpenAPI specification
        version: Version of the API
        description: Description of the API
        
    Returns:
        OpenAPI object with all tools converted to operations
        
    Example:
        ```python
        from cuga.backend.cuga_graph.nodes.cuga_lite.combined_tool_provider import CombinedToolProvider
        from cuga.backend.cuga_graph.policy.tool_guard import cuga_tools_to_oas
        
        # Create tool provider
        provider = CombinedToolProvider()
        await provider.initialize()
        
        # Convert to OpenAPI
        oas = await cuga_tools_to_oas(provider)
        
        # Save to file
        oas.save("tools.yaml")
        
        # Or convert to ToolInfo for Toolguard
        from toolguard.buildtime.gen_spec.oas_to_toolinfo import openapi_to_toolinfos
        tool_infos = openapi_to_toolinfos(oas)
        ```
    """
    # Initialize the tool provider if needed
    if not getattr(tool_provider, "initialized", False):
        await tool_provider.initialize()
    
    # Get all apps and their tools
    apps = await tool_provider.get_apps()
    
    # Create OpenAPI structure
    paths: Dict[str, Union[PathItem, Any]] = {}
    all_tags = []
    
    # Process each app
    for app in apps:
        app_name = app.name
        all_tags.append({"name": app_name, "description": app.description or f"Tools from {app_name}"})
        
        # Get tools for this app
        tools = await tool_provider.get_tools(app_name)
        
        logger.info(f"Converting {len(tools)} tools from app '{app_name}' to OpenAPI")
        
        # Convert each tool to an operation
        for tool in tools:
            try:
                operation_id, operation = _convert_tool_to_operation(tool, app_name)
                
                # Create a path for this tool
                # Use the tool name as the path
                path = f"/{app_name}/{tool.name}"
                
                # Create or update PathItem
                if path not in paths:
                    paths[path] = PathItem()
                
                # Add operation as POST (most tools are actions)
                paths[path].post = operation
                
                logger.debug(f"Converted tool '{tool.name}' to operation '{operation_id}'")
                
            except Exception as e:
                logger.error(f"Failed to convert tool '{tool.name}' from app '{app_name}': {e}")
                continue
    
    # Create the OpenAPI object
    openapi = OpenAPI(
        openapi="3.1.0",
        info=Info(
            title=title,
            version=version,
            description=description,
        ),
        servers=[Server(url="/")],
        paths=paths,
        tags=all_tags,
    )
    
    logger.info(f"Successfully converted {len(paths)} tool paths to OpenAPI specification")
    
    return openapi

# Made with Bob
