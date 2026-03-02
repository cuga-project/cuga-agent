import json
import re
from typing import Any, Callable, Dict, List, Literal, Optional, TypeAlias
from urllib.parse import urlencode

import requests
from loguru import logger
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel

from cuga.backend.tools_env.registry.config.config_loader import ServiceConfig
from cuga.backend.tools_env.registry.mcp_manager.openapi_parser import (
    SimpleOpenAPIParser,
)
from cuga.config import settings

TYPE_MAP = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "object": dict,
    "array": list,
}


def sanitize_tool_name(name: str) -> str:
    """Sanitize tool names to be valid Python identifiers."""
    s = name.lower()
    s = re.sub(r'[ /.\-{}:?&=%]', '_', s)
    s = re.sub(r'__+', '_', s)
    return s.strip('_') or "unnamed_tool"


def _extract_path_segment(path_str: str, segment_index: int = 1) -> Optional[str]:
    """Extract a specific segment from a path string.

    Args:
        path_str: The path string to extract from (e.g., "/api/v1/users/123")
        segment_index: Which segment to extract (1 = first, 2 = second, 3 = third)

    Returns:
        The requested segment if it exists, None otherwise.

    Examples:
        _extract_path_segment("/api/v1/users", 1) -> "api"
        _extract_path_segment("/api/v1/users", 2) -> "v1"
        _extract_path_segment("/api/v1/users", 3) -> "users"
    """
    path_stripped = (path_str or "").strip()
    if path_stripped and path_stripped.strip('/'):
        segments = [seg for seg in path_stripped.strip('/').split('/') if seg]
        if 1 <= segment_index <= len(segments):
            return segments[segment_index - 1]
    return None


def determine_operation_name_strategy(operations: List[Any]) -> Callable[[str, str], str]:
    """
    Determine the naming strategy for operations by checking if path segments are unique.

    Args:
        operations: List of operations. Each operation should have:
            - For APIEndpoint objects: .path and .operation_id attributes
            - For dict objects: 'path' key and 'operationId' key, or nested 'op_obj' dict with 'operationId'

    Returns:
        A function that takes (path: str, operation_id: str) and returns the name to use.
    """
    # Get configured segment index from settings (defaults to 1)
    segment_index = getattr(settings.advanced_features, 'path_segment_index', 1)
    # Ensure segment_index is between 1 and 3
    segment_index = max(1, min(3, segment_index))

    path_segments = []
    operation_ids = []

    for op in operations:
        if hasattr(op, 'path'):
            path = op.path
            operation_id = op.operation_id
        elif isinstance(op, dict):
            path = op.get('path', '')
            operation_id = op.get('operationId', '')
            if not operation_id and 'op_obj' in op and isinstance(op['op_obj'], dict):
                operation_id = op['op_obj'].get('operationId', '')
        else:
            continue

        path_seg = _extract_path_segment(path, segment_index=segment_index)
        if path_seg:
            path_segments.append(path_seg)
        if operation_id:
            operation_ids.append(operation_id)

    use_path_segment = len(path_segments) == len(set(path_segments)) and len(path_segments) > 0

    def get_operation_name(path: str, operation_id: str) -> str:
        """Get the operation name based on the determined strategy."""
        if use_path_segment:
            path_seg = _extract_path_segment(path, segment_index=segment_index)
            if path_seg:
                return path_seg
        return operation_id or "unnamed"

    return get_operation_name


# A field spec is either a (type, default) tuple, or another nested dict of field specs
FieldSpec: TypeAlias = "tuple[type, Any] | dict[str, 'FieldSpec']"


def _titleize(s: str) -> str:
    # simple name helper: "countyCode" -> "CountyCode"
    parts, buf = [], []
    for ch in s:
        if ch.isupper() and buf:
            parts.append(''.join(buf))
            buf = [ch]
        else:
            buf.append(ch)
    if buf:
        parts.append(''.join(buf))
    return ''.join(p.capitalize() for p in parts)


def build_model(model_name: str, field_defs: dict[str, FieldSpec]) -> type:
    annotations: dict[str, Any] = {}
    attrs: dict[str, Any] = {}

    for field_name, spec in field_defs.items():
        # Nested model case
        if isinstance(spec, dict):
            sub_name = f"{model_name}{_titleize(field_name)}"
            sub_model = build_model(sub_name, spec)
            annotations[field_name] = sub_model
            # default for nested: None (you can change to sub_model() if you want an instance by default)
            attrs[field_name] = None
        # Leaf (type, default) case
        elif (
            isinstance(spec, tuple)
            and len(spec) == 2
            and (isinstance(spec[0], type) or hasattr(spec[0], '__origin__'))
        ):
            field_type, default = spec
            annotations[field_name] = field_type
            attrs[field_name] = default
        else:
            raise TypeError(
                f"Invalid spec for field '{field_name}': expected (type, default) or nested dict got type {spec}"
            )

    attrs["__annotations__"] = annotations
    return type(model_name, (BaseModel,), attrs)


