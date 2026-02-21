"""Helper to setup agent config (draft + v1) for demo and demo_crm with manage experience."""

import os
from typing import Any

from cuga.config import settings


DIGITAL_SALES_OPENAPI_URL = (
    "https://digitalsales.19pc1vtv090u.us-east.codeengine.appdomain.cloud/openapi.json"
)
DIGITAL_SALES_DESCRIPTION = (
    "This Digital Sales Skills API provides sales professionals with a unified interface "
    "to access territory accounts, retrieve client information from TPP, manage job roles, "
    "and synchronize contacts between Zoominfo and Salesloft—streamlining the process of "
    "managing customer relationships and sales data across multiple platforms."
)


def _demo_tools() -> list[dict[str, Any]]:
    """Tools for demo: filesystem + digital_sales OpenAPI. Same SSE links as cli."""
    fs_port = int(os.environ.get("DYNACONF_SERVER_PORTS__FILESYSTEM_MCP", "8112"))
    return [
        {
            "name": "filesystem",
            "url": f"http://localhost:{fs_port}/sse",
            "transport": "sse",
            "description": "Standard file system operations for workspace management",
        },
        {
            "name": "digital_sales",
            "type": "openapi",
            "url": DIGITAL_SALES_OPENAPI_URL,
            "description": DIGITAL_SALES_DESCRIPTION,
        },
    ]


def _demo_crm_tools(no_email: bool = False) -> list[dict[str, Any]]:
    """Tools for demo_crm: filesystem, email (if enabled), crm. Same SSE links as cli."""
    fs_port = int(os.environ.get("DYNACONF_SERVER_PORTS__FILESYSTEM_MCP", "8112"))
    email_port = int(os.environ.get("DYNACONF_SERVER_PORTS__EMAIL_MCP", "8000"))
    crm_port = int(os.environ.get("DYNACONF_SERVER_PORTS__CRM_API", str(settings.server_ports.crm_api)))

    tools: list[dict[str, Any]] = [
        {
            "name": "filesystem",
            "url": f"http://localhost:{fs_port}/sse",
            "transport": "sse",
            "description": "Standard file system operations for workspace management",
        },
        {
            "name": "crm",
            "type": "openapi",
            "url": f"http://localhost:{crm_port}/openapi.json",
            "description": "CRM API for territory accounts, client info, job roles, contacts",
        },
    ]
    if not no_email:
        tools.insert(
            1,
            {
                "name": "email",
                "url": f"http://localhost:{email_port}/sse",
                "transport": "sse",
                "description": "Standard email server connected to the user's email",
            },
        )
    return tools


def setup_demo_manage_config(
    demo_type: str,
    agent_id: str = "cuga-default",
    no_email: bool = False,
) -> None:
    """
    Reset config db, then setup agent config (draft + v1) for demo or demo_crm.
    Uses same SSE links as cli for filesystem, email, crm.
    """
    from cuga.backend.server.config_store import (
        reset_config_db,
        save_config,
        save_draft,
    )

    DEFAULT_HOMESCREEN = {
        "isOn": True,
        "greeting": "Hello, how can I help you today?",
        "starters": ["Hi, what can you do for me?"],
    }
    DEMO_CRM_STARTERS = [
        "From the list of emails in the file contacts.txt, please filter those who exist in the CRM application. "
        "For the filtered contacts, retrieve their name and their associated account name, and calculate their "
        "account's revenue percentile across all accounts. Finally, draft an email based on email_template.md "
        "template summarizing the result and show it to me",
        "from contacts.txt show me which users belong to the crm system",
        "./cuga_workspace/cuga_playbook.md",
        "What is CUGA?",
    ]
    reset_config_db()
    tools = _demo_crm_tools(no_email) if demo_type == "demo_crm" else _demo_tools()
    homescreen = (
        {"isOn": True, "greeting": "Hello, how can I help you today?", "starters": DEMO_CRM_STARTERS}
        if demo_type == "demo_crm"
        else DEFAULT_HOMESCREEN
    )
    config = {"tools": tools, "policies": [], "homescreen": homescreen}
    save_draft(config, agent_id)
    save_config(config, agent_id)
