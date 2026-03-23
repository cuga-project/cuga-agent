"""Helper to setup agent config (draft + v1) for demo and demo_crm with manage experience."""

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from cuga.config import settings

_OAK_POLICIES_PATH = Path(__file__).resolve().parent / "demo_setup_utils" / "oak_policies.json"


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


def _get_oak_health_tool() -> dict[str, Any]:
    port = int(
        os.environ.get(
            "DYNACONF_SERVER_PORTS__OAK_HEALTH_API",
            str(getattr(settings.server_ports, "oak_health_api", 8090)),
        )
    )
    return {
        "name": "oak_health",
        "type": "openapi",
        "url": f"http://localhost:{port}/openapi.json",
        "description": (
            "Healthcare insurance member APIs: claims, EOBs, benefits, coverage, "
            "in-network providers, referrals, and accumulators"
        ),
    }


def load_oak_policy_entries() -> list[dict[str, Any]]:
    with _OAK_POLICIES_PATH.open(encoding="utf-8") as f:
        data = json.load(f)
    return list(data.get("policies") or [])


def _get_docs_tool() -> dict[str, Any]:
    docs_port = int(
        os.environ.get(
            "DYNACONF_SERVER_PORTS__DOCS_MCP", str(getattr(settings.server_ports, "docs_mcp", 8113))
        )
    )
    return {
        "name": "docs",
        "url": f"http://localhost:{docs_port}/sse",
        "transport": "sse",
        "description": "IBM Documentation search and page analysis (search, summarize, ask questions)",
    }


def build_tools_from_apps(
    *,
    crm: bool = False,
    email: bool = False,
    digital_sales: bool = False,
    docs: bool = False,
    filesystem: bool = True,
    oak_health: bool = False,
) -> list[dict[str, Any]]:
    """Build tools list from enabled app flags. Order: filesystem, email, crm, docs, digital_sales, oak_health."""
    tools: list[dict[str, Any]] = []
    if filesystem:
        tools.append(_get_filesystem_tool())
    if email:
        tools.append(_get_email_tool())
    if crm:
        tools.append(_get_crm_tool())
    if docs:
        tools.append(_get_docs_tool())
    if digital_sales:
        tools.append(_get_digital_sales_tool())
    if oak_health:
        tools.append(_get_oak_health_tool())
    return tools


DOCS_OUTPUT_FORMATTER = {
    "id": "output_formatter_docs_citations",
    "name": "Docs citations and actions",
    "description": "Forces citation of all visited pages and actions performed when using IBM Docs tools",
    "type": "output_formatter",
    "policy_type": "output_formatter",
    "triggers": [
        {"type": "keyword", "value": [" "], "target": "agent_response", "operator": "or"},
    ],
    "format_type": "markdown",
    "format_config": """Reformat the response to ALWAYS include these sections when documentation search tools were used:

## Answer
(Preserve the main answer content here)

## Sources
List every web page or documentation URL that was visited. For each:
- Full URL
- Brief note on what was retrieved (e.g. "Search results", "Page summary", "Q&A answer")
If no pages were visited, write: "No external pages were consulted."

## Actions Performed
List each documentation tool action: ibm_search_doc, filter_grep, fetch_doc_page — and what it was used for (query, URL, pattern, etc.). If no docs tools were used, write: "No documentation tools were used."

Preserve all factual information. Do not add information not in the original. Only add structure and citations based on what the response implies or states.""",
    "priority": 80,
    "enabled": True,
}