TYPE_MAP: Dict[str, Any] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "object": dict,
    "array": list,
    "null": type(None),
    "": Any,
}


def _resolve_ref(schema, resolve):
    # resolve is parser._resolve_ref
    if not schema:
        return None
    if "$ref" in schema:
        return _resolve_ref(resolve(schema["$ref"]), resolve)
    return schema


def _normalize_union(schema, resolve):
    """Handle anyOf/oneOf with optional null; return (core_schema, nullable)."""
    schema = _resolve_ref(schema, resolve)
    if not schema:
        return None, False

    nullable = bool(schema.get("nullable", False))

    # OpenAPI 3.1: type: ["string","null"]
    t = schema.get("type")
    if isinstance(t, list):
        if "null" in t:
            nullable = True
            t = next((x for x in t if x != "null"), "")
        schema = {**schema, "type": t}

    for key in ("anyOf", "oneOf"):
        if key in schema:
            variants = [v for v in schema[key] if not (isinstance(v, dict) and v.get("type") == "null")]
            nullable = nullable or any(isinstance(v, dict) and v.get("type") == "null" for v in schema[key])
            if len(variants) == 1:
                return _normalize_union(variants[0], resolve)  # recurse down to core
            # Multiple real variants -> leave as-is but mark nullable
            core = {k: v for k, v in schema.items() if k not in ("anyOf", "oneOf")}
            core["anyOf"] = variants
            return core, nullable

    if "allOf" in schema:
        merged: Dict[str, Any] = {"type": "", "properties": {}, "required": []}
        for part in schema["allOf"]:
            p, p_null = _normalize_union(part, resolve)
            nullable = nullable or p_null
            if not p:
                continue
            if p.get("type") and not merged["type"]:
                merged["type"] = p["type"]
            # merge props
            for k, v in (p.get("properties") or {}).items():
                merged["properties"][k] = v
            for r in p.get("required", []):
                if r not in merged["required"]:
                    merged["required"].append(r)
            # carry items/enum if first time
            if "items" in p and "items" not in merged:
                merged["items"] = p["items"]
            if "enum" in p and "enum" not in merged:
                merged["enum"] = p["enum"]
        if not merged["type"] and merged["properties"]:
            merged["type"] = "object"
        return merged, nullable

    return schema, nullable


def _python_type_for_schema(schema: Dict[str, Any]) -> Any:
    t = schema.get("type", "")
    if not t and "properties" in schema:
        t = "object"
    if not t and "items" in schema:
        t = "array"
    base = TYPE_MAP.get(t, Any)
    if t == "array":
        return list  # type: ignore[index]
    if "enum" in schema and isinstance(schema["enum"], list) and schema["enum"]:
        # Optional: turn enum into Literal
        try:
            return Literal[tuple(schema["enum"])]  # type: ignore[misc]
        except Exception:
            return base
    return base


