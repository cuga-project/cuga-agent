"""Helper to setup agent config (draft + v1) for demo and demo_crm with manage experience."""

import asyncio
import json
import logging
import os
import platform
from pathlib import Path
from typing import Any

from loguru import logger as loguru_logger

from cuga.config import settings

logger = logging.getLogger("cuga.demo")

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


def _normalize_demo_sandbox_mode() -> None:
    """Native sandbox uses macOS sandbox-exec; demo falls back to local elsewhere."""
    sandbox_mode = getattr(settings.advanced_features, "sandbox_mode", "opensandbox")
    if sandbox_mode == "native" and platform.system().lower() != "darwin":
        os.environ["DYNACONF_ADVANCED_FEATURES__SANDBOX_MODE"] = "local"
        settings.reload()
        logger.info("Demo sandbox_mode='native' is only supported on macOS; using 'local' instead.")


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
    """Knowledge is available when enabled in settings (default: false)."""
    if "DYNACONF_KNOWLEDGE__ENABLED" in os.environ:
        if os.environ["DYNACONF_KNOWLEDGE__ENABLED"].lower() not in ("true", "1", "yes", "on"):
            return False

        def _scope(name: str, default: bool) -> bool:
            v = os.environ.get(name)
            if v is None:
                return default
            return v.lower() in ("true", "1", "yes", "on")

        return _scope("DYNACONF_KNOWLEDGE__AGENT_LEVEL_ENABLED", True) or _scope(
            "DYNACONF_KNOWLEDGE__SESSION_LEVEL_ENABLED", True
        )
    try:
        from cuga.config import settings

        kb = settings.get("knowledge", {})
        if not kb:
            return False
        return kb.get("enabled", False) and (
            kb.get("agent_level_enabled", True) or kb.get("session_level_enabled", True)
        )
    except Exception:
        return False


HEALTH_USER_CONTEXT = """Member ID (string): 121231234
Location: latitude(str):40.7128, longitude(str):-74.0060
Current Date: 2025-12-31"""


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


def load_cuga_policy_entries_for_demo(cuga_folder: str | None = None) -> list[dict[str, Any]]:
    """One-way import of local .cuga policy markdown files into demo manage config.

    This intentionally does not initialize PolicyFilesystemSync. The UI can edit the
    managed config copy without writing changes back to .cuga.
    """
    from cuga.backend.cuga_graph.policy.folder_loader import (
        POLICY_CREATORS,
        parse_markdown_with_frontmatter,
    )

    root = Path(cuga_folder or os.getenv("CUGA_FOLDER", settings.policy.cuga_folder))
    if not root.exists():
        logger.warning(
            "Demo policy preload: .cuga folder not found at %s; skipping local policy import",
            root,
        )
        return []

    subfolders = {
        "playbooks": "playbook",
        "output_formatters": "output_formatter",
        "tool_guides": "tool_guide",
        "intent_guards": "intent_guard",
        "tool_approvals": "tool_approval",
    }
    policies: list[dict[str, Any]] = []

    for subfolder_name, default_policy_type in subfolders.items():
        subfolder = root / subfolder_name
        if not subfolder.exists():
            continue

        for policy_file in sorted(subfolder.glob("*.md")):
            try:
                frontmatter, content = parse_markdown_with_frontmatter(str(policy_file))
                policy_type = frontmatter.get("type", default_policy_type)
                creator = POLICY_CREATORS.get(policy_type)
                if creator is None:
                    raise ValueError(f"Unknown policy type: {policy_type}")

                policy_obj = creator(str(policy_file), frontmatter, content)
                policy_data = policy_obj.model_dump(mode="json")
                policy_data["policy_type"] = policy_data.get("type", policy_type)
                policies.append(policy_data)
            except Exception as e:
                logger.warning("Skipping demo policy preload from %s: %s", policy_file, e)

    if policies:
        logger.info("Preloaded %s local .cuga policy file(s) into demo config", len(policies))

    return policies


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
        "description": "Documentation page fetching and analysis (fetch, summarize, navigate links)",
    }


