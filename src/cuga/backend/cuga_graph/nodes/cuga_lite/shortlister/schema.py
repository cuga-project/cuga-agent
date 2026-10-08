"""Model-facing tool schemas shared by prompt and discovery renderers."""

from typing import Any

from langchain_core.tools import BaseTool


def model_tool_schema(tool: BaseTool) -> dict[str, Any]:
    """Exclude injected context while preserving ordinary argument constraints."""
    # Keep compatibility with schema-only tool objects used by integrations.
    is_langchain_tool = isinstance(tool, BaseTool)
    args_schema = tool.args_schema
    if isinstance(args_schema, dict):
        return args_schema
    if not is_langchain_tool and hasattr(args_schema, "schema"):
        return args_schema.schema()
    schema = (
        args_schema.model_json_schema() if hasattr(args_schema, "model_json_schema") else args_schema.schema()
    )
    if not is_langchain_tool:
        return schema

    # LangChain's subset model identifies injected fields, but rebuilding its
    # JSON schema loses aliases, json_schema_extra and model-level constraints.
    # Filter the original schema instead, leaving all other metadata intact.
    fields = getattr(args_schema, "model_fields", None)
    visible_fields = getattr(tool.tool_call_schema, "model_fields", None)
    if fields is None or visible_fields is None:
        return schema
    excluded = set()
    for name in fields.keys() - visible_fields.keys():
        field = fields[name]
        alias = getattr(field, "validation_alias", None)
        if hasattr(alias, "choices"):
            alias = next((choice for choice in alias.choices if isinstance(choice, str)), None)
        if not isinstance(alias, str):
            alias = field.alias or name
        excluded.add(alias)
    if not excluded:
        return schema
    schema = {
        **schema,
        "properties": {k: v for k, v in schema.get("properties", {}).items() if k not in excluded},
    }
    if "required" in schema:
        schema["required"] = [name for name in schema["required"] if name not in excluded]
        if not schema["required"]:
            del schema["required"]
    return schema