def _walk_schema_fields(
    schema: Dict[str, Any],
    resolve_ref,
    prefix: str,
    required: List[str],
    out: Dict[str, Any],  # NOTE: allow nested dicts in `out`
    *,
    flatten: bool = False,
) -> None:
    core, nullable = _normalize_union(schema, resolve_ref)
    if not core:
        return

    def _is_required(name: str, req: List[str]) -> bool:
        last = (name or "").split(".")[-1] if name else ""
        return bool(last and last in (req or []))

    t = core.get("type", "")

    # If anyOf with multiple real variants remains, treat as broad 'Any'
    if "anyOf" in core and len(core["anyOf"]) > 1:
        py_t = Any
        default = ... if _is_required(prefix, required) else None
        if prefix:
            out[prefix] = (py_t, default)
        else:
            # top-level
            out.update({"body": (py_t, default)})
        return

    if t == "array":
        # (Behavior unchanged; arrays remain a leaf with List[item_type].)
        item_schema = core.get("items") or {}
        item_core, item_nullable = _normalize_union(item_schema, resolve_ref)
        list_py = list  # type: ignore[index]
        py_t = list_py
        default = ... if _is_required(prefix, required) else None

        key = prefix or "body"
        out[key] = (py_t, default)

        # Only flatten array item properties if explicitly requested
        if flatten and (item_core or {}).get("type") == "object" and (item_core or {}).get("properties"):
            for k, v in (item_core or {}).get("properties", {}).items():
                _walk_schema_fields(
                    v,
                    resolve_ref,
                    f"{key}[].{k}",
                    (item_core or {}).get("required", []) or [],
                    out,
                    flatten=flatten,
                )
        return

    if t == "object" or core.get("properties") or "additionalProperties" in core:
        props = core.get("properties") or {}
        addl = core.get("additionalProperties", False)

        if props:
            # NEW: recurse into object and build a nested dict instead of flattening
            child_req = core.get("required", []) or []
            node: Dict[str, Any] = {}
            for k, v in props.items():
                _walk_schema_fields(v, resolve_ref, k, child_req, node, flatten=flatten)

            if prefix:
                out[prefix] = node
            else:
                # top-level object: merge its fields at the root
                out.update(node)
            return

        elif addl:
            # map-like object -> Dict[str, value_type]
            value_schema = {} if addl is True else addl
            value_py = _python_type_for_schema(_resolve_ref(value_schema, resolve_ref) or {})
            dict_py = Dict[str, value_py]  # type: ignore[index]
            py_t = dict_py
            default = ... if _is_required(prefix, required) else None
            (out if prefix else out.setdefault("body", {}))
            out[prefix or "body"] = (py_t, default)
            return

        else:
            # empty object -> dict leaf
            py_t = dict
            default = ... if _is_required(prefix, required) else None
            out[prefix or "body"] = (py_t, default)
            return

    # primitive leaf
    py_t = _python_type_for_schema(core)
    default = ... if _is_required(prefix, required) else None
    if prefix:
        out[prefix] = (py_t, default)
    else:
        out["body"] = (py_t, default)


def extract_field_definitions(api) -> Dict[str, tuple[type, Any]]:
    """
    Collect field definitions from parameters and request bodies.
    Flattens nested objects (dot notation) and handles arrays / unions.
    """
    field_defs: Dict[str, tuple[type, Any]] = {}

    # 1) Parameters
    if api.parameters:
        for param in api.parameters:
            name = param.name or ""
            if not name:
                continue
            sch = None
            if param.schema_field:
                # param.schema_field here is a Schema model; convert to dict-ish view
                sch = {
                    "type": param.schema_field.type,
                    "format": param.schema_field.format,
                    "description": param.schema_field.description,
                    "enum": param.schema_field.enum or None,
                    "nullable": param.schema_field.nullable,
                }
            py_type = str
            if sch and (param.schema_field.type and param.schema_field.type in TYPE_MAP):
                py_type = TYPE_MAP[param.schema_field.type]
            default = ... if param.required else None
            field_defs[name] = (py_type, default)

    # 2) Request body
    if api.request_body:
        for media in api.request_body.content.values():
            schema = media.schema_field
            if not schema:
                continue

            # Convert your Schema model -> raw dict so the helpers can process composition
            def to_raw(s) -> Dict[str, Any]:
                if s is None:
                    return {}
                raw: Dict[str, Any] = {}
                if s.type:
                    raw["type"] = s.type
                if s.format:
                    raw["format"] = s.format
                if s.description:
                    raw["description"] = s.description
                if s.enum:
                    raw["enum"] = list(s.enum)
                if s.nullable:
                    raw["nullable"] = True
                if s.required:
                    raw["required"] = list(s.required)
                if s.items:
                    raw["items"] = to_raw(s.items)
                if s.properties:
                    raw["properties"] = {k: to_raw(v) for k, v in s.properties.items()}
                return raw

            # We need parser._resolve_ref; store a closure that knows how to look up by ref
            # If you call this outside the parser, pass a resolver in.
            def _no_ref(_ref):  # you already resolved $ref inside your parser.Schema; keep as no-op
                raise ValueError(f"Unexpected $ref {_ref}")

            out: Dict[str, tuple[type, Any]] = {}
            _walk_schema_fields(to_raw(schema), _no_ref, prefix="", required=schema.required or [], out=out)
            # Check for name collisions between parameters and request body fields
            # Parameters (query/path) take precedence over request body fields to avoid conflicts
            # Request body fields that don't collide are still added
            for key, value in out.items():
                if key not in field_defs:
                    field_defs[key] = value
                else:
                    # Name collision: parameter takes precedence
                    # Log warning to help debug parameter structure issues
                    logger.warning(
                        f"Name collision in API '{api.operation_id}': field '{key}' exists as both "
                        f"parameter and request body field. Parameter definition (likely query/path) "
                        f"will be used. If '{key}' should be a nested object from request body, "
                        f"consider renaming the parameter or restructuring the API."
                    )

    return field_defs