def build_tools_from_apps(
    *,
    crm: bool = False,
    email: bool = False,
    digital_sales: bool = False,
    docs: bool = False,
    filesystem: bool = True,
    oak_health: bool = False,
    knowledge: bool = False,
) -> list[dict[str, Any]]:
    """Build tools list from enabled app flags."""
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
    if knowledge:
        tools.append(_get_knowledge_tool())
    return tools


DOCS_OUTPUT_FORMATTER = {
    "id": "output_formatter_docs_citations",
    "name": "Docs citations and actions",
    "description": "Forces citation of all visited pages and actions performed when using docs tools",
    "type": "output_formatter",
    "policy_type": "output_formatter",
    "triggers": [
        {"type": "keyword", "value": [" "], "target": "agent_response", "operator": "or"},
    ],
    "format_type": "markdown",
    "format_config": """Reformat the response to ALWAYS include these sections when documentation tools were used:

## Answer
(Preserve the main answer content here)

## Sources
List every documentation URL that was visited. For each:
- Full URL
- Brief note on what was retrieved (e.g. "Page content", "Page summary", "Followed link")
If no pages were visited, write: "No external pages were consulted."

## Actions Performed
List each documentation tool action: search_doc, fetch_doc_page, filter_grep — and what it was used for (URL, keywords, etc.). If no docs tools were used, write: "No documentation tools were used."

Preserve all factual information. Do not add information not in the original. Only add structure and citations based on what the response implies or states.""",
    "priority": 80,
    "enabled": True,
}

DOCS_PLAYBOOK = {
    "id": "playbook_docs_tools",
    "name": "Docs tool usage guide",
    "description": "Guides the agent on how to use Documentation MCP tools (search_doc, fetch_doc_page, filter_grep) for docs-related questions",
    "type": "playbook",
    "policy_type": "playbook",
    "triggers": [
        {
            "type": "keyword",
            "value": ["docs", "documentation", "fetch page", " "],
            "target": "intent",
            "operator": "or",
        },
    ],
    "markdown_content": """# Documentation Tool Usage

Two tools are available: **search_doc** for discovery, **fetch_doc_page** for fetching a single known URL.

## Search URL pattern

Always construct the `search_url` using this pattern:
```
https://www.ibm.com/docs/en/search/<search term>
```
Replace `<search term>` with a descriptive phrase, using `+` to separate words. Examples:
- `https://www.ibm.com/docs/en/search/watsonx+orchestrate+release+notes`
- `https://www.ibm.com/docs/en/search/MQ+persistent+messaging+configuration`
- `https://www.ibm.com/docs/en/search/kubernetes+deployment`

## Step 1: Search with search_doc

- Call **search_doc** with the full search URL. It returns the search results page as plain markdown.
- **Inspect the output before proceeding:**
  ```
  result = await search_doc(search_url=...)
  print(result['result'])
  ```
  Read the markdown — it contains result titles and URLs linking to the actual documentation pages.
- One call is enough. Do NOT retry with different queries for the same topic.

## Step 2: Fetch a result page with fetch_doc_page

- After inspecting Step 1 output, pick the most relevant URL from the search results.
- Call **fetch_doc_page** with that URL to get the full page content and its same-domain links.
- **Inspect the output before proceeding:**
  ```
  result_page = await fetch_doc_page(url=...)
  print(result_page['result'])
  ```

## Step 3: Narrow down with filter_grep (only when needed)

- Only use this after inspecting prior output and confirming targeted extraction is needed.
- Use `keywords=` for plain-text search — separate alternatives with ` | `:
  ```
  result = await filter_grep(content=result_page['result'], keywords="timeout | retry")
  print(result)
  ```
- More examples:
  - `keywords="api key | authentication"`
  - `keywords="release notes | what's new | deprecation"`
- Only fall back to `pattern=` for raw regex when keywords aren't expressive enough. Never use both.

## Key rules

- Always inspect tool output before deciding the next step.
- search_doc returns the search page markdown — pick URLs from it to pass to fetch_doc_page.
- The page content from fetch_doc_page already contains all links inline as markdown — no need to grep for URLs.
- Use filter_grep only when targeted extraction is needed.
- Cite all visited URLs in your response.
""",
    "steps": [
        {
            "step_number": 1,
            "instruction": "Call search_doc with the full search URL (query embedded). Inspect the returned markdown — it contains result titles and links to documentation pages.",
            "expected_outcome": "Markdown content of the search results page.",
            "tools_allowed": None,
        },
        {
            "step_number": 2,
            "instruction": "Pick the most relevant URL from the search results and call fetch_doc_page. Inspect the output — links appear inline in the markdown — before deciding to follow one.",
            "expected_outcome": "Full page markdown with inline links and optional LLM summary for large pages.",
            "tools_allowed": None,
        },
        {
            "step_number": 3,
            "instruction": "Only if targeted extraction is needed after inspecting prior output, call filter_grep with the content and keywords (e.g. keywords=\"timeout | retry\"). Use pattern= only for raw regex.",
            "expected_outcome": "Structured matches with line numbers and section context.",
            "tools_allowed": None,
        },
    ],
    "priority": 70,
    "enabled": True,
}


