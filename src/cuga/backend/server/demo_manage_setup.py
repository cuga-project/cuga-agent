"""Helper to setup agent config (draft + v1) for demo and demo_crm with manage experience."""

import asyncio
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


def _get_filesystem_tool() -> dict[str, Any]:
    fs_port = int(os.environ.get("DYNACONF_SERVER_PORTS__FILESYSTEM_MCP", "8112"))
    return {
        "name": "filesystem",
        "url": f"http://localhost:{fs_port}/sse",
        "transport": "sse",
        "description": "Standard file system operations for workspace management",
    }


def _get_email_tool() -> dict[str, Any]:
    email_port = int(os.environ.get("DYNACONF_SERVER_PORTS__EMAIL_MCP", "8000"))
    return {
        "name": "email",
        "url": f"http://localhost:{email_port}/sse",
        "transport": "sse",
        "description": "Standard email server connected to the user's email",
    }


def _get_crm_tool() -> dict[str, Any]:
    crm_port = int(os.environ.get("DYNACONF_SERVER_PORTS__CRM_API", str(settings.server_ports.crm_api)))
    return {
        "name": "crm",
        "type": "openapi",
        "url": f"http://localhost:{crm_port}/openapi.json",
        "description": "CRM API for territory accounts, client info, job roles, contacts",
    }


def _get_digital_sales_tool() -> dict[str, Any]:
    return {
        "name": "digital_sales",
        "type": "openapi",
        "url": DIGITAL_SALES_OPENAPI_URL,
        "description": DIGITAL_SALES_DESCRIPTION,
    }


def build_tools_from_apps(
    *,
    crm: bool = False,
    email: bool = False,
    digital_sales: bool = False,
    filesystem: bool = True,
) -> list[dict[str, Any]]:
    """Build tools list from enabled app flags. Order: filesystem, email, crm, digital_sales."""
    tools: list[dict[str, Any]] = []
    if filesystem:
        tools.append(_get_filesystem_tool())
    if email:
        tools.append(_get_email_tool())
    if crm:
        tools.append(_get_crm_tool())
    if digital_sales:
        tools.append(_get_digital_sales_tool())
    return tools


def get_default_apps_for_preset(preset: str) -> dict[str, bool]:
    """Return default app flags for a given preset (demo, demo_crm, manager)."""
    if preset == "demo_crm":
        return {"crm": True, "email": True, "digital_sales": False, "filesystem": True}
    if preset == "demo":
        return {"crm": False, "email": False, "digital_sales": True, "filesystem": True}
    return {"crm": False, "email": False, "digital_sales": False, "filesystem": True}


def setup_demo_manage_config(
    demo_type: str,
    agent_id: str = "cuga-default",
    no_email: bool = False,
    tools: list[dict[str, Any]] | None = None,
) -> None:
    """
    Reset config db, then setup agent config (draft + v1) for demo or demo_crm.
    Uses same SSE links as cli for filesystem, email, crm.
    If tools is provided, uses it; otherwise builds from demo_type and no_email.
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
    if tools is None:
        defaults = get_default_apps_for_preset(demo_type)
        if no_email:
            defaults["email"] = False
        tools = build_tools_from_apps(**defaults)
    use_crm_starters = demo_type == "demo_crm" or (
        demo_type == "manager" and tools and any(t.get("name") == "crm" for t in tools)
    )
    homescreen = (
        {"isOn": True, "greeting": "Hello, how can I help you today?", "starters": DEMO_CRM_STARTERS}
        if use_crm_starters
        else DEFAULT_HOMESCREEN
    )
    config = {"tools": tools, "policies": [], "homescreen": homescreen}

    async def _setup():
        await save_draft(config, agent_id)
        await save_config(config, agent_id)

    asyncio.run(_setup())
