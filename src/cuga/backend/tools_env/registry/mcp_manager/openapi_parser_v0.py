import json
from cuga.backend.tools_env.registry.mcp_manager.adapter import (
    determine_operation_name_strategy,
    sanitize_tool_name,
)


class OpenAPITransformer:
    """
    Transforms an OpenAPI schema into a more human-readable JSON structure.
    """

    def __init__(self, openapi_schema, filter_patterns=None):
        if isinstance(openapi_schema, str):
            try:
                self.openapi_schema = json.loads(openapi_schema)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON string provided for openapi_schema: {e}")
        elif isinstance(openapi_schema, dict):
            self.openapi_schema = openapi_schema
        else:
            raise ValueError("openapi_schema must be a JSON string or a dictionary")

        if not isinstance(self.openapi_schema.get('paths'), dict):
            print(
                "Warning: 'paths' object not found or not a dictionary in the OpenAPI schema. Output might be empty."
            )

        self.app_name = self._get_app_name()
        self.filter_patterns = (
            filter_patterns if filter_patterns is not None else ["No-API-Docs", "Private-API"]
        )

    def _summarize_param_schema(self, schema_obj):
        """
        Produce a compact, human-readable structure for parameters:
        - primitives -> "string"/"integer"/...
        - arrays     -> [{"...item shape..."}] or ["string"]
        - objects    -> { field: shape, ... }
        """
        resolved = self._resolve_ref(schema_obj)
        if not isinstance(resolved, dict):
            return "unknown"

        # unwrap unions
        variant = self._select_variant(resolved)
        if isinstance(variant, dict):
            resolved = variant

        # example beats everything
        if 'example' in resolved:
            return resolved['example']

        t, _ = self._get_schema_type_and_enum(resolved)

        if t == 'array' or resolved.get('type') == 'array':
            item_ref = resolved.get('items', {})
            item_resolved = self._resolve_ref(item_ref) if isinstance(item_ref, dict) else item_ref
            if isinstance(item_resolved, dict):
                item_variant = self._select_variant(item_resolved)
                if isinstance(item_variant, dict):
                    item_resolved = item_variant
            # Return proper array structure with items property
            return {"type": "array", "items": self._summarize_param_schema(item_resolved)}

        # object with properties
        if t == 'object' or 'properties' in resolved:
            if 'properties' in resolved:
                return {
                    name: self._summarize_param_schema(prop_ref)
                    for name, prop_ref in resolved['properties'].items()
                }
            # additionalProperties support (common in your schema)
            ap = resolved.get("additionalProperties", None)
            if ap is True:
                return {"<key>": "any"}
            elif isinstance(ap, dict):
                return {"<key>": self._summarize_param_schema(ap)}
            return "object"

        # enums and primitives just return the base type (or enum values if you prefer)
        return t

    def _select_variant(self, schema_obj):
        """
        For anyOf/oneOf/allOf wrappers, resolve refs and pick a representative non-null variant.
        Preference order:
          1) object with properties
          2) first non-null candidate
        Returns the chosen variant dict or None if not applicable.
        """
        if not isinstance(schema_obj, dict):
            return None

        for key in ['anyOf', 'oneOf', 'allOf']:
            if key in schema_obj and isinstance(schema_obj[key], list):
                # Resolve all candidates
                candidates = []
                for s in schema_obj[key]:
                    resolved = self._resolve_ref(s) if isinstance(s, dict) else s
                    if isinstance(resolved, dict):
                        candidates.append(resolved)

                # Filter out explicit null types
                non_null = [c for c in candidates if c.get('type') != 'null']

                # Prefer object with properties
                for c in non_null:
                    if c.get('type') == 'object' or 'properties' in c:
                        return c

                # Otherwise, first non-null
                if non_null:
                    return non_null[0]
                return None
        return None

    def _resolve_ref(self, ref_obj):
        current_obj = ref_obj
        visited_refs = set()

        while isinstance(current_obj, dict) and '$ref' in current_obj:
            ref_path_str = current_obj['$ref']
            if ref_path_str in visited_refs:
                return {"type": "circular_ref", "ref": ref_path_str, "error": "Circular reference detected"}
            visited_refs.add(ref_path_str)

            if not ref_path_str.startswith('#/'):
                return {
                    "type": "unresolved_external_ref",
                    "ref": ref_path_str,
                    "error": "External references are not supported.",
                }

            ref_path_parts = ref_path_str.split('/')[1:]  # Remove '#'
            resolved_component = self.openapi_schema
            try:
                for part in ref_path_parts:
                    resolved_component = resolved_component[part]
                current_obj = resolved_component
            except (KeyError, TypeError) as e:
                return {
                    "type": "unresolved_ref",
                    "ref": ref_path_str,
                    "error": f"Path part not found during resolution: {e}",
                }
        return current_obj

    def _get_app_name(self):
        if 'x-app-name' in self.openapi_schema:
            return self.openapi_schema['x-app-name']
        info = self.openapi_schema.get('info', {})
        if 'x-app-name' in info:
            return info['x-app-name']
        title = info.get('title')
        if title:
            common_suffixes = [" API", " Service", " Application"]
            for suffix in common_suffixes:
                if title.endswith(suffix):
                    return title[: -len(suffix)].strip()
            return title.strip()
        tags = self.openapi_schema.get('tags')
        if (
            tags
            and isinstance(tags, list)
            and len(tags) > 0
            and isinstance(tags[0], dict)
            and 'name' in tags[0]
        ):
            return tags[0]['name']
        return "unknown_app"

    def _get_schema_type_and_enum(self, schema_obj):
        """
        Extracts the type and enum values from a schema, handling anyOf/oneOf/allOf patterns.
        """
        if not isinstance(schema_obj, dict):
            return "unknown", None

        # If this schema is a union wrapper, pick a representative variant and recurse/inspect
        variant = self._select_variant(schema_obj)
        if isinstance(variant, dict):
            # Prefer direct enum/type on the variant
            if 'enum' in variant and isinstance(variant['enum'], list):
                return variant.get('type', 'string'), variant['enum']
            if 'type' in variant:
                return variant['type'], variant.get('enum')
            if 'properties' in variant:
                return 'object', None  # implicit object

        # Direct enum first
        if 'enum' in schema_obj and isinstance(schema_obj['enum'], list):
            schema_type = schema_obj.get('type', 'string')
            return schema_type, schema_obj['enum']

        # Direct type + optional enum
        if 'type' in schema_obj:
            return schema_obj['type'], schema_obj.get('enum')

        # allOf fallback (rare without type)
        if 'allOf' in schema_obj and isinstance(schema_obj['allOf'], list):
            for sub_schema in schema_obj['allOf']:
                sub_schema = self._resolve_ref(sub_schema) if isinstance(sub_schema, dict) else sub_schema
                if isinstance(sub_schema, dict) and 'type' in sub_schema:
                    return sub_schema['type'], sub_schema.get('enum')

        return "object", None

    def _format_constraints(self, schema_obj_ref):
        resolved_schema = self._resolve_ref(schema_obj_ref)
        if not isinstance(resolved_schema, dict):
            return []

        # Unwrap unions to chosen variant
        variant = self._select_variant(resolved_schema)
        if isinstance(variant, dict):
            resolved_schema = variant

        constraints = []

        schema_type, enum_values = self._get_schema_type_and_enum(resolved_schema)

        # Enum constraint
        if enum_values and isinstance(enum_values, list):
            enum_values_str = ", ".join(map(str, enum_values))
            constraints.append(f"must be one of: [{enum_values_str}]")
        elif 'enum' in resolved_schema and isinstance(resolved_schema['enum'], list):
            enum_values_str = ", ".join(map(str, resolved_schema['enum']))
            constraints.append(f"must be one of: [{enum_values_str}]")

        # String constraints
        if schema_type == 'string' or resolved_schema.get('type') == 'string':
            if 'minLength' in resolved_schema:
                constraints.append(f"length >= {resolved_schema['minLength']}")
            if 'maxLength' in resolved_schema:
                constraints.append(f"length <= {resolved_schema['maxLength']}")
            if 'pattern' in resolved_schema:
                constraints.append(f"matches pattern: {resolved_schema['pattern']}")
            if 'format' in resolved_schema:
                constraints.append(f"format: {resolved_schema['format']}")

        # Number/integer constraints
        if schema_type in ['number', 'integer'] or resolved_schema.get('type') in ['number', 'integer']:
            if 'minimum' in resolved_schema:
                op = ">=" if not resolved_schema.get('exclusiveMinimum', False) else ">"
                constraints.append(f"{op} {resolved_schema['minimum']}")
            if 'maximum' in resolved_schema:
                op = "<=" if not resolved_schema.get('exclusiveMaximum', False) else "<"
                constraints.append(f"{op} {resolved_schema['maximum']}")
            if 'multipleOf' in resolved_schema:
                constraints.append(f"multiple of {resolved_schema['multipleOf']}")

        # Array constraints
        if schema_type == 'array' or resolved_schema.get('type') == 'array':
            if 'minItems' in resolved_schema:
                constraints.append(f"min items: {resolved_schema['minItems']}")
            if 'maxItems' in resolved_schema:
                constraints.append(f"max items: {resolved_schema['maxItems']}")
            if resolved_schema.get('uniqueItems', False):
                constraints.append("items must be unique")

        return constraints

    def _get_property_representation(self, prop_schema_ref):
        resolved_prop_schema = self._resolve_ref(prop_schema_ref)
        if not isinstance(resolved_prop_schema, dict):
            return "unknown_schema_format"

        # Unwrap union wrappers to preferred variant
        variant = self._select_variant(resolved_prop_schema)
        if isinstance(variant, dict):
            resolved_prop_schema = variant

        # Example takes precedence
        if 'example' in resolved_prop_schema:
            return resolved_prop_schema['example']

        # Type + enum
        prop_type, enum_values = self._get_schema_type_and_enum(resolved_prop_schema)

        if prop_type == 'array':
            items_schema_ref = resolved_prop_schema.get('items', {})
            resolved_items_schema = self._resolve_ref(items_schema_ref)
            if not isinstance(resolved_items_schema, dict):
                return ["unknown_item_schema_format"]

            # Unwrap item unions as well
            item_variant = self._select_variant(resolved_items_schema)
            if isinstance(item_variant, dict):
                resolved_items_schema = item_variant

            if resolved_items_schema.get('type') == 'object' and 'properties' in resolved_items_schema:
                item_representation = self._simplify_response_schema_properties(resolved_items_schema)
                return [item_representation]
            elif 'example' in resolved_items_schema:
                return [resolved_items_schema['example']]
            else:
                return [self._get_property_representation(resolved_items_schema)]

        if (
            prop_type == 'object'
            or (prop_type == "unknown" and 'properties' in resolved_prop_schema)
            or 'properties' in resolved_prop_schema
        ):
            if 'properties' in resolved_prop_schema:
                return self._simplify_response_schema_properties(resolved_prop_schema)
            return "object"

        if enum_values and isinstance(enum_values, list):
            return enum_values

        return prop_type if prop_type != "unknown" else "unknown_type"

    def _simplify_response_schema_properties(self, schema_obj_ref):
        resolved_schema = self._resolve_ref(schema_obj_ref)
        if not isinstance(resolved_schema, dict):
            return "error_resolving_schema"

        # Unwrap unions
        variant = self._select_variant(resolved_schema)
        if isinstance(variant, dict):
            resolved_schema = variant

        # Object with properties
        if (
            resolved_schema.get('type') == 'object' or 'properties' in resolved_schema
        ) and 'properties' in resolved_schema:
            simplified_props = {}
            for prop_name, prop_schema_val_ref in resolved_schema['properties'].items():
                simplified_props[prop_name] = self._get_property_representation(prop_schema_val_ref)
            return simplified_props
        else:
            # Primitive/array/etc.
            return self._get_property_representation(resolved_schema)

    def _extract_parameters(self, operation_obj, path_item_obj):
        """
        Extracts and formats parameters from an operation, including:
        - path-level + operation-level params (query/path/header/cookie)
        - requestBody (object fields), attaching a compact `schema` shape for objects/arrays
        """
        processed_params = []
        consolidated_params_dict = {}

        # 1) Collect/merge path-level and operation-level parameters
        for param_container in path_item_obj.get('parameters', []):
            param_obj = self._resolve_ref(param_container)
            if isinstance(param_obj, dict) and 'name' in param_obj and 'in' in param_obj:
                consolidated_params_dict[(param_obj['name'], param_obj['in'])] = param_obj

        for param_container in operation_obj.get('parameters', []):
            param_obj = self._resolve_ref(param_container)
            if isinstance(param_obj, dict) and 'name' in param_obj and 'in' in param_obj:
                consolidated_params_dict[(param_obj['name'], param_obj['in'])] = param_obj

        # 2) Process consolidated (query/path/header/cookie) params
        for param_obj_val in consolidated_params_dict.values():
            param_schema_ref = param_obj_val.get('schema', {})
            resolved_param_schema = self._resolve_ref(param_schema_ref)
            if not isinstance(resolved_param_schema, dict):
                continue

            # Unwrap anyOf/oneOf/allOf for the param schema
            variant = self._select_variant(resolved_param_schema)
            if isinstance(variant, dict):
                resolved_param_schema = variant

            param_type, _ = self._get_schema_type_and_enum(resolved_param_schema)
            param_repr = self._summarize_param_schema(resolved_param_schema)

            processed_params.append(
                {
                    "name": param_obj_val.get('name'),
                    "type": param_type,
                    "required": param_obj_val.get('required', False),
                    "description": param_obj_val.get('description', ''),
                    "default": resolved_param_schema.get('default'),
                    "constraints": self._format_constraints(resolved_param_schema),
                    # Include shape only when it adds structure (objects, arrays, or complex types)
                    "schema": param_repr
                    if isinstance(param_repr, dict)
                    and (param_repr.get("type") == "array" or len(param_repr) > 1)
                    else None,
                }
            )

        # 3) Process requestBody (JSON) object fields as parameters
        request_body_container = operation_obj.get('requestBody')
        if request_body_container:
            request_body = self._resolve_ref(request_body_container)
            if isinstance(request_body, dict):
                # Prefer application/json but fall back to the first content type
                json_content = request_body.get('content', {}).get('application/json')
                if (
                    not json_content
                    and isinstance(request_body.get('content'), dict)
                    and request_body['content']
                ):
                    first_content_key = next(iter(request_body['content']), None)
                    if first_content_key:
                        json_content = request_body['content'][first_content_key]

                if json_content and isinstance(json_content.get('schema'), dict):
                    body_schema_ref = json_content['schema']
                    resolved_body_schema = self._resolve_ref(body_schema_ref)

                    if isinstance(resolved_body_schema, dict):
                        # Unwrap union at the root of the body schema
                        variant = self._select_variant(resolved_body_schema)
                        if isinstance(variant, dict):
                            resolved_body_schema = variant

                        # Only explode object-with-properties into individual params
                        if resolved_body_schema.get('type') == 'object' and isinstance(
                            resolved_body_schema.get('properties'), dict
                        ):
                            required_body_fields = resolved_body_schema.get('required', [])

                            for prop_name, prop_schema_ref_val in resolved_body_schema['properties'].items():
                                resolved_prop_schema = self._resolve_ref(prop_schema_ref_val)
                                if not isinstance(resolved_prop_schema, dict):
                                    continue

                                # Unwrap field-level unions. FastAPI/Pydantic put an
                                # optional parameter's description and title on the union
                                # wrapper, not on the variant, so carry them through the
                                # unwrap; the wrapper's own values win.
                                field_variant = self._select_variant(resolved_prop_schema)
                                if isinstance(field_variant, dict):
                                    wrapper_docs = {
                                        key: resolved_prop_schema[key]
                                        for key in ('description', 'title')
                                        if key in resolved_prop_schema
                                    }
                                    resolved_prop_schema = {**field_variant, **wrapper_docs}

                                prop_type, _ = self._get_schema_type_and_enum(resolved_prop_schema)
                                prop_repr = self._summarize_param_schema(resolved_prop_schema)

                                processed_params.append(
                                    {
                                        "name": prop_name,
                                        "type": prop_type,
                                        "required": prop_name in required_body_fields,
                                        "description": resolved_prop_schema.get('description', ''),
                                        "default": resolved_prop_schema.get('default'),
                                        "constraints": self._format_constraints(resolved_prop_schema),
                                        # Include nested shape for object/array fields
                                        "schema": prop_repr
                                        if isinstance(prop_repr, dict)
                                        and (prop_repr.get("type") == "array" or len(prop_repr) > 1)
                                        else None,
                                    }
                                )

        return processed_params

    def _extract_response_schemas(self, responses_obj):
        output_responses = {}
        success_schema_data = None
        failure_schema_data = None

        if not isinstance(responses_obj, dict):
            return output_responses

        for code, resp_obj_ref in responses_obj.items():
            resp_obj = self._resolve_ref(resp_obj_ref)
            if not isinstance(resp_obj, dict):
                continue
            content = resp_obj.get('content', {})
            schema_to_simplify = None
            if isinstance(content.get('application/json'), dict) and 'schema' in content['application/json']:
                schema_to_simplify = content['application/json']['schema']
            elif content:
                for media_type_obj in content.values():
                    if isinstance(media_type_obj, dict) and 'schema' in media_type_obj:
                        schema_to_simplify = media_type_obj['schema']
                        break
            if not schema_to_simplify:
                continue

            simplified_data = self._simplify_response_schema_properties(schema_to_simplify)
            if not isinstance(simplified_data, (dict, list)):
                simplified_data = {"value": simplified_data} if simplified_data is not None else {}

            str_code = str(code)
            is_success = str_code.startswith('2')
            is_failure = str_code.startswith('4') or str_code.startswith('5') or str_code.lower() == 'default'

            if is_success and not success_schema_data:
                success_schema_data = simplified_data
            elif is_failure and not failure_schema_data:
                failure_schema_data = simplified_data

        if success_schema_data:
            output_responses['success'] = success_schema_data
        if failure_schema_data:
            output_responses['failure'] = failure_schema_data
        return output_responses

    def _extract_operation_details(self, path_str, method_str, op_obj, path_item_obj, get_operation_name):
        operation_id = op_obj.get('operationId', 'unnamed')
        api_name = get_operation_name(path_str, operation_id)
        sanitized_api_name = sanitize_tool_name(f"{self.app_name}_{api_name}")

        description = op_obj.get('description', op_obj.get('summary', ''))
        parameters = self._extract_parameters(op_obj, path_item_obj)
        response_schemas = self._extract_response_schemas(op_obj.get('responses', {}))
        canary_string = op_obj.get('x-canary-string', op_obj.get('x-custom-canary'))

        operation_details = {
            "app_name": self.app_name,
            "secure": "security" in op_obj,
            "api_name": sanitized_api_name,
            "operation_id": operation_id,  # Original OpenAPI operationId for tracking
            "path": path_str,
            "method": method_str.upper(),
            "description": description,
            "parameters": parameters,
            "response_schemas": response_schemas,
            "canary_string": canary_string,
        }
        return sanitized_api_name, operation_details

    def _should_filter_api(self, description):
        if not description or not self.filter_patterns:
            return False
        description_lower = description.lower()
        return any(pattern.lower() in description_lower for pattern in self.filter_patterns)

    def transform(self):
        output = {}
        paths = self.openapi_schema.get('paths', {})
        if not isinstance(paths, dict):
            print("Warning: 'paths' is not a dictionary in the OpenAPI schema. Cannot transform.")
            return output

        operations = []
        for path_str, path_item_obj_ref in paths.items():
            path_item_obj = self._resolve_ref(path_item_obj_ref)
            if not isinstance(path_item_obj, dict):
                continue

            valid_methods = ["get", "put", "post", "delete", "options", "head", "patch", "trace"]
            for method_str, op_obj_ref in path_item_obj.items():
                if method_str.lower() not in valid_methods:
                    continue
                op_obj = self._resolve_ref(op_obj_ref)
                if not isinstance(op_obj, dict):
                    continue

                description = op_obj.get('description', '')
                if self._should_filter_api(description):
                    continue

                operations.append(
                    {
                        'path': path_str,
                        'method': method_str,
                        'op_obj': op_obj,
                        'path_item_obj': path_item_obj,
                    }
                )

        get_operation_name = determine_operation_name_strategy(operations)

        for op_data in operations:
            api_name_key, operation_details = self._extract_operation_details(
                op_data['path'],
                op_data['method'],
                op_data['op_obj'],
                op_data['path_item_obj'],
                get_operation_name,
            )
            if api_name_key in output:
                print(
                    f"Warning: Duplicate api_name_key '{api_name_key}' detected. Overwriting previous entry. "
                    "Ensure operationIds combined with app_name result in unique keys or generation logic is robust."
                )
            output[api_name_key] = operation_details
        return output
