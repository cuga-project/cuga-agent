#!/usr/bin/env python3
"""Table Designer: a deterministic MCP Apps demo server.

Shows how a tool can ask the user to fill in a form through the CUGA chat UI,
using the MCP Apps standard (https://mcpui.dev, SEP-1865):

- ``propose_table`` (visible to the model) proposes columns for a new table and
  declares ``_meta.ui.resourceUri = ui://table-designer/form``. A host that
  supports MCP Apps renders that resource next to the tool result.
- The form calls ``submit_table_design``, ``edit_table_design`` and
  ``confirm_table_design`` (``visibility: ["app"]``: the form may call them,
  the model never sees them) to move between the edit and review forms.
- When the user accepts, ``confirm_table_design`` returns the approved design
  and the form hands it back to the host, which gives it to the agent as the
  result of ``propose_table``.
- ``create_table`` (visible to the model) "creates" the table and returns the
  DDL. Nothing is written anywhere.

Every tool is deterministic: the same inputs always give the same output.

Run (streamable HTTP, default http://127.0.0.1:8210/mcp)::

    uv run python -m cuga.demo_tools.table_designer_mcp.server
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.apps.config import AppConfig
from fastmcp.tools import ToolResult
from mcp.types import TextContent
from pydantic import BaseModel, Field

from cuga.demo_tools.table_designer_mcp.schema_logic import (
    COLUMN_TYPES,
    IDENTIFIER_PATTERN,
    Column,
    columns_as_dicts,
    diff_columns,
    propose_columns,
    render_ddl,
    session_id_for,
    to_identifier,
    validate,
)

FORM_URI = "ui://table-designer/form"
_HERE = Path(__file__).parent
_STEPS = ["Edit columns", "Review", "Done"]

mcp = FastMCP(
    "table-designer",
    instructions=(
        "Design database tables with the user. Call propose_table first: it shows the user an "
        "interactive form and returns the columns the user approved. Then call create_table "
        "with exactly those columns."
    ),
)


class ColumnInput(BaseModel):
    name: str = Field(description="Column name (snake_case)")
    type: str = Field(description=f"One of: {', '.join(COLUMN_TYPES)}")
    nullable: bool = True
    primary_key: bool = False


class _Session(BaseModel):
    session_id: str
    purpose: str
    proposed_table: str
    proposed: list[ColumnInput]
    table_name: str
    columns: list[ColumnInput]
    status: str = "editing"  # editing | review | accepted


# Session state lives in this process only. It is keyed by a hash of the
# request, so replaying the same request reaches the same session.
_sessions: dict[str, _Session] = {}


def _cols(columns: list[ColumnInput]) -> list[Column]:
    return [Column.from_dict(c.model_dump()) for c in columns]


def _inputs(columns: list[Column]) -> list[ColumnInput]:
    return [ColumnInput(**c) for c in columns_as_dicts(columns)]


# ---------------------------------------------------------------------------
# Form specs: the form app is a generic renderer; the server decides what to show.
# ---------------------------------------------------------------------------


def _edit_form(session: _Session, errors: list[str] | None = None) -> dict[str, Any]:
    return {
        "session_id": session.session_id,
        "view": "edit",
        "eyebrow": "Table designer",
        "title": f"Review the proposed table “{session.table_name}”",
        "description": (
            f"Based on your request ({session.purpose}), the tool proposes these columns. "
            "Rename, retype or add columns, then continue to the review."
        ),
        "steps": _STEPS,
        "step_index": 0,
        "errors": errors or [],
        "fields": [
            {
                "id": "table_name",
                "kind": "text",
                "label": "Table name",
                "value": session.table_name,
                "required": True,
                "pattern": IDENTIFIER_PATTERN,
                "help": "Lowercase letters, digits and underscores.",
            },
            {
                "id": "columns",
                "kind": "columns",
                "label": "Columns",
                "value": [c.model_dump() for c in session.columns],
                "type_options": list(COLUMN_TYPES),
                "pattern": IDENTIFIER_PATTERN,
                "min_rows": 1,
            },
        ],
        "actions": [
            {
                "id": "review",
                "label": "Continue to review",
                "busy_label": "Checking design…",
                "kind": "primary",
                "tool": "submit_table_design",
                "include_fields": True,
            }
        ],
    }


def _review_form(session: _Session) -> dict[str, Any]:
    columns = _cols(session.columns)
    changes = diff_columns(_cols(session.proposed), columns)
    if session.table_name != session.proposed_table:
        changes.insert(
            0,
            {
                "kind": "renamed",
                "column": "(table)",
                "detail": f"{session.proposed_table} → {session.table_name}",
            },
        )
    return {
        "session_id": session.session_id,
        "view": "review",
        "eyebrow": "Table designer",
        "title": f"Accept the design for “{session.table_name}”?",
        "description": "Check the changes and the generated SQL. Accepting returns this design to the agent.",
        "steps": _STEPS,
        "step_index": 1,
        "errors": [],
        "summary": [
            {"label": "Table", "value": session.table_name},
            {"label": "Columns", "value": str(len(columns))},
            {"label": "Primary key", "value": ", ".join(c.name for c in columns if c.primary_key) or "—"},
            {"label": "Changes from proposal", "value": str(len(changes))},
        ],
        "columns": [c.model_dump() for c in session.columns],
        "changes": changes,
        "ddl": render_ddl(session.table_name, columns),
        "actions": [
            {
                "id": "back",
                "label": "Back to edit",
                "busy_label": "Opening editor…",
                "kind": "secondary",
                "tool": "edit_table_design",
            },
            {
                "id": "accept",
                "label": "Accept design",
                "busy_label": "Accepting…",
                "kind": "primary",
                "tool": "confirm_table_design",
            },
        ],
    }


def _form_result(text: str, form: dict[str, Any]) -> ToolResult:
    return ToolResult(content=[TextContent(type="text", text=text)], structured_content={"form": form})


def _session_or_error(session_id: str) -> _Session | ToolResult:
    session = _sessions.get(session_id)
    if session is None:
        return ToolResult(
            content=[TextContent(type="text", text=f"Unknown design session '{session_id}'.")],
            is_error=True,
        )
    return session


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool(
    app=AppConfig(resource_uri=FORM_URI, visibility=["model", "app"]),
    description=(
        "Propose columns for a new database table and let the user review and edit them in an "
        "interactive form. The call returns only after the user accepts the design; its result "
        "holds the approved table_name and columns. Then call create_table with exactly those values."
    ),
)
def propose_table(
    table_name: Annotated[str, Field(description="Requested table name, e.g. 'customers'")],
    purpose: Annotated[str, Field(description="One sentence on what the table stores")],
    fields: Annotated[
        list[str],
        Field(
            description=(
                "Plain names of the data to store, e.g. ['name', 'email', 'signup date']. "
                "Names only: the tool chooses the column types."
            )
        ),
    ],
) -> ToolResult:
    table = to_identifier(table_name)
    proposed = propose_columns(fields)
    session_id = session_id_for(table, fields)
    session = _Session(
        session_id=session_id,
        purpose=purpose.strip() or f"a table for {table}",
        proposed_table=table,
        proposed=_inputs(proposed),
        table_name=table,
        columns=_inputs(proposed),
    )
    _sessions[session_id] = session
    return _form_result(
        f"Proposed {len(proposed)} columns for table '{table}' "
        f"({', '.join(c.name for c in proposed)}). Waiting for the user to review them in the form.",
        _edit_form(session),
    )


@mcp.tool(
    app=AppConfig(visibility=["app"]),
    description="Form action: validate the edited design and show the review form.",
)
def submit_table_design(session_id: str, table_name: str, columns: list[ColumnInput]) -> ToolResult:
    found = _session_or_error(session_id)
    if isinstance(found, ToolResult):
        return found
    session = found
    session.table_name = table_name.strip()
    session.columns = [ColumnInput(**Column.from_dict(c.model_dump()).__dict__) for c in columns]
    errors = validate(session.table_name, _cols(session.columns))
    if errors:
        session.status = "editing"
        return _form_result(
            "The design has problems; showing the edit form again.", _edit_form(session, errors)
        )
    session.status = "review"
    return _form_result("Design is valid; showing the review form.", _review_form(session))


@mcp.tool(app=AppConfig(visibility=["app"]), description="Form action: go back from review to the edit form.")
def edit_table_design(session_id: str) -> ToolResult:
    found = _session_or_error(session_id)
    if isinstance(found, ToolResult):
        return found
    found.status = "editing"
    return _form_result("Showing the edit form.", _edit_form(found))


@mcp.tool(
    app=AppConfig(visibility=["app"]),
    description="Form action: accept the reviewed design and return it to the agent.",
)
def confirm_table_design(session_id: str) -> ToolResult:
    found = _session_or_error(session_id)
    if isinstance(found, ToolResult):
        return found
    session = found
    if session.status != "review":
        return ToolResult(
            content=[TextContent(type="text", text="Review the design before accepting it.")], is_error=True
        )
    session.status = "accepted"
    columns = _cols(session.columns)
    result = {
        "status": "accepted",
        "table_name": session.table_name,
        "columns": columns_as_dicts(columns),
        "changes": diff_columns(_cols(session.proposed), columns),
    }
    names = ", ".join(c.name for c in columns)
    return ToolResult(
        content=[
            TextContent(
                type="text",
                text=f"The user accepted table '{session.table_name}' with columns: {names}.",
            )
        ],
        structured_content={
            "status": "completed",
            "result": result,
            "user_message": f"I accepted the design for table '{session.table_name}' ({len(columns)} columns).",
            "done": {
                "title": f"Design for “{session.table_name}” accepted",
                "description": "The agent received the approved columns and will continue.",
                "ddl": render_ddl(session.table_name, columns),
                "steps": _STEPS,
                "step_index": 2,
            },
        },
    )


@mcp.tool(
    description=(
        "Create a database table from an approved design. Pass the table_name and columns returned "
        "by propose_table. Returns the executed DDL."
    ),
)
def create_table(table_name: str, columns: list[ColumnInput]) -> dict[str, Any]:
    cols = _cols(columns)
    errors = validate(table_name, cols)
    if errors:
        return {"status": "error", "errors": errors}
    return {
        "status": "created",
        "table_name": table_name,
        "column_count": len(cols),
        "ddl": render_ddl(table_name, cols),
    }


# ---------------------------------------------------------------------------
# UI resource
# ---------------------------------------------------------------------------


def _inline_module(bundle: str) -> str:
    """Turn the ES-module bundle into a script that sets ``globalThis.McpExtApps``.

    The form runs with ``connect-src 'none'`` and no remote script domains, so
    the guest SDK is inlined instead of loaded from a CDN.
    """
    match = re.search(r"export\s*\{([^}]*)\}\s*;?\s*$", bundle)
    if not match:
        raise RuntimeError("Unexpected ext-apps bundle format: no trailing export list")
    pairs = []
    for spec in match.group(1).split(","):
        spec = spec.strip()
        if not spec:
            continue
        local, _, exported = spec.partition(" as ")
        pairs.append(f"{(exported or local).strip()}:{local.strip()}")
    return bundle[: match.start()] + "globalThis.McpExtApps={" + ",".join(pairs) + "};"


@lru_cache(maxsize=1)
def form_html() -> str:
    template = (_HERE / "form_app.html").read_text(encoding="utf-8")
    bundle = (_HERE / "third_party" / "ext-apps-app-with-deps.js").read_text(encoding="utf-8")
    return template.replace(
        "/*__EXT_APPS_BUNDLE__*/", _inline_module(bundle).replace("</script", "<\\/script")
    )


@mcp.resource(
    FORM_URI,
    name="Table designer form",
    description="Interactive form for reviewing and editing a proposed table design.",
    app=AppConfig(prefers_border=False),
)
def table_designer_form() -> str:
    return form_html()


def main() -> None:
    host = os.environ.get("TABLE_DESIGNER_HOST", "127.0.0.1")
    port = int(os.environ.get("TABLE_DESIGNER_PORT", "8210"))
    mcp.run(transport="http", host=host, port=port)


if __name__ == "__main__":
    main()