DOCS_PLAYBOOK = {
    "id": "playbook_ibm_docs_tools",
    "name": "IBM Docs tool usage guide",
    "description": "Guides the agent on how to use IBM Documentation MCP tools (ibm_search_doc, filter_grep, fetch_doc_page) for docs-related questions",
    "type": "playbook",
    "policy_type": "playbook",
    "triggers": [
        {
            "type": "keyword",
            "value": ["IBM docs", "IBM documentation", "docs search", "documentation"],
            "target": "intent",
            "operator": "or",
        },
    ],
    "markdown_content": """# IBM Documentation Tool Usage

When the user asks about IBM products, documentation, or technical topics, use these tools as follows:

## Step 1: Search with ibm_search_doc (for discovery)

- **Call once** with a broad, descriptive query. One call returns full page content — do NOT call multiple times for the same topic.
- Use queries like: "MQ persistent messaging configuration", "Kubernetes deployment", "Db2 backup restore" (not just "MQ" or "Db2").
- The tool returns aggregated markdown from up to 3–5 pages. This is already comprehensive.

## Step 2: Narrow down with filter_grep (only when needed)

- Use **filter_grep** only when you specifically need to extract targeted lines (e.g. config keys, error codes, API endpoints). Do NOT use it by default — the full content from ibm_search_doc or fetch_doc_page is usually sufficient.
- When you do use it: pass the markdown content plus a regex pattern. Examples:
  - `filter_grep(content, r"timeout|retry")` — timeout or retry mentions
  - `filter_grep(content, r"Error \\d+")` — error codes
  - `filter_grep(content, r"api_key|API_KEY")` — auth-related

## Step 3: Follow-up with fetch_doc_page (for navigation)

- Use **fetch_doc_page** when the user provides an IBM docs URL or asks to visit a specific page:
  - User pastes a URL (e.g. "What does this say? https://www.ibm.com/docs/...")
  - User asks to open "the second result" or a specific link from your earlier search
  - User wants to follow a linked page mentioned in the documentation
- Only ibm.com/docs and ibm.com/support URLs are allowed.
- Do NOT use fetch_doc_page to re-fetch a page you already have from ibm_search_doc.

## Key rules

- One ibm_search_doc call per topic — do not retry with different queries to "get more".
- Use filter_grep only when needed (targeted extraction), not as a default step.
- Use fetch_doc_page for follow-up navigation when the user provides a URL or asks about a specific page. Large pages (>100k chars) return a summary plus full content.
- Cite all URLs and sources in your response.
""",
    "steps": [
        {
            "step_number": 1,
            "instruction": "Call ibm_search_doc with a broad, descriptive query.",
            "expected_outcome": "Receive full page content as markdown.",
            "tools_allowed": None,
        },
        {
            "step_number": 2,
            "instruction": "Use filter_grep only when needed (e.g. specific config keys, error codes); do not use by default.",
            "expected_outcome": "Structured matches with line numbers.",
            "tools_allowed": None,
        },
        {
            "step_number": 3,
            "instruction": "Use fetch_doc_page when the user provides a URL or asks to visit a specific page from search results.",
            "expected_outcome": "Full page content for the requested URL.",
            "tools_allowed": None,
        },
    ],
    "priority": 70,
    "enabled": True,
}


