from typing import List, Dict, Optional, Any
from pydantic import BaseModel, Field, ConfigDict
import json
import yaml


class Server(BaseModel):
    url: Optional[str] = ""
    description: Optional[str] = ""


class APIInfo(BaseModel):
    title: Optional[str] = ""
    version: Optional[str] = ""
    description: Optional[str] = ""


class Schema(BaseModel):
    type: Optional[str] = ""
    format: Optional[str] = ""
    description: Optional[str] = ""
    default: Optional[Any] = None
    enum: List[Any] = Field(default_factory=list)
    properties: Dict[str, "Schema"] = Field(default_factory=dict)
    items: Optional["Schema"] = None
    required: List[str] = Field(default_factory=list)
    ref: Optional[str] = ""
    nullable: Optional[bool] = False
    title: Optional[str] = ""  # handy, many specs use it


Schema.model_rebuild()


class Parameter(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: Optional[str] = ""
    in_: Optional[str] = Field("", alias="in")  # allow aliasing "in"
    required: Optional[bool] = False
    description: Optional[str] = ""
    schema_field: Optional[Schema] = Field(None, alias="schema")


class MediaType(BaseModel):
    schema_field: Optional[Schema] = Field(None, alias="schema")


class RequestBody(BaseModel):
    required: Optional[bool] = False
    content: Dict[str, MediaType] = Field(default_factory=dict)


class Response(BaseModel):
    description: Optional[str] = ""
    content: Dict[str, MediaType] = Field(default_factory=dict)


class APIEndpoint(BaseModel):
    path: Optional[str] = ""
    method: Optional[str] = ""
    summary: Optional[str] = ""
    description: Optional[str] = ""
    operation_id: Optional[str] = ""
    parameters: List[Parameter] = Field(default_factory=list)
    request_body: Optional[RequestBody] = None
    responses: Dict[str, Response] = Field(default_factory=dict)


def is_http_method(method):
    return method.lower() in ["get", "post", "put", "delete", "options", "head", "patch", "trace"]


class SimpleOpenAPIParser:
    def __init__(self, document):
        self.document = document

    @staticmethod
    def from_json(data):
        try:
            doc = json.loads(data)
        except Exception as e:
            raise ValueError("Invalid JSON") from e
        return SimpleOpenAPIParser(doc)

    @staticmethod
    def from_yaml(data):
        try:
            yaml_obj = yaml.safe_load(data)
            json_data = json.dumps(yaml_obj)
            return SimpleOpenAPIParser.from_json(json_data)
        except Exception as e:
            raise ValueError("Invalid YAML") from e

    def servers(self):
        result = []
        for item in self.document.get("servers", []):
            url = item.get("url", "")
            desc = item.get("description", "")
            result.append(Server(url=url, description=desc))
        return result

    def info(self):
        info_data = self.document.get("info", {})
        return APIInfo(
            title=info_data.get("title", ""),
            version=info_data.get("version", ""),
            description=info_data.get("description", ""),
        )

    def apis(self):
        result = []
        paths = self.document.get("paths", {})
        for path, methods in paths.items():
            for method, op_data in methods.items():
                if not is_http_method(method):
                    continue
                endpoint = APIEndpoint()
                endpoint.path = path
                endpoint.method = method.upper()
                endpoint.summary = op_data.get("summary", "")
                endpoint.description = op_data.get("description", "")
                endpoint.operation_id = op_data.get("operationId", "")
                endpoint.parameters = self._parse_parameters(op_data.get("parameters", []))
                endpoint.request_body = self._parse_request_body(op_data.get("requestBody", {}))
                endpoint.responses = self._parse_responses(op_data.get("responses", {}))
                result.append(endpoint)
        return result

    def get_server(self):
        if not self.document or 'servers' not in self.document or len(self.document['servers']) < 1:
            return ''
        return self.document['servers'][0]['url']

    def _resolve_ref(self, ref: str) -> dict:
        parts = ref.lstrip("#/").split("/")
        data = self.document
        for part in parts:
            if not isinstance(data, dict):
                raise ValueError(f"Could not resolve $ref (non-dict encountered): {ref}")
            data = data.get(part)
            if data is None:
                raise ValueError(f"Could not resolve $ref: {ref}")
        return data

    def _parse_parameters(self, param_list):
        result = []
        for item in param_list or []:
            # Handle $ref-ed parameters
            if "$ref" in item:
                item = self._resolve_ref(item["$ref"])
            p = Parameter()
            p.name = item.get("name", "")
            p.in_ = item.get("in", "")
            p.required = item.get("required", False)
            p.description = item.get("description", "")
            if "schema" in item:
                p.schema_field = self._parse_schema(item["schema"])
            result.append(p)
        return result

    def _parse_request_body(self, rb_data):
        if not rb_data:
            return None
        rb = RequestBody()
        rb.required = rb_data.get("required", False)
        for media_type, media_data in (rb_data.get("content") or {}).items():
            media = MediaType()
            if "schema" in media_data:
                media.schema_field = self._parse_schema(media_data["schema"])
            rb.content[media_type] = media
        return rb

    def _parse_responses(self, resp_data):
        result = {}
        for code, response in (resp_data or {}).items():
            r = Response()
            r.description = response.get("description", "")
            for media_type, media_data in (response.get("content") or {}).items():
                media = MediaType()
                if "schema" in media_data:
                    media.schema_field = self._parse_schema(media_data["schema"])
                r.content[media_type] = media
            result[code] = r
        return result

    def _parse_schema(self, schema_data) -> Optional[Schema]:
        if not schema_data:
            return None

        # Handle $ref up front
        if "$ref" in schema_data:
            resolved = self._resolve_ref(schema_data["$ref"])
            parsed = self._parse_schema(resolved)
            # carry through local description/title, if present
            if "description" in schema_data and parsed:
                parsed.description = schema_data["description"]
            if "title" in schema_data and parsed:
                parsed.title = schema_data["title"]
            return parsed

        # Normalize OpenAPI 3.1 union types, e.g. "type": ["string","null"]
        raw_type = schema_data.get("type", "")
        nullable = schema_data.get("nullable", False)
        if isinstance(raw_type, list):
            nullable = nullable or ("null" in raw_type)
            # choose the first non-null as primary type for ease of use
            raw_type = next((t for t in raw_type if t != "null"), "") or ""

        # Composition keywords
        for key in ("anyOf", "oneOf", "allOf"):
            if key in schema_data:
                variants = schema_data[key] or []
                # mark nullable if "null" variant present
                comp_nullable = any(v.get("type") == "null" for v in variants if isinstance(v, dict))
                non_null_variants = [
                    v for v in variants if not (isinstance(v, dict) and v.get("type") == "null")
                ]

                # Simple common case: [$ref, null] or [primitive, null]
                if key in ("anyOf", "oneOf") and len(non_null_variants) == 1:
                    sub = self._parse_schema(non_null_variants[0])
                    if sub:
                        sub.nullable = bool(sub.nullable or nullable or comp_nullable)
                        # carry through local description/title, as the $ref branch does
                        if "description" in schema_data:
                            sub.description = schema_data["description"]
                        if "title" in schema_data:
                            sub.title = schema_data["title"]
                    return sub

                # Fallback: shallow merge for allOf (and complex anyOf/oneOf)
                merged = Schema()
                merged.nullable = bool(nullable or comp_nullable)
                # Merge each non-null variant (properties, required, etc.)
                for v in non_null_variants:
                    sv = self._parse_schema(v)
                    if not sv:
                        continue
                    # prefer first concrete type/format
                    if not merged.type and sv.type:
                        merged.type = sv.type
                        merged.format = sv.format
                    # accumulate properties/required/items/enum/description/title
                    if sv.properties:
                        for k, val in sv.properties.items():
                            merged.properties[k] = val
                    if sv.required:
                        for r in sv.required:
                            if r not in merged.required:
                                merged.required.append(r)
                    if sv.items and not merged.items:
                        merged.items = sv.items
                    if sv.enum and not merged.enum:
                        merged.enum = list(sv.enum)
                    if sv.description and not merged.description:
                        merged.description = sv.description
                    if sv.title and not merged.title:
                        merged.title = sv.title
                # the wrapper's own description/title outrank any variant's
                if "description" in schema_data:
                    merged.description = schema_data["description"]
                if "title" in schema_data:
                    merged.title = schema_data["title"]
                # if still no type but we have properties, default to object
                if not merged.type and merged.properties:
                    merged.type = "object"
                return merged

        # Plain schema
        schema = Schema()
        schema.type = raw_type or schema.type
        schema.format = schema_data.get("format", "")
        schema.description = schema_data.get("description", "")
        schema.title = schema_data.get("title", "")
        schema.default = schema_data.get("default")
        schema.enum = list(schema_data.get("enum", [])) or schema.enum
        schema.required = list(schema_data.get("required", [])) or schema.required
        schema.nullable = bool(nullable or schema.nullable)

        if "properties" in schema_data and isinstance(schema_data["properties"], dict):
            schema.properties = {k: self._parse_schema(v) for k, v in schema_data["properties"].items()}

        if "items" in schema_data:
            schema.items = self._parse_schema(schema_data["items"])

        return schema