# Require approval for run_command when using the skills / shell-tools demo preset with
# sandbox_mode = "local" only (native/opensandbox/e2b are not seeded with this policy here).
# Stable id for run_command approval in skills demo / local shell workflows (Manage or preset config).
DEMO_SKILLS_SHELL_TOOL_APPROVAL: dict[str, Any] = {
    "id": "tool_approval_run_command_local",
    "name": "Shell command approval (skills demo)",
    "description": (
        "Require approval before run_command when using Cuga Lite injected shell tools "
        "(run_command is not an MCP/OpenAPI tool; it is listed under app cuga_lite_shell in Manage)."
    ),
    "policy_type": "tool_approval",
    "type": "tool_approval",
    "enabled": True,
    "required_tools": ["run_command"],
    "approval_message": ("About to run a shell command in the agent workspace. Approve to continue?"),
    "show_code_preview": True,
    "priority": 10,
}


def get_default_apps_for_preset(preset: str) -> dict[str, bool]:
    """Return default app flags for a given preset (demo, demo_crm, demo_docs, demo_health, demo_knowledge, manager).
    Knowledge is disabled by default; only demo_knowledge hardcodes it to True."""
    knowledge = _knowledge_configured()
    if preset == "demo_crm":
        return {
            "crm": True,
            "email": True,
            "digital_sales": False,
            "docs": False,
            "filesystem": True,
            "oak_health": False,
            "knowledge": knowledge,
        }
    if preset == "demo_docs":
        return {
            "crm": False,
            "email": False,
            "digital_sales": False,
            "docs": True,
            "filesystem": False,
            "oak_health": False,
            "knowledge": knowledge,
        }
    if preset == "demo_health":
        return {
            "crm": False,
            "email": False,
            "digital_sales": False,
            "docs": False,
            "filesystem": False,
            "oak_health": True,
            "knowledge": knowledge,
        }
    if preset == "demo_knowledge":
        return {
            "crm": False,
            "email": False,
            "digital_sales": False,
            "docs": False,
            "filesystem": False,
            "oak_health": False,
            "knowledge": True,  # Always enabled for demo_knowledge
        }
    if preset == "demo":
        return {
            "crm": False,
            "email": False,
            "digital_sales": True,
            "docs": False,
            "filesystem": True,
            "oak_health": False,
            "knowledge": knowledge,
        }
    if preset == "demo_skills":
        return {
            "crm": False,
            "email": False,
            "digital_sales": True,
            "docs": False,
            "filesystem": False,
            "oak_health": False,
            "knowledge": knowledge,
        }
    return {
        "crm": False,
        "email": False,
        "digital_sales": False,
        "docs": False,
        "filesystem": True,
        "oak_health": False,
        "knowledge": knowledge,
    }