def get_default_apps_for_preset(preset: str) -> dict[str, bool]:
    """Return default app flags for a given preset (demo, demo_crm, demo_docs, demo_health, manager)."""
    if preset == "demo_crm":
        return {
            "crm": True,
            "email": True,
            "digital_sales": False,
            "docs": False,
            "filesystem": True,
            "oak_health": False,
        }
    if preset == "demo_docs":
        return {
            "crm": False,
            "email": False,
            "digital_sales": False,
            "docs": True,
            "filesystem": False,
            "oak_health": False,
        }
    if preset == "demo_health":
        return {
            "crm": False,
            "email": False,
            "digital_sales": False,
            "docs": False,
            "filesystem": False,
            "oak_health": True,
        }
    if preset == "demo":
        return {
            "crm": False,
            "email": False,
            "digital_sales": True,
            "docs": False,
            "filesystem": True,
            "oak_health": False,
        }
    return {
        "crm": False,
        "email": False,
        "digital_sales": False,
        "docs": False,
        "filesystem": True,
        "oak_health": False,
    }


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
    DEMO_DOCS_STARTERS = [
        "What was the latest watsonx orchestrate release?",
        "How do I configure IBM MQ for persistent messaging?",
        "What are the system requirements for IBM Db2?",
        "Find the steps to deploy Kubernetes on IBM Cloud.",
        "Show me the OpenShift container platform installation guide.",
    ]
    DEMO_HEALTH_STARTERS = [
        "Show my last approved claims and share the URL of any EOB PDF (member 121231234)",
        "Find in-network primary care doctors near me that accept new patients",
        "Find knee surgeons nearby and what are my benefits for surgery",
        "What is my deductible and out-of-pocket progress this plan year?",
        "Check the status of my referral and where it was sent",
    ]
    reset_config_db()
    if tools is None:
        defaults = get_default_apps_for_preset(demo_type)
        if no_email:
            defaults["email"] = False
        tools = build_tools_from_apps(**defaults)
    use_crm_starters = demo_type == "demo_crm"
    use_docs_starters = demo_type == "demo_docs"
    use_health_starters = demo_type == "demo_health"
    if use_crm_starters:
        homescreen = {
            "isOn": True,
            "greeting": "Hello, how can I help you today?",
            "starters": DEMO_CRM_STARTERS,
        }
    elif use_docs_starters:
        homescreen = {
            "isOn": True,
            "greeting": "Search IBM documentation for answers.",
            "starters": DEMO_DOCS_STARTERS,
        }
    elif use_health_starters:
        homescreen = {
            "isOn": True,
            "greeting": "Ask about claims, benefits, coverage, and finding in-network care.",
            "starters": DEMO_HEALTH_STARTERS,
        }
    else:
        homescreen = DEFAULT_HOMESCREEN
    llm_api_key_ref = ""
    try:
        from cuga.backend.secrets.seed import resolve_llm_api_key_ref

        llm_api_key_ref = resolve_llm_api_key_ref()
    except Exception:
        pass
    llm_cfg: dict[str, Any] = {"model": os.environ.get("MODEL_NAME", "")}
    if llm_api_key_ref:
        llm_cfg["api_key"] = llm_api_key_ref
    if use_crm_starters:
        agent_meta = {
            "name": "CRM Agent",
            "description": "CRM-enabled agent with email and filesystem for managing contacts and accounts",
        }
    elif use_docs_starters:
        agent_meta = {
            "name": "IBM Documentation Agent",
            "description": "Agent focused on IBM Documentation search and analysis",
        }
    elif use_health_starters:
        agent_meta = {
            "name": "Member & Benefits Assistant",
            "description": (
                "Healthcare insurance assistant for claims, EOBs, benefits, accumulators, "
                "referrals, and finding in-network providers—grounded in member coverage APIs"
            ),
        }
    else:
        agent_meta = {
            "name": "Digital Sales Agent",
            "description": "Agent with Digital Sales API and filesystem for sales workflows",
        }
    policies: list[dict[str, Any]] = []
    if tools and any(t.get("name") == "oak_health" for t in tools):
        policies.extend(load_oak_policy_entries())
    if tools and any(t.get("name") == "docs" for t in tools):
        policies.append(DOCS_PLAYBOOK)
        policies.append(DOCS_OUTPUT_FORMATTER)
    policies_struct: dict[str, Any] = {"enablePolicies": True, "policies": policies}
    config: dict[str, Any] = {
        "agent": agent_meta,
        "tools": tools,
        "policies": policies_struct,
        "homescreen": homescreen,
        "llm": llm_cfg,
    }
    if tools and any(t.get("name") == "docs" for t in tools):
        config["feature_flags"] = config.get("feature_flags") or {}
        config["feature_flags"]["enable_todos"] = True

    async def _setup():
        await save_draft(config, agent_id)
        await save_config(config, agent_id)

    asyncio.run(_setup())
