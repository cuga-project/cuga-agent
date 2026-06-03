"""Helper functions for natural language tool shortlisting (find_tools)."""

from __future__ import annotations

import json as _json
from pathlib import Path
from typing import Any, Dict, List, Optional

from langchain_core.exceptions import OutputParserException
from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.tools import StructuredTool
from loguru import logger

from cuga.config import settings
from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import PromptUtils

_BUNDLED_FIND_TOOLS_FEW_SHOT_JSON = (
    Path(__file__).resolve().parent.parent / "prompts" / "find_tools_few_shot_examples.json"
)


def _first_user_message_text(chat_messages: Optional[List[BaseMessage]]) -> Optional[str]:
    if not chat_messages:
        return None
    for msg in chat_messages:
        if isinstance(msg, HumanMessage):
            raw = msg.content
            text = raw.strip() if isinstance(raw, str) else str(raw).strip()
            return text or None
    return None


def _compose_find_tools_shortlister_query(query: str, initial_user_message: Optional[str]) -> str:
    q = query.strip()
    init = (initial_user_message or "").strip()
    if not init:
        return q
    return f"Query: {q}\nTask context (initial user message): {init}"


def _web_search_enabled() -> bool:
    return bool(getattr(settings.advanced_features, "enable_web_search", False))


def _ensure_web_app(apps: List[Any], all_apps: List[Any]) -> List[Any]:
    if not _web_search_enabled() or any(getattr(app, "name", None) == "web" for app in apps):
        return apps
    web_app = next((app for app in all_apps if getattr(app, "name", None) == "web"), None)
    if web_app:
        return [*apps, web_app]
    return apps


async def create_find_tools_tool(
    all_tools,
    all_apps: List[Any],
    app_to_tools_map: Optional[Dict[str, List[StructuredTool]]] = None,
    llm: Optional[Any] = None,
    initial_user_message: Optional[str] = None,
) -> StructuredTool:
    """Create a find_tools StructuredTool for tool discovery.

    Args:
        all_tools: All available tools to search through
        all_apps: All available app definitions
        app_to_tools_map: Optional mapping of app_name -> list of tools. If provided, used for filtering by app_name.
        initial_user_message: First human message in the session; combined with the tool `query` for shortlisting.

    Returns:
        StructuredTool configured for finding relevant tools
    """

    async def find_tools_func(query: str, app_name: str):
        """Search for relevant tools from the connected applications based on a natural language query.

        Args:
            query: Natural language query describing what tools are needed to accomplish the task can include also which parameters are needed or the output expected
            app_name: Name of a specific app to filter tools from. Only searches tools from that app.

        Returns:
            Top 4 matching tools with their details
        """
        if app_to_tools_map and app_name in app_to_tools_map:
            filtered_tools = app_to_tools_map[app_name]
        elif app_to_tools_map is None:
            filtered_tools = all_tools
        else:
            logger.warning(
                f"App '{app_name}' not found in app_to_tools_map. Available apps: {list(app_to_tools_map.keys()) if app_to_tools_map else 'N/A'}"
            )
            filtered_tools = []

        filtered_apps = [app for app in all_apps if hasattr(app, 'name') and app.name == app_name]

        if not filtered_apps:
            logger.warning(
                f"App '{app_name}' not found in available apps. Available apps: {[app.name if hasattr(app, 'name') else str(app) for app in all_apps]}"
            )

        shortlister_query = _compose_find_tools_shortlister_query(query, initial_user_message)

        from cuga.backend.cuga_graph.utils.langfuse_tracing import nested_langgraph_invoke_config

        try:
            return await PromptUtils.find_tools(
                query=shortlister_query,
                all_tools=filtered_tools,
                all_apps=filtered_apps,
                llm=llm,
                run_config=nested_langgraph_invoke_config(),
            )
        except OutputParserException as e:
            logger.bind(
                query_len=len(shortlister_query),
                error_type=type(e).__name__,
            ).opt(exception=True).warning(
                "Tool shortlisting failed due to parser error; returning error to agent"
            )
            return (
                f"Tool shortlisting failed due to malformed response: {e}. "
                "Please retry with a different query."
            )
        except Exception as e:
            logger.bind(
                query_len=len(shortlister_query),
                error_type=type(e).__name__,
            ).opt(exception=True).warning("Tool shortlisting failed unexpectedly; returning error to agent")
            return (
                f"Tool shortlisting failed due to an internal error: {e}. "
                "Please retry with a different query."
            )

    return StructuredTool.from_function(
        func=find_tools_func,
        name="find_tools",
        description="Search for relevant tools from a specific connected application based on a natural language query. Use this when you need to discover what tools are available for a specific task within a specific application.",
    )


def _resolve_find_tools_few_shot_json_path() -> Optional[Path]:
    if _BUNDLED_FIND_TOOLS_FEW_SHOT_JSON.is_file():
        return _BUNDLED_FIND_TOOLS_FEW_SHOT_JSON
    return None


def _load_default_find_tools_few_shot_examples() -> List[Dict[str, str]]:
    from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import normalize_mcp_few_shot_examples

    path = _resolve_find_tools_few_shot_json_path()
    if path is None:
        logger.debug(
            "Find-tools few-shot JSON not found (expected packaged %s or repo samples copy); skipping",
            _BUNDLED_FIND_TOOLS_FEW_SHOT_JSON,
        )
        return []
    try:
        raw = _json.loads(path.read_text(encoding="utf-8"))
        normalized = normalize_mcp_few_shot_examples(raw)
        if normalized:
            logger.info(f"Loaded {len(normalized)} find_tools MCP few-shot turn(s) from {path}")
        return normalized
    except (OSError, _json.JSONDecodeError) as e:
        logger.warning(f"Could not load find_tools few-shot JSON from {path}: {e}")
        return []
