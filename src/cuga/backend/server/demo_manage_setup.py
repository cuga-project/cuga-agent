"""Helper to setup agent config (draft + v1) for demo and demo_crm with manage experience."""

import asyncio
import logging
import os
from typing import Any

from cuga.config import settings

logger = logging.getLogger("cuga.demo")


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


def _get_knowledge_tool() -> dict[str, Any]:
    return {
        "name": "knowledge",
        "type": "mcp",
        "command": "python3",
        "args": ["-m", "cuga.backend.knowledge.mcp_server"],
        "transport": "stdio",
        "description": "Knowledge service for semantic document search and RAG-enhanced conversations over knowledge bases",
        "env": {
            "CUGA_BACKEND_URL": "CUGA_BACKEND_URL",
            "CUGA_INTERNAL_TOKEN_FILE": "CUGA_INTERNAL_TOKEN_FILE",
            "CUGA_AGENT_ID": "CUGA_AGENT_ID",
        },
    }


def _knowledge_configured() -> bool:
    """Knowledge is available when enabled in settings (default: true)."""
    try:
        from cuga.config import settings
        kb = settings.get("knowledge", {})
        if not kb:
            return True
        return (
            kb.get("enabled", True)
            and (kb.get("agent_level_enabled", True) or kb.get("session_level_enabled", True))
        )
    except Exception:
        return True


def build_tools_from_apps(
    *,
    crm: bool = False,
    email: bool = False,
    digital_sales: bool = False,
    filesystem: bool = True,
    knowledge: bool = False,
) -> list[dict[str, Any]]:
    """Build tools list from enabled app flags. Order: filesystem, email, crm, digital_sales, knowledge."""
    tools: list[dict[str, Any]] = []
    if filesystem:
        tools.append(_get_filesystem_tool())
    if email:
        tools.append(_get_email_tool())
    if crm:
        tools.append(_get_crm_tool())
    if digital_sales:
        tools.append(_get_digital_sales_tool())
    if knowledge:
        tools.append(_get_knowledge_tool())
    return tools


def get_default_apps_for_preset(preset: str) -> dict[str, bool]:
    """Return default app flags for a given preset (demo, demo_crm, manager).
    Knowledge is always enabled — engine auto-initializes."""
    knowledge = _knowledge_configured()
    if preset == "demo_crm":
        return {"crm": True, "email": True, "digital_sales": False, "filesystem": True, "knowledge": knowledge}
    if preset == "demo":
        return {"crm": False, "email": False, "digital_sales": True, "filesystem": True, "knowledge": knowledge}
    return {"crm": False, "email": False, "digital_sales": False, "filesystem": True, "knowledge": knowledge}


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

    # Clear ALL knowledge data for a truly clean demo slate.
    # This removes files, vectors, and metadata for this agent's collections.
    # The engine will recreate empty collections on startup.
    try:
        from cuga.backend.knowledge.config import KnowledgeConfig as _KC
        from cuga.config import settings as _settings
        import re as _re, shutil

        _kc = _KC.from_settings(_settings)
        _san = _re.sub(r"[^a-zA-Z0-9_]", "_", agent_id)
        prefix = f"kb_agent_{_san}"

        # Remove source files for agent collections
        files_dir = _kc.persist_dir / "files"
        if files_dir.exists():
            for d in files_dir.iterdir():
                if d.is_dir() and d.name.startswith(prefix):
                    shutil.rmtree(d, ignore_errors=True)
                    logger.info("Demo reset: cleared %s", d.name)

        # Remove the entire vector DB and metadata so stale vectors don't persist.
        # The engine recreates these on startup.
        for db_file in ("knowledge.db", "metadata.db"):
            db_path = _kc.persist_dir / db_file
            if db_path.exists():
                db_path.unlink()
                logger.info("Demo reset: removed %s", db_file)
    except Exception as e:
        logger.debug("Demo reset: knowledge cleanup skipped: %s", e)

    if tools is None:
        defaults = get_default_apps_for_preset(demo_type)
        if no_email:
            defaults["email"] = False
        tools = build_tools_from_apps(**defaults)
    else:
        # Auto-append knowledge tool if knowledge is enabled and not already present
        if _knowledge_configured() and not any(t.get("name") == "knowledge" for t in tools):
            tools.append(_get_knowledge_tool())
    use_crm_starters = demo_type == "demo_crm" or (
        demo_type == "manager" and tools and any(t.get("name") == "crm" for t in tools)
    )
    homescreen = (
        {"isOn": True, "greeting": "Hello, how can I help you today?", "starters": DEMO_CRM_STARTERS}
        if use_crm_starters
        else DEFAULT_HOMESCREEN
    )
    llm_api_key_ref = ""
    try:
        from cuga.backend.secrets.seed import resolve_llm_api_key_ref

        llm_api_key_ref = resolve_llm_api_key_ref()
    except Exception:
        pass
    llm_cfg: dict[str, Any] = {"model": os.environ.get("MODEL_NAME", "")}
    if llm_api_key_ref:
        llm_cfg["api_key"] = llm_api_key_ref
    # Include knowledge vector config hash so the collection name is consistent
    # between configure and published states from the first startup.
    knowledge_cfg: dict[str, Any] = {}
    try:
        from cuga.backend.knowledge.config import KnowledgeConfig as _KC
        from cuga.config import settings as _settings
        _kc = _KC.from_settings(_settings)
        knowledge_cfg["_vector_config_hash"] = _kc.vector_config_hash()
    except Exception:
        pass
    config = {"tools": tools, "policies": [], "homescreen": homescreen, "llm": llm_cfg, "knowledge": knowledge_cfg}

    async def _setup():
        await save_draft(config, agent_id)
        await save_config(config, agent_id)

    asyncio.run(_setup())