def setup_demo_manage_config(
    demo_type: str,
    agent_id: str = "cuga-default",
    no_email: bool = False,
    tools: list[dict[str, Any]] | None = None,
    reset_knowledge: bool = False,
) -> None:
    """
    Reset config db, then setup agent config (draft + v1) for demo or demo_crm.
    Uses same SSE links as cli for filesystem, email, crm.
    If tools is provided, uses it; otherwise builds from demo_type and no_email.
    When reset_knowledge is True, wipes all knowledge data (vector DB, metadata, files).
    """
    _normalize_demo_sandbox_mode()

    from cuga.backend.server.config_store import (
        reset_config_db,
        save_config,
        save_draft,
    )

    if demo_type == "demo_knowledge":
        os.environ["DYNACONF_KNOWLEDGE__ENABLED"] = "true"
        os.environ["DYNACONF_KNOWLEDGE__AGENT_LEVEL_ENABLED"] = "true"
        os.environ["DYNACONF_KNOWLEDGE__SESSION_LEVEL_ENABLED"] = "true"
        settings.reload()

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
        "What can you do?",
    ]
    DEMO_DOCS_STARTERS = [
        "What was the latest watsonx orchestrate release?",
        "How do I configure IBM MQ for persistent messaging?",
        "What are the system requirements for IBM Db2?",
        "Find the steps to deploy Kubernetes on IBM Cloud.",
        "Show me the OpenShift container platform installation guide.",
    ]
    DEMO_HEALTH_STARTERS = [
        "Show my last approved claims and share the URL of any EOB PDF",
        "Find in-network primary care doctors near me that accept new patients",
        "Find knee surgeons nearby and what are my benefits for surgery",
        "What is my deductible and out-of-pocket progress this plan year?",
        "Check the status of my referral and where it was sent",
    ]
    DEMO_SKILLS_STARTERS = [
        "List what agent skills are available and summarize what each is for",
        "Use the pptx skill to outline a short deck about our product",
        "What is in my workspace .cuga skills folder?",
    ]
    # Aligns with OOBE doc sovereign_core_overview.pdf (ingested on first demo_knowledge start).
    DEMO_KNOWLEDGE_STARTERS = [
        "What is Sovereign Core, and what problem does it solve?",
        "Summarize the main themes from the Sovereign Core overview in my knowledge base.",
        "What capabilities does the platform highlight on-premises use?",
    ]
    reset_config_db()

    # Only wipe knowledge data when explicitly requested (--reset flag).
    # This preserves uploaded documents across normal restarts.
    if reset_knowledge:
        try:
            from cuga.backend.knowledge.config import KnowledgeConfig as _KC
            from cuga.backend.knowledge.interprocess_lock import (
                acquire_exclusive_nonblocking as _lock_acquire,
                release_exclusive as _lock_release,
            )
            from cuga.config import settings as _settings
            import re as _re
            import shutil

            _kc = _KC.from_settings(_settings)
            _kc.persist_dir.mkdir(parents=True, exist_ok=True)

            # Check flock to avoid deleting files from under a running server.
            lock_path = _kc.persist_dir / ".lock"
            _lock_fd = open(lock_path, "w+b")
            try:
                _lock_acquire(_lock_fd)
            except OSError:
                _lock_fd.close()
                logger.error("Knowledge reset: another server is still running (holds .lock). Stop it first.")
                raise SystemExit(1)

            try:
                _pg = getattr(getattr(_settings, "storage", None), "postgres_url", "") or ""
                _storage_mode = (
                    getattr(getattr(_settings, "storage", None), "mode", None) or "local"
                ).lower()
                if _storage_mode == "prod" and _pg.strip():
                    from cuga.backend.knowledge.metadata import truncate_knowledge_metadata_tables

                    truncate_knowledge_metadata_tables(_pg.strip())
                    logger.info("Demo reset: truncated knowledge metadata tables in Postgres")

                _san = _re.sub(r"[^a-zA-Z0-9_]", "_", agent_id)
                prefix = f"kb_agent_{_san}"

                def _on_rmtree_error(func, path, exc_info):
                    logger.warning("Knowledge reset: failed to remove %s: %s", path, exc_info[1])

                files_dir = _kc.persist_dir / "files"
                if files_dir.exists():
                    for d in files_dir.iterdir():
                        if d.is_dir() and d.name.startswith(prefix):
                            shutil.rmtree(d, onerror=_on_rmtree_error)
                            logger.info("Knowledge reset: cleared %s", d.name)

                for db_file in ("knowledge.db", "metadata.db", "knowledge_vectors.db"):
                    for suffix in ("", "-wal", "-shm"):
                        p = _kc.persist_dir / (db_file + suffix)
                        if p.exists():
                            p.unlink()
                            logger.info("Knowledge reset: removed %s", p.name)

                session_state = _kc.persist_dir.parent / "session_knowledge.json"
                if session_state.exists():
                    session_state.unlink()
                    logger.info("Knowledge reset: removed session_knowledge.json")
            finally:
                _lock_release(_lock_fd)
                _lock_fd.close()

        except ImportError:
            logger.info("Knowledge reset: knowledge module not available, skipping")
        except SystemExit:
            raise
        except Exception as e:
            logger.warning("Knowledge reset: cleanup failed: %s", e)

    if tools is None:
        defaults = get_default_apps_for_preset(demo_type)
        if no_email:
            defaults["email"] = False
        tools = build_tools_from_apps(**defaults)
    else:
        # Auto-append knowledge tool if knowledge is enabled and not already present
        if _knowledge_configured() and not any(t.get("name") == "knowledge" for t in tools):
            tools.append(_get_knowledge_tool())
    use_crm_starters = demo_type == "demo_crm"
    use_docs_starters = demo_type == "demo_docs"
    use_health_starters = demo_type == "demo_health"
    use_skills_starters = demo_type == "demo_skills"
    use_knowledge = demo_type == "demo_knowledge"
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
    elif use_skills_starters:
        homescreen = {
            "isOn": True,
            "greeting": "Try agent skills (SKILL.md under .cuga) and OpenSandbox shell tools.",
            "starters": DEMO_SKILLS_STARTERS,
        }
    elif use_knowledge:
        homescreen = {
            "isOn": True,
            "greeting": (
                "Sovereign Core puts this agent and your data under your control. "
                "The overview (sovereign_core_overview.pdf) is in your knowledge base—ask anything about it."
            ),
            "starters": DEMO_KNOWLEDGE_STARTERS,
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
    # Include knowledge config so the server knows the intended state on restart.
    # The vector config hash ensures collection names are consistent across restarts.
    knowledge_cfg: dict[str, Any] = {}
    try:
        from cuga.backend.knowledge.config import KnowledgeConfig as _KC
        from cuga.config import settings as _settings

        _kc = _KC.from_settings(_settings)
        knowledge_cfg = _kc.to_dict()
        knowledge_cfg["_vector_config_hash"] = _kc.vector_config_hash()
    except Exception:
        pass
    if demo_type == "manager":
        agent_meta = {
            "name": "Default",
            "description": "Configurable agent for manage mode (filesystem and optional integrations)",
        }
    elif use_crm_starters:
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
    elif use_knowledge:
        agent_meta = {
            "name": "Knowledge Agent",
            "description": (
                "Document-grounded assistant; includes the Sovereign Core overview (sovereign_core_overview.pdf) "
                "for onboarding Q&A, plus your workspace files."
            ),
        }
    elif tools and any(t.get("name") == "digital_sales" for t in tools):
        agent_meta = {
            "name": "Digital Sales Agent",
            "description": "Agent with Digital Sales API and filesystem for sales workflows",
        }
    else:
        agent_meta = {
            "name": "Default",
            "description": "Agent with workspace filesystem and optional tools",
        }
    policies: list[dict[str, Any]] = []
    if tools and any(t.get("name") == "oak_health" for t in tools):
        policies.extend(load_oak_policy_entries())
    if tools and any(t.get("name") == "docs" for t in tools):
        policies.append(DOCS_PLAYBOOK)
        policies.append(DOCS_OUTPUT_FORMATTER)
    if demo_type == "demo_skills":
        _sandbox_mode = getattr(settings.advanced_features, "sandbox_mode", "opensandbox")
        if _sandbox_mode == "local":
            policies.append(DEMO_SKILLS_SHELL_TOOL_APPROVAL)
    policies.extend(load_cuga_policy_entries_for_demo())
    policies_struct: dict[str, Any] = {"enablePolicies": True, "policies": policies}
    config: dict[str, Any] = {
        "agent": agent_meta,
        "tools": tools,
        "policies": policies_struct,
        "homescreen": homescreen,
        "llm": llm_cfg,
        "knowledge": knowledge_cfg,
    }
    if tools and (any(t.get("name") == "docs" for t in tools) or demo_type in ("demo", "demo_skills")):
        config["feature_flags"] = config.get("feature_flags") or {}
        config["feature_flags"]["enable_todos"] = True

    if demo_type == "demo_skills":
        config.setdefault("advanced_features", {})["enable_shell_tool"] = True

    async def _setup():
        await save_draft(config, agent_id)
        await save_config(config, agent_id)

    asyncio.run(_setup())


# Packaged with cuga at src/cuga/demo_tools/huggingface/; copied to workspace by prepare_workspace.
DEMO_KNOWLEDGE_OOBE_PDF_NAME = "sovereign_core_overview.pdf"


def _resolve_oobe_knowledge_pdf_path() -> Path | None:
    """Package data path first; then cuga_workspace/ (e.g. Docker OOTB copy)."""
    from cuga.config import DEMO_TOOLS_ROOT

    primary = DEMO_TOOLS_ROOT / "huggingface" / DEMO_KNOWLEDGE_OOBE_PDF_NAME
    if primary.is_file():
        return primary
    fallback = Path.cwd() / "cuga_workspace" / DEMO_KNOWLEDGE_OOBE_PDF_NAME
    if fallback.is_file():
        return fallback
    return None


async def seed_demo_knowledge_oobe_pdf_via_engine_if_needed(app_state: Any) -> None:
    """Ingest packaged OOBE PDF into agent KB in-process.

    Intended to run immediately after KnowledgeEngine.warmup() succeeds on the demo server
    (`mcp_transport == http`). Requires CUGA_DEMO_MODE knowledge / demo_knowledge.
    Caller must invoke only while knowledge subsystem is ``ready``.
    """
    from cuga.backend.knowledge.auth import resolve_agent_collection
    from cuga.backend.knowledge.engine import DocumentExistsError

    mode = os.environ.get("CUGA_DEMO_MODE", "").strip().lower()
    loguru_logger.info("OOBE PDF seed check started (mode={!r})", mode)
    if mode not in ("knowledge", "demo_knowledge"):
        loguru_logger.info("OOBE PDF seed skipped: unsupported CUGA_DEMO_MODE={!r}", mode)
        return

    st = app_state.get_subsystem_status("knowledge")
    if st["state"] != "ready":
        if st["state"] == "failed":
            loguru_logger.warning("Knowledge subsystem failed; skip OOBE PDF seed")
        else:
            loguru_logger.warning(
                "OOBE PDF seed skipped: knowledge subsystem not ready yet (state={})",
                st["state"],
            )
        return

    pdf_path = _resolve_oobe_knowledge_pdf_path()
    if pdf_path is None:
        from cuga.config import DEMO_TOOLS_ROOT

        loguru_logger.warning(
            "OOBE knowledge PDF not found (tried {} and cuga_workspace/); skipping seed",
            DEMO_TOOLS_ROOT / "huggingface" / DEMO_KNOWLEDGE_OOBE_PDF_NAME,
        )
        return
    loguru_logger.info("OOBE knowledge PDF resolved to {}", pdf_path)

    engine = getattr(app_state, "knowledge_engine", None)
    if engine is None:
        loguru_logger.warning("Knowledge engine missing; skip OOBE PDF seed")
        return

    if not getattr(app_state, "knowledge_config_hash", None):
        cfg = getattr(engine, "_config", None)
        if cfg is not None:
            app_state.knowledge_config_hash = cfg.vector_config_hash()

    agent_id = os.environ.get(
        "CUGA_AGENT_ID", getattr(app_state, "agent_id", "cuga-default") or "cuga-default"
    )
    collection = resolve_agent_collection(agent_id, app_state)
    loguru_logger.info(
        "OOBE PDF seed targeting collection {} for agent_id={}",
        collection,
        agent_id,
    )

    try:
        docs = await engine.list_documents(collection)
        if any(d.filename == DEMO_KNOWLEDGE_OOBE_PDF_NAME for d in docs):
            loguru_logger.info("OOBE knowledge PDF already indexed; skip ingest")
            return
        await engine.ingest(collection, pdf_path, True, DEMO_KNOWLEDGE_OOBE_PDF_NAME)
        loguru_logger.info("Ingested OOBE knowledge PDF {}", DEMO_KNOWLEDGE_OOBE_PDF_NAME)
    except DocumentExistsError:
        loguru_logger.info("OOBE knowledge PDF already present; skip ingest")
    except Exception as e:
        loguru_logger.warning("OOBE PDF seed failed: {}", e)