def extract_url_params(api, all_params: dict):
    """
    Extract parameters to be used in the URL (i.e. path and query parameters).
    """
    path_params = {}
    query_params = {}
    if api.parameters:
        for param in api.parameters:
            if param.name in all_params:
                if param.in_ == "query":
                    if all_params[param.name] is not None:
                        query_params[param.name] = all_params[param.name]
                elif param.in_ == "path":
                    path_params[param.name] = all_params[param.name]
    return path_params, query_params


def extract_body_params(api, all_params: dict):
    """
    Extract parameters to be included in the body payload.
    """
    body_params = {}
    # Include parameters not classified as query or path
    if api.parameters:
        for param in api.parameters:
            if param.name in all_params and param.in_ not in {"query", "path"}:
                body_params[param.name] = all_params[param.name]
    # Include request body fields
    if api.request_body:
        for media in api.request_body.content.values():
            if media.schema_field and media.schema_field.properties:
                for prop in media.schema_field.properties.keys():
                    if prop in all_params:
                        body_params[prop] = all_params[prop]
    return body_params


def construct_final_url(base_url: str, api, path_params: dict, query_params: dict) -> str:
    """
    Build the final URL by substituting path parameters and appending query parameters.
    Handles list values for repeated query parameters (e.g., specialty_category_codes=75&specialty_category_codes=231).
    """
    final_path = api.path
    for k, v in path_params.items():
        final_path = final_path.replace(f"{{{k}}}", str(v))
    final_url = base_url + final_path
    if query_params:
        # Handle list values for repeated query parameters
        # urlencode doesn't handle lists correctly, so we need to flatten them
        flattened_params = []
        for key, value in query_params.items():
            if isinstance(value, list):
                # For lists, create multiple key-value pairs
                for item in value:
                    flattened_params.append((key, item))
            elif value is not None:
                # For non-list values, add as single key-value pair
                flattened_params.append((key, value))
        if flattened_params:
            final_url += "?" + urlencode(flattened_params)
    return final_url


def determine_content_type(api) -> bool:
    """
    Determine if the API expects JSON content.
    """
    if api.request_body:
        for media_type in api.request_body.content.keys():
            if media_type == "application/json":
                return True
    return False


def get_operation_override_parameters(
    schema_urls: Dict[str, ServiceConfig],
    app_name: str,
    operation_id: str,
) -> Optional[Dict]:
    """
    Get all parameters that would be dropped for a given operation_id from api_overrides.

    Args:
        operation_id: The operationId to search for
        api_overrides: List of ApiOverride configurations

    Returns:
        None if no overrides found for the operation, otherwise a list of parameter dictionaries:
        [{"param_name": "debug", "in": "query"}, {"param_name": "internal_id", "in": "body"}]
    """

    if not schema_urls[app_name].api_overrides:
        return None

    # Find the override for this operation_id
    target_override = None
    for override in schema_urls[app_name].api_overrides:
        if override.operation_id == operation_id:
            target_override = override
            break

    if not target_override:
        return None

    parameters = {}

    # Add query parameters to be dropped
    if target_override.drop_query_parameters:
        for param_name in target_override.drop_query_parameters:
            parameters[param_name] = {"in": "query"}

    # Add request body parameters to be dropped
    if target_override.drop_request_body_parameters:
        for param_name in target_override.drop_request_body_parameters:
            parameters[param_name] = {"in": "body"}

    return parameters if parameters else None


def apply_authentication(auth, headers: dict, query_params: dict):
    """
    Apply authentication configuration to headers or query parameters.

    Args:
        auth: Auth configuration object
        headers: Dictionary of headers to modify
        query_params: Dictionary of query parameters to modify
    """
    if not auth or not auth.value:
        return

    import base64

    from cuga.backend.secrets import resolve_secret

    value = resolve_secret(auth.value)
    if not value:
        return

    auth_type = auth.type.lower()

    if auth_type == 'bearer':
        headers['Authorization'] = f"Bearer {value}"

    elif auth_type == 'basic':
        if ':' in value:
            encoded = base64.b64encode(value.encode()).decode()
            headers['Authorization'] = f"Basic {encoded}"
        else:
            logger.warning("Basic auth requires 'username:password' format in value")

    elif auth_type == 'header':
        if auth.key:
            headers[auth.key] = value
        else:
            logger.warning("Header auth requires 'key' field to specify header name")

    elif auth_type == 'api-key' or auth_type == 'query':
        key = auth.key or 'api_key'
        query_params[key] = value

    else:
        logger.warning(f"Unknown auth type: {auth_type}")


