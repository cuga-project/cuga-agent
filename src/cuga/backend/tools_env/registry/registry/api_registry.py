import asyncio
import json
import os
import traceback as tb
from typing import Dict, List, Any
import httpx
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

    def __init__(self, client: MCPManager, policy_storage=None):
        logger.info("ApiRegistry: Initializing.")
        self.mcp_client = client
        self.auth_manager = None
        self.tavily_client = None
        self._init_tavily_if_enabled()
        
        # ToolGuardRuntime support for policy-based tool validation
        self.policy_storage = policy_storage
        self.tool_guard_runtime = None
        self._tool_guard_initialized = False

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
        
        # Initialize ToolGuardRuntime after tools are loaded
        await self._initialize_tool_guard_runtime()
    
    async def _initialize_tool_guard_runtime(self):
        """
        Initialize ToolGuardRuntime after tools are loaded.
        
        This is called after load_tools() to ensure both tools and policies are available.
        """
        if self._tool_guard_initialized:
            return
        
        self._tool_guard_initialized = True
        
        if self.policy_storage is None:
            logger.debug("ToolGuardRuntime: No policy storage provided, skipping initialization")
            return
        
        try:
            from cuga.backend.cuga_graph.policy.tool_guard.tool_guard_runtime import ToolGuardRuntime
            
            # Note: tool_provider is only needed if guards invoke tools via the invoker
            # For most guards that just validate arguments, it's not used
            # We pass self.mcp_client which can be wrapped if needed
            self.tool_guard_runtime = ToolGuardRuntime(
                tool_provider=self.mcp_client,
                policy_storage=self.policy_storage
            )
            
            await self.tool_guard_runtime.initialize()
            
            guarded_tools = self.tool_guard_runtime.get_guarded_tools()
            logger.info(
                f"✅ ToolGuardRuntime initialized with guards for {len(guarded_tools)} tools"
            )
            if guarded_tools:
                logger.debug(f"   Guarded tools: {', '.join(guarded_tools)}")
            
        except Exception as e:
            logger.warning(f"Failed to initialize ToolGuardRuntime: {e}")
            logger.debug("ToolGuardRuntime initialization error details:", exc_info=True)
            self.tool_guard_runtime = None

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

    async def get_service_statuses(self) -> Dict[str, Dict[str, Any]]:
        return self.mcp_client.get_service_statuses()

    async def show_apis_for_app(self, app_name: str, include_response_schema: bool = False) -> List[Dict]:
        """Lists API definitions of a specific app."""
        logger.debug(f"ApiRegistry: show_apis_for_app(app_name='{app_name}') called.")

        if app_name == "web" and self._is_web_search_enabled():
            return self._get_web_search_api_definition(include_response_schema)

        try:
            return self.mcp_client.get_apis_for_application(app_name, include_response_schema)
        except KeyError:
            logger.error(
                f"Application '{app_name}' not found in registry. Available apps: {self.mcp_client.get_app_names()}"
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
        
        # Normalize argument shape so guards and the tool see the same payload
        unwrapped_args = arguments.get('params', arguments) if isinstance(arguments, dict) and 'params' in arguments else arguments
        
        # Validate tool call against ToolGuard policies
        if self.tool_guard_runtime and self.tool_guard_runtime.is_initialized:
            try:
                error_message = await self.tool_guard_runtime.guard_tool_call(
                    app_name=app_name,
                    function_name=function_name,
                    arguments=unwrapped_args if isinstance(unwrapped_args, dict) else {}
                )
                
                if error_message:
                    # Guard validation failed - return error without executing tool
                    logger.warning(
                        f"🛡️ Tool guard blocked call to '{function_name}': {error_message}"
                    )
                    return {
                        "status": "exception",
                        "status_code": 403,
                        "message": f"Tool guard policy violation: {error_message}",
                        "error_type": "ToolGuardViolation",
                        "function_name": function_name,
                    }
                else:
                    logger.debug(f"✅ Tool guard validation passed for '{function_name}'")
            except Exception as e:
                # Fail-closed: treat exceptions from guard_tool_call as policy violations
                # to honor ToolGuardRuntime's fail-closed contract
                error_msg = f"Exception during tool guard execution for '{function_name}': {e}"
                logger.error(error_msg, exc_info=True)
                return {
                    "status": "exception",
                    "status_code": 403,
                    "message": f"Tool guard policy violation: {error_msg}",
                    "error_type": "ToolGuardViolation",
                    "function_name": function_name,
                }
        
        if app_name == "web" and function_name == "search_web" and self._is_web_search_enabled():
            query = unwrapped_args.get('query') if isinstance(unwrapped_args, dict) else str(unwrapped_args)
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
                logger.error(tb.format_exc())
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

                try:
                    access_token = self.auth_manager.get_access_token(app_name)
                    if access_token:
                        headers = {"Authorization": "Bearer " + access_token}
                    else:
                        logger.warning(
                            f"Could not get access token for {app_name}. "
                            f"This may be because the AppWorld supervisor is not initialized yet. "
                            f"Make sure you're using AppWorld context manager with a valid task_id."
                        )
                except Exception as e:
                    # Check if it's a TokenFetchError with detailed information
                    from cuga.backend.tools_env.registry.registry.authentication.appworld_auth_manager import (
                        TokenFetchError,
                    )

                    # Extract detailed error message from any exception type
                    detailed_message = str(e)

                    # Check for TokenFetchError with detailed information

                    if isinstance(e, TokenFetchError):
                        detailed_message = e.detailed_message or str(e)
                        logger.error(f"Token fetch error for {app_name}: {detailed_message}")
                        logger.error(f"  Status Code: {e.status_code}")
                        logger.error(f"  URL: {e.url}")
                        logger.error(f"  Response Body: {e.response_body}")

                        # Return error dict with detailed message that will propagate to user
                        return {
                            "status": "exception",
                            "status_code": e.status_code or 401,
                            "message": detailed_message,
                            "error_type": type(e).__name__,
                            "function_name": function_name,
                            "error_detail": {
                                "status_code": e.status_code,
                                "url": e.url,
                                "response_body": e.response_body,
                            },
                        }
                    elif isinstance(e, ValueError):
                        logger.error(f"Authentication error for {app_name}: {e}")
                        logger.warning(
                            f"API call to {app_name} may fail with 401 Unauthorized. "
                            f"Ensure AppWorld supervisor is initialized before making authenticated calls."
                        )
                        # Use the exception message directly
                        detailed_message = str(e)
                    else:
                        logger.error(f"Unexpected authentication error for {app_name}: {e}")
                        import traceback

                        logger.error(traceback.format_exc())
                        # Use the exception message directly
                        detailed_message = str(e)

                    # Return error dict with detailed message
                    return {
                        "status": "exception",
                        "status_code": 401,
                        "message": detailed_message,
                        "error_type": type(e).__name__,
                        "function_name": function_name,
                    }
            elif auth_config.value:
                headers = {f"{auth_config.type}": f"{auth_config.value}"}

        logger.debug(
            f"ApiRegistry: call_function(function_name='{function_name}', arguments={arguments}, headers={headers}) called."
        )
        try:
            # Delegate the call to the client (use already-unwrapped args)
            if self.auth_manager:
                headers["_tokens"] = json.dumps(self.auth_manager.get_stored_tokens())
            result = await self.mcp_client.call_tool(
                tool_name=function_name,
                args=unwrapped_args,
                headers=headers,
            )
            logger.debug("Response:", result)

            # Check if this is an /auth/token endpoint call and update stored token
            # Only do this when benchmark is "appworld"
            if settings.advanced_features.benchmark == "appworld":
                # Get API info to check the path
                try:
                    apis = await self.show_apis_for_app(app_name)
                    api_info = apis.get(function_name, {})
                    api_path = api_info.get("path", "")
                    is_auth_token_endpoint = api_path.endswith("/auth/token") or "/auth/token" in api_path

                    if is_auth_token_endpoint and result:
                        # Successful token fetch - extract and store the token
                        try:
                            # Result is TextContent list, extract the text
                            if isinstance(result, list) and len(result) > 0:
                                result_text = result[0].text if hasattr(result[0], 'text') else str(result[0])
                                try:
                                    result_json = (
                                        json.loads(result_text)
                                        if isinstance(result_text, str)
                                        else result_text
                                    )
                                    if isinstance(result_json, dict) and "access_token" in result_json:
                                        token = result_json["access_token"]
                                        # Update the auth manager's stored token
                                        if self.auth_manager:
                                            self.auth_manager._tokens[app_name] = token
                                            logger.info(
                                                f"✅ Updated stored token for {app_name} from /auth/token endpoint"
                                            )
                                        else:
                                            logger.debug(
                                                f"Auth manager not available to store token for {app_name}"
                                            )
                                    else:
                                        logger.debug(
                                            f"Token response for {app_name} does not contain 'access_token': {result_json}"
                                        )
                                except (json.JSONDecodeError, TypeError) as e:
                                    logger.debug(
                                        f"Could not parse token response as JSON: {result_text}, error: {e}"
                                    )
                        except Exception as e:
                            logger.warning(
                                f"Failed to extract and store token from /auth/token response: {e}"
                            )
                except Exception as e:
                    # If we can't get API info, that's okay - just log and continue
                    logger.debug(f"Could not check if endpoint is /auth/token: {e}")

            return result
        except httpx.HTTPStatusError as e:
            error_detail = {
                "status_code": e.response.status_code,
                "url": str(e.request.url) if e.request else "unknown",
                "method": e.request.method if e.request else "unknown",
            }
            try:
                error_detail["response_body"] = e.response.json()
            except Exception:
                error_detail["response_body"] = e.response.text
            try:
                error_detail["request_body"] = (
                    e.request.content.decode() if e.request and e.request.content else None
                )
            except Exception:
                error_detail["request_body"] = None
            error_detail["headers"] = dict(e.request.headers) if e.request else {}

            logger.error(f"HTTP error calling MCP function '{function_name}': {error_detail}")
            response_body_str = (
                json.dumps(error_detail['response_body'], indent=2)
                if isinstance(error_detail['response_body'], dict)
                else str(error_detail['response_body'])
            )
            logger.error(f"HTTP error calling MCP function '{function_name}':")
            logger.error(f"  Status Code: {error_detail['status_code']}")
            logger.error(f"  URL: {error_detail['url']}")
            logger.error(f"  Method: {error_detail['method']}")
            logger.error(f"  Response Body:\n{response_body_str}")
            if error_detail['request_body']:
                logger.error(f"  Request Body: {error_detail['request_body']}")
            logger.error(f"  Headers: {error_detail['headers']}")
            print(f"\n{'=' * 60}")
            print(f"HTTP ERROR: Calling MCP function '{function_name}' failed")
            print(f"{'=' * 60}")
            print(f"Status Code: {error_detail['status_code']}")
            print(f"URL: {error_detail['url']}")
            print(f"Method: {error_detail['method']}")
            print("Response Body:")
            print(response_body_str)
            if error_detail['request_body']:
                print(f"Request Body: {error_detail['request_body']}")
            print(f"Headers: {json.dumps(error_detail['headers'], indent=2)}")
            print(f"{'=' * 60}\n")

            # Extract detailed error message from response body
            detailed_message = None
            response_body = error_detail.get('response_body')
            if response_body:
                if isinstance(response_body, dict):
                    # Prioritize "message" field, then "detail", then format the whole dict
                    if "message" in response_body:
                        detailed_message = response_body["message"]
                    elif "detail" in response_body:
                        detailed_message = response_body["detail"]
                    else:
                        detailed_message = json.dumps(response_body, indent=2)
                elif isinstance(response_body, str):
                    detailed_message = response_body

            # Use detailed message if available, otherwise fall back to generic message
            if detailed_message:
                final_message = detailed_message
            else:
                final_message = (
                    f"HTTP {error_detail['status_code']} error executing function '{function_name}'"
                )

            return {
                "status": "exception",
                "status_code": error_detail['status_code'],
                "message": final_message,
                "error_type": type(e).__name__,
                "function_name": function_name,
                "error_detail": error_detail,
            }
        except Exception as e:
            logger.error(tb.format_exc())
            logger.error(f"Error calling MCP function '{function_name}': {e}")
            print(f"\n{'=' * 60}")
            print(f"ERROR: Calling MCP function '{function_name}' failed")
            print(f"{'=' * 60}")
            print(f"Error Type: {type(e).__name__}")
            print(f"Error Message: {str(e)}")
            print(f"{'=' * 60}\n")
            tb.print_exc()

            # Extract detailed message from exception if it has one
            detailed_message = str(e)

            # Check if exception has attributes that might contain detailed error info
            if hasattr(e, 'detailed_message'):
                detailed_message = e.detailed_message or detailed_message
            elif hasattr(e, 'response_body') and isinstance(e.response_body, dict):
                if "message" in e.response_body:
                    detailed_message = e.response_body["message"]
                elif "detail" in e.response_body:
                    detailed_message = e.response_body["detail"]

            return {
                "status": "exception",
                "status_code": 500,
                "message": detailed_message,
                "error_type": type(e).__name__,
                "function_name": function_name,
            }
