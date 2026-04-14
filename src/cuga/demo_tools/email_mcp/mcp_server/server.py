#!/usr/bin/env python3
"""
MCP Email Server using fastmcp over SSE (no stdio).

- Exposes an MCP server over SSE on http://127.0.0.1:8080
- Tools:
    send_email(from_addr, to_addrs, subject, text_body?, html_body?)
    list_emails(query?, limit=50)
    get_email(id)

- Uses the SMTP sink at localhost:1025 (see mail_sink/server.py)
- Lists/reads messages by scanning ./mcp_mail/*.json

Install:
  pip install fastmcp uvicorn

Run:
  python mcp_server/server.py

MCP client (example Claude Desktop config):
{
  "mcpServers": {
    "local-email": {
      "type": "sse",
      "url": "http://127.0.0.1:8080"
    }
  }
}
"""

from __future__ import annotations

from typing import List, Optional
import os

from fastmcp import FastMCP
from pydantic import BaseModel, Field

from utils import send_email_smtp, list_emails_fs, get_email_fs

mcp = FastMCP(name="LocalEmailMCP")

# --------- Schemas -----------------------------------------------------------


class SendEmailParams(BaseModel):
    from_addr: str = Field(..., description="Envelope From and 'From' header")
    to_addrs: List[str] = Field(..., description="Recipient list (To header)")
    subject: str
    text_body: Optional[str] = Field(None, description="Plaintext body")
    html_body: Optional[str] = Field(None, description="HTML body")


class SendEmailResult(BaseModel):
    ok: bool
    message_id: Optional[str] = None
    error: Optional[str] = None


class ListEmailsParams(BaseModel):
    query: Optional[str] = Field(
        None, description="Case-insensitive substring search over subject/from/to/text"
    )
    limit: int = Field(50, ge=1, le=1000)


class ListItem(BaseModel):
    id: str
    subject: Optional[str] = None
    from_: Optional[str] = Field(None, alias="from")
    to: Optional[List[str]] = None
    date: Optional[str] = None

    class Config:
        allow_population_by_field_name = True


class ListEmailsResult(BaseModel):
    ok: bool = True
    items: List[ListItem]


class GetEmailParams(BaseModel):
    id: str


class GetEmailResult(BaseModel):
    ok: bool = True
    email: dict


class ErrorResult(BaseModel):
    ok: bool = False
    error: dict


# --------- Tools -------------------------------------------------------------


@mcp.tool()
def send_email(to_address: str, subject: str, body: str) -> dict | ErrorResult:
    """
    Send an email via the local SMTP sink (localhost:1025).
    Sends multipart/alternative if both text_body and html_body are provided.
    """
    try:
        res = send_email_smtp(
            from_addr="test@mail.com",
            to_addrs=[to_address],
            subject=subject,
            text_body=body,
            attachments_paths=None,
        )
    except FileNotFoundError as e:
        return ErrorResult(ok=False, error={"code": "ATTACHMENT_NOT_FOUND", "message": str(e)})
    except Exception:
        return ErrorResult(ok=False, error={"code": "SMTP_ERROR", "message": res.error or "unknown"})
    if res.success:
        return {
            "ok": True,
            "message_id": res.message_id,
        }
    return ErrorResult(ok=False, error={"code": "SMTP_ERROR", "message": res.error or "unknown"})


@mcp.tool()
def list_emails(query: str) -> dict:
    """
    List captured emails from ./mcp_mail/*.json, newest first.

    Query semantics:
    - Optional, case-insensitive **substring** match across these fields:
      subject (string), from (string), to (array joined as one string), and text (plaintext body).
    - Does NOT search: cc, bcc, attachments' content (only metadata stored),
      Message-ID, In-Reply-To, References, or other headers.
    - No regex/wildcards: the literal query is matched as-is (case-insensitive).
      Spaces are literal; "weekly report" matches that exact sequence only.
    - Unicode: case-insensitive but not accent-insensitive (e.g., "cafe" != "café").
    - Empty or omitted query returns all messages.

    Examples:
    - "ops@" matches From: "Ops Bot <ops@example.local>"
    - "lead@customer" matches a recipient in To
    - "weekly" matches subject/text containing that word
    - "report</b>" won't match (HTML not searched)
    """

    rows = list_emails_fs(query=query, limit=10)
    items = []
    for r in rows:
        items.append(
            ListItem(
                id=r.get("id"),
                subject=r.get("subject"),
                **{"from": r.get("from")},  # alias via from_
                to=r.get("to"),
                date=r.get("date"),
            )
        )
    return {
        "ok": True,
        "result": items,
    }


@mcp.tool()
def read_email(id: str) -> dict | ErrorResult:
    """
    Fetch the full JSON record for a specific email id (UUID filename stem).
    """
    data = get_email_fs(id)
    if "error" in data:
        return ErrorResult(ok=False, error=data["error"])
    return {
        "ok": True,
        "result": data,
    }


# --------- Server bootstrap (SSE) -------------------------------------------
def main():
    print("[Email MCP] Environment check:")
    print(
        f"[Email MCP]   DYNACONF_SERVER_PORTS__EMAIL_MCP = {os.environ.get('DYNACONF_SERVER_PORTS__EMAIL_MCP', 'NOT SET')}"
    )
    print(
        f"[Email MCP]   DYNACONF_SERVER_PORTS__EMAIL_SINK = {os.environ.get('DYNACONF_SERVER_PORTS__EMAIL_SINK', 'NOT SET')}"
    )
    print(f"[Email MCP]   All DYNACONF env vars: {[k for k in os.environ.keys() if 'DYNACONF' in k]}")

    mcp_port = int(os.environ.get("DYNACONF_SERVER_PORTS__EMAIL_MCP", "8000"))
    smtp_port = int(os.environ.get("DYNACONF_SERVER_PORTS__EMAIL_SINK", "1025"))
    print(f"[Email MCP] Starting MCP server on port {mcp_port}")
    print(f"[Email MCP] Will connect to SMTP sink at localhost:{smtp_port}")
    mcp.run(transport="sse", port=mcp_port)