def create_handler(api, model, base_url: str, name: str, schemas: Dict[str, ServiceConfig]):
    """
    Create a handler function for an API that processes parameters,
    builds the URL, and handles the request.
    """

    def handler(params: model, headers: dict = None):
        all_params = params.model_dump()
        headers = headers if headers else {}

        try:
            params = get_operation_override_parameters(
                schema_urls=schemas, app_name=name, operation_id=api.operation_id
            )
            additional_query_params = {}
            additional_body_params = {}
            tokens = headers.get("_tokens", None)
            if params and tokens is not None:
                tokens = json.loads(tokens)
                file_system_token = tokens.get("file_system", None)
                if "file_system_access_token" in params.keys() and file_system_token:
                    if params["file_system_access_token"]["in"] == "query":
                        additional_query_params["file_system_access_token"] = file_system_token
                    if params["file_system_access_token"]["in"] == "body":
                        additional_body_params["file_system_access_token"] = file_system_token
            if "_tokens" in list(headers.keys()):
                del headers['_tokens']
            path_params, query_params = extract_url_params(api, all_params)
            query_params.update(additional_query_params)

            # Apply authentication from service config
            service_config = schemas.get(name)
            if service_config and service_config.auth:
                apply_authentication(service_config.auth, headers, query_params)

            final_url = construct_final_url(base_url, api, path_params, query_params)
            body_params = extract_body_params(api, all_params)
            body_params.update(additional_body_params)
            use_json = determine_content_type(api)

            if use_json:
                response = requests.request(api.method, final_url, headers=headers, json=body_params)
            else:
                response = requests.request(api.method, final_url, headers=headers, data=body_params)

            response.raise_for_status()
            return response.text

        except Exception as e:
            logger.debug(f"Error in adapter {e}")
            error_response = {
                "status": "exception",
                "error_type": type(e).__name__,
                "message": str(e),
            }

            # Add HTTP-specific details if it's an HTTP error
            if hasattr(e, 'response') and e.response is not None:
                error_response["status_code"] = e.response.status_code
                error_response["url"] = final_url if 'final_url' in locals() else None
                error_response["method"] = api.method

                # Try to get response body for more details
                try:
                    if e.response.headers.get('content-type', '').startswith('application/json'):
                        error_response["message"] += f" {json.dumps(response.json())}"
                    else:
                        error_response["message"] += f" {e.response.text}"
                except Exception:
                    pass

            return error_response

    return handler


def new_mcp_from_custom_parser(
    base_url: str, parser: SimpleOpenAPIParser, name: str, schema_urls: Dict[str, ServiceConfig]
) -> FastMCP:
    """
    Assemble a FastMCP instance from a custom parser by dynamically creating
    tools (handlers) based on API definitions.
    """
    prefix = sanitize_tool_name(name)
    mcp = FastMCP(prefix)
    server_url = parser.get_server()
    base_url += server_url if "http" not in server_url else ""

    all_apis = [
        api
        for api in parser.apis()
        if 'No-API-Docs' not in api.description
        and 'Private-API' not in api.description
        and 'constant' not in api.path
    ]

    get_operation_name = determine_operation_name_strategy(all_apis)

    for api in all_apis:
        path_first_segment = get_operation_name(api.path, api.operation_id)
        path_prefix = sanitize_tool_name(path_first_segment)
        # Don't include prefix here - mcp_manager.py will add it when registering tools
        tool_name = f"{prefix}_{sanitize_tool_name(path_prefix)}"
        description = f"{api.operation_id} {api.summary} {api.description}"

        # Dynamically collect field definitions and build the InputModel
        field_defs = extract_field_definitions(api)
        InputModel = build_model(f"{tool_name}Input", field_defs)

        # Create and register the handler for this API endpoint
        handler = create_handler(api, InputModel, base_url, name, schema_urls)
        handler.__name__ = f"{tool_name}_handler"
        mcp.tool(name=tool_name, description=description)(handler)

    return mcp
