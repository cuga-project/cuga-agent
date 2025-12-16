import asyncio
import json
import os
import traceback
from typing import Dict, List, Any
from fastapi import HTTPException
from cuga.backend.tools_env.registry.mcp_manager.mcp_manager import MCPManager
from cuga.backend.tools_env.registry.registry.authentication.appworld_auth_manager import (
    AppWorldAuthManager,
)
from loguru import logger
from cuga.config import settings

from cuga.backend.tools_env.registry.utils.types import AppDefinition

try:
    from tavily import TavilyClient

    TAVILY_AVAILABLE = True
except ImportError:
    TAVILY_AVAILABLE = False
    TavilyClient = None


class ApiRegistry:
    """
    Internal class to manage API and Application information,
    interacting with the mcp manager
    """

    def __init__(self, client: MCPManager):
        logger.info("ApiRegistry: Initializing.")
        self.mcp_client = client
        self.auth_manager = None
        self.tavily_client = None
        self._init_tavily_if_enabled()

    def _init_tavily_if_enabled(self):
        """Initialize Tavily client if web search is enabled."""
        if self._is_web_search_enabled():
            api_key = os.getenv("TAVILY_API_KEY")
            if not api_key:
                logger.warning("TAVILY_API_KEY not found in environment. Web search will not work.")
                return
            if TAVILY_AVAILABLE:
                try:
                    self.tavily_client = TavilyClient(api_key)
                    logger.info("Tavily client initialized for web search.")
                except Exception as e:
                    logger.error(f"Failed to initialize Tavily client: {e}")
            else:
                logger.warning("tavily-python package not available. Install it to use web search.")

    def _is_web_search_enabled(self) -> bool:
        """Check if web search feature is enabled."""
        return getattr(settings.advanced_features, "enable_web_search", False)

    async def start_servers(self):
        """Start servers and load tools"""
        await self.mcp_client.load_tools()
        logger.info("ApiRegistry: Servers started successfully.")

    async def show_applications(self) -> List[AppDefinition]:
        """Lists application names and their descriptions."""
        logger.debug("ApiRegistry: show_applications() called.")
        apps = self.mcp_client.get_apps()
        app_list = [AppDefinition(name=p.name, url=p.url, description=p.description) for p in apps]

        if self._is_web_search_enabled():
            app_list.append(
                AppDefinition(name="web", url=None, description="Web search tool powered by Tavily")
            )

        return app_list

    async def show_apis_for_app(self, app_name: str, include_response_schema: bool = False) -> List[Dict]:
        """Lists API definitions of a specific app."""
        logger.debug(f"ApiRegistry: show_apis_for_app(app_name='{app_name}') called.")

        if app_name == "web" and self._is_web_search_enabled():
            return self._get_web_search_api_definition(include_response_schema)

        try:
            return self.mcp_client.get_apis_for_application(app_name, include_response_schema)
        except KeyError:
            logger.error(
                f"Application '{app_name}' not found in registry. Available apps: {[app.name for app in self.mcp_client.get_apps()]}"
            )
            raise HTTPException(status_code=404, detail=f"Application '{app_name}' not found in registry")
        except Exception as e:
            logger.error(f"Error getting APIs for app '{app_name}': {type(e).__name__}: {e}")
            raise

    async def show_all_apis(self, include_response_schema) -> List[Dict[str, str]]:
        """Gets all API definitions."""
        logger.debug("ApiRegistry: show_all_apis() called.")
        return self.mcp_client.get_all_apis(include_response_schema)

    async def auth_apps(self, apps: List[str]):
        """Gets all API definitions."""
        logger.debug("auth_apps: auth_apps called.")
        if not self.auth_manager:
            self.auth_manager = AppWorldAuthManager()
        for app in apps:
            self.auth_manager.get_access_token(app)

    def _get_web_search_api_definition(self, include_response_schema: bool = False) -> Dict[str, Dict]:
        """Get API definition for web search tool."""
        response_schema = {}
        if include_response_schema:
            response_schema = {
                "success": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "follow_up_questions": {"type": ["array", "null"], "items": {"type": "string"}},
                        "answer": {"type": ["string", "null"]},
                        "images": {"type": "array", "items": {"type": "string"}},
                        "results": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "url": {"type": "string"},
                                    "title": {"type": "string"},
                                    "content": {"type": "string"},
                                    "score": {"type": "number"},
                                    "raw_content": {"type": ["string", "null"]},
                                },
                            },
                        },
                        "response_time": {"type": "number"},
                        "request_id": {"type": "string"},
                    },
                },
                "failure": {"type": "object", "properties": {"error": {"type": "string"}}},
            }

        return {
            "search_web": {
                "app_name": "web",
                "secure": False,
                "api_name": "search_web",
                "path": "/search_web",
                "method": "POST",
                "description": "Search the web using Tavily API. Returns relevant search results with URLs, titles, content, and scores.",
                "parameters": [
                    {
                        "name": "query",
                        "type": "string",
                        "required": True,
                        "description": "The search query string",
                        "default": None,
                        "constraints": [],
                    }
                ],
                "response_schemas": response_schema,
            }
        }

    async def _call_web_search(self, query: str) -> Dict[str, Any]:
        """Call Tavily web search API."""
        if not self.tavily_client:
            raise Exception("Tavily client not initialized. Check TAVILY_API_KEY environment variable.")

        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(None, self.tavily_client.search, query)
            return response
        except Exception as e:
            logger.error(f"Error calling Tavily search: {e}")
            raise

    async def call_function(
        self, app_name: str, function_name: str, arguments: Dict[str, Any], auth_config=None
    ) -> Dict[str, Any]:
        """Calls a function via the mcp_client."""
        if app_name == "web" and function_name == "search_web" and self._is_web_search_enabled():
            args = arguments.get('params', arguments) if isinstance(arguments, dict) else arguments
            query = args.get('query') if isinstance(args, dict) else str(args)
            if not query:
                return {
                    "status": "exception",
                    "status_code": 400,
                    "message": "Missing required parameter 'query'",
                    "error_type": "ValueError",
                    "function_name": function_name,
                }
            try:
                result = await self._call_web_search(query)
                from mcp.types import TextContent

                return [TextContent(text=json.dumps(result), type='text')]
            except Exception as e:
                logger.error(traceback.format_exc())
                return {
                    "status": "exception",
                    "status_code": 500,
                    "message": f"Error executing web search: {str(e)}",
                    "error_type": type(e).__name__,
                    "function_name": function_name,
                }

        headers = {}
        logger.debug(auth_config)
        if auth_config:
            if auth_config.type == 'oauth2':
                if not self.auth_manager:
                    self.auth_manager = AppWorldAuthManager()

                access_token = self.auth_manager.get_access_token(app_name)
                if access_token:
                    headers = {"Authorization": "Bearer " + access_token}
            elif auth_config.value:
                headers = {f"{auth_config.type}": f"{auth_config.value}"}

        logger.debug(
            f"ApiRegistry: call_function(function_name='{function_name}', arguments={arguments}, headers={headers}) called."
        )
        try:
            # Delegate the call to the client
            args = arguments['params'] if 'params' in arguments else arguments
            if self.auth_manager:
                headers["_tokens"] = json.dumps(self.auth_manager.get_stored_tokens())
            result = await self.mcp_client.call_tool(
                tool_name=function_name,
                args=args,
                headers=headers,
            )
            logger.debug("Response:", result)
            return result
        except Exception as e:
            # In a real scenario, you might catch specific client exceptions
            logger.error(traceback.format_exc())

            logger.error(f"Error calling MCP function '{function_name}': {e}")

            # Return structured error response instead of raising HTTPException
            return {
                "status": "exception",
                "status_code": 500,
                "message": f"Error executing function '{function_name}': {str(e)}",
                "error_type": type(e).__name__,
                "function_name": function_name,
            }
