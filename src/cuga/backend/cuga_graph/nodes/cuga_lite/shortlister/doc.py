"""Turn a tool into the short text that gets embedded.

Tool text in practice is much weaker than it looks. Sampling the bundled CRM
demo: endpoints carry no docstring and no ``summary=``/``description=``, so the
OpenAPI parser falls back to FastAPI's auto-summary (``"Get Contacts"``) or
nothing. Names are machine-generated — ``determine_operation_name_strategy``
only uses a path segment when segments are unique, and ``/contacts/`` collides
with ``/contacts/{contact_id}``, so names fall back to operationIds:
``crm_get_contacts_contacts_get``.

So the **name carries most of the signal**, but only once split back into words:
``crm_get_contacts_contacts_get`` tokenizes badly, ``crm get contacts contacts
get`` matches "list all contacts" well.

Deliberately excluded from the embedded text: ``args_schema`` JSON, full
response schemas, param constraints. They are what makes the *LLM* prompt
expensive, and in a fixed-size vector they dilute signal rather than add it.
They still reach the agent via the rendered markdown — they are simply not part
of the ranking.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List, Optional

from langchain_core.tools import StructuredTool

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_NON_WORD = re.compile(r"[^0-9a-zA-Z]+")
_WS = re.compile(r"\s+")


def split_identifier(name: str) -> str:
    """Split a tool identifier into space-separated lowercase words.

    ``crm_get_contacts_contacts__get``  -> ``crm get contacts contacts get``
    ``getAccountContacts``              -> ``get account contacts``
    ``crm-get.contacts``                -> ``crm get contacts``

    Duplicate words are kept: repetition in an operationId is genuine emphasis
    on the entity ("contacts" twice) and helps rather than hurts the match.
    """
    if not name:
        return ""
    spaced = _CAMEL_BOUNDARY.sub(" ", str(name))
    spaced = _NON_WORD.sub(" ", spaced)
    return _WS.sub(" ", spaced).strip().lower()


def _response_field_names(response_doc: str, limit: int = 24) -> List[str]:
    """Pull bare field names out of a rendered response doc.

    We want ``items, total, skip`` — not the JSON Schema they came from. Parsing
    the already-rendered doc keeps this in step with whatever
    ``PromptUtils.get_tool_docs`` produces instead of re-walking schemas here.
    """
    names: List[str] = []
    for raw_line in (response_doc or "").splitlines():
        line = raw_line.strip().lstrip("-*• ").strip()
        if not line:
            continue
        match = re.match(r"^`?([A-Za-z_][A-Za-z0-9_]*)`?\s*[:(]", line)
        if match:
            candidate = match.group(1)
            if candidate not in names:
                names.append(candidate)
        if len(names) >= limit:
            break
    return names


def _param_lines(tool: StructuredTool, limit: int = 24) -> List[str]:
    """``name: description`` for each parameter, description optional.

    With tool descriptions frequently empty, parameter text is often the only
    real natural language available for a tool.
    """
    schema: Dict[str, Any] = {}
    args_schema = getattr(tool, "args_schema", None)
    if args_schema is not None:
        try:
            if hasattr(args_schema, "model_json_schema"):
                schema = args_schema.model_json_schema()
            elif hasattr(args_schema, "schema"):
                schema = args_schema.schema()
        except (AttributeError, TypeError, ValueError):
            schema = {}
    properties = (schema or {}).get("properties") or {}
    lines: List[str] = []
    for param_name, spec in list(properties.items())[:limit]:
        description = ""
        if isinstance(spec, dict):
            description = str(spec.get("description") or "").strip()
        words = split_identifier(param_name)
        lines.append(f"{words}: {description}" if description else words)
    return lines


def tool_document(
    tool: StructuredTool,
    app_name: str = "",
    response_doc: str = "",
) -> str:
    """Build the text embedded for ``tool``.

    Deterministic: the same tool always yields the same string, which is what
    makes :func:`tool_fingerprint` a valid cache key.
    """
    name = getattr(tool, "name", "") or ""
    description = (getattr(tool, "description", "") or "").strip()

    parts: List[str] = [f"{split_identifier(app_name)} {split_identifier(name)}".strip()]
    if description:
        parts.append(description)

    params = _param_lines(tool)
    if params:
        parts.append("Parameters: " + "; ".join(params))

    returns = _response_field_names(response_doc)
    if returns:
        parts.append("Returns: " + ", ".join(returns))

    return "\n".join(p for p in parts if p)


def tool_fingerprint(document: str, model_name: str) -> str:
    """Cache key for an embedded document.

    Keyed on **content**, not tool name, so an edited description or a changed
    embedding model re-embeds automatically instead of serving a stale vector.
    """
    digest = hashlib.sha256()
    digest.update((model_name or "").encode("utf-8"))
    digest.update(b"\x00")
    digest.update((document or "").encode("utf-8"))
    return digest.hexdigest()


def app_name_for_tool(tool: StructuredTool, fallback: Optional[str] = None) -> str:
    """Best-effort app name; the registry provider stamps ``_app_name``."""
    for target in (tool, getattr(tool, "func", None), getattr(tool, "coroutine", None)):
        if target is None:
            continue
        value = getattr(target, "_app_name", None)
        if value:
            return str(value)
    return fallback or ""
