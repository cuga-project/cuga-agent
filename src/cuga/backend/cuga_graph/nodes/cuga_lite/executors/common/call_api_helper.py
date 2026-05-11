from typing import Callable
import asyncio
import time
from loguru import logger
from cuga.config import settings


class CallApiHelper:
    """Unified utilities for creating call_api helper functions for both local and remote execution."""

    @staticmethod
    def get_function_call_url() -> str:
        """Get the function call URL for remote execution (E2B, Docker, etc)."""
        function_call_url = getattr(settings.server_ports, 'function_call_host', None)
        if not function_call_url:
            function_call_url = getattr(settings.server_ports, 'registry_host', None)
        if not function_call_url:
            logger.warning(
                "No function_call_host or registry_host configured. "
                "Remote execution may fail. Using localhost fallback."
            )
            # TODO: validate if hardcoded port is correct and maybe take it from settings.toml
            function_call_url = "http://localhost:8001"
        return function_call_url

    @staticmethod
    def get_trajectory_path() -> str:
        """Get URL-encoded trajectory path from ActivityTracker."""
        from cuga.backend.activity_tracker.tracker import ActivityTracker
        from urllib.parse import quote

        tracker = ActivityTracker()
        return quote(tracker.get_current_trajectory_path())

    @staticmethod
    def create_local_call_api_function() -> Callable:
        """Create call_api function for LOCAL execution.

        For local execution, we need to:
        1. First try ActivityTracker (for runtime tools)
        2. Fallback to registry API via HTTP

        Returns:
            Async function that can call tools via tracker or registry
        """
        import json
        import aiohttp
        from cuga.backend.tools_env.registry.utils.api_utils import get_registry_base_url
        from cuga.backend.activity_tracker.tracker import ActivityTracker
        from cuga.backend.cuga_graph.nodes.cuga_lite.tool_call_tracker import ToolCallTracker

        tracker = ActivityTracker()

        async def call_api(app_name: str, api_name: str, args: dict = None, operation_id: str = None):
            """Call API tool via tracker or registry.

            Args:
                app_name: Name of the app/server
                api_name: Name of the API/tool
                args: Arguments to pass to the API
                operation_id: Optional original OpenAPI operationId for tracking
            """
            if args is None:
                args = {}

            timeout_seconds = getattr(settings.advanced_features, 'tool_call_timeout', 30)
            start_time = time.time()
            result = None
            error_msg = None

            try:
                # First try tracker (for runtime tools)
                if tracker.tools and app_name in tracker.tools:
                    try:
                        result = await asyncio.wait_for(
                            tracker.invoke_tool(app_name, api_name, args), timeout=timeout_seconds
                        )
                    except asyncio.TimeoutError:
                        error_msg = f"Tool call '{api_name}' timed out after {timeout_seconds} seconds"
                        raise TimeoutError(error_msg)

                    if not isinstance(result, dict):
                        if hasattr(result, 'model_dump'):
                            result = result.model_dump()
                        elif hasattr(result, '__dict__'):
                            result = result.__dict__
                        else:
                            result = str(result)
                    return result

                # Fallback to registry API
                if settings.advanced_features.registry:
                    registry_base = get_registry_base_url()
                    url = f'{registry_base}/functions/call'

                    payload = {"app_name": app_name, "function_name": api_name, "args": args}

                    try:
                        async with aiohttp.ClientSession() as session:
                            async with session.post(
                                url,
                                json=payload,
                                headers={"accept": "application/json", "Content-Type": "application/json"},
                                timeout=aiohttp.ClientTimeout(total=timeout_seconds),
                            ) as response:
                                if response.status != 200:
                                    error_text = await response.text()
                                    error_msg = f"HTTP Error: {response.status} - {error_text}"
                                    raise Exception(error_msg)

                                response_data = await response.text()
                                try:
                                    result = json.loads(response_data)
                                except json.JSONDecodeError:
                                    result = response_data
                                return result
                    except asyncio.TimeoutError:
                        error_msg = f"Tool call '{api_name}' timed out after {timeout_seconds} seconds"
                        raise TimeoutError(error_msg)
                    except Exception as e:
                        error_msg = f"Error calling API {api_name}: {str(e)}"
                        raise Exception(error_msg)
                else:
                    error_msg = f"Server '{app_name}' not found in tracker and registry is disabled"
                    raise ValueError(error_msg)

            except TimeoutError:
                raise
            except Exception as e:
                logger.error(f"Error calling {app_name}.{api_name}: {e}")
                raise
            finally:
                duration_ms = (time.time() - start_time) * 1000
                ToolCallTracker.record_call(
                    tool_name=api_name,
                    arguments=args,
                    result=result,
                    app_name=app_name,
                    operation_id=operation_id,
                    duration_ms=duration_ms,
                    error=error_msg,
                )

        return call_api

    @staticmethod
    def create_remote_call_api_code(function_call_url: str = None, trajectory_path: str = "") -> str:
        """Create call_api helper function CODE for REMOTE execution (E2B, Docker, etc).

        For remote execution, we ONLY use registry API via HTTP (no tracker).
        This code will be injected into the remote sandbox.

        Args:
            function_call_url: Base URL for function calls (if None, uses get_function_call_url())
            trajectory_path: URL-encoded trajectory path for tracking

        Returns:
            Python code string defining async call_api function
        """
        if function_call_url is None:
            function_call_url = CallApiHelper.get_function_call_url()

        url_with_trajectory = f"{function_call_url}/functions/call"
        if trajectory_path:
            url_with_trajectory += f"?trajectory_path={trajectory_path}"

        timeout_seconds = getattr(settings.advanced_features, 'tool_call_timeout', 30)

        return f"""
import asyncio
import json
import socket
import urllib.error
import urllib.request

async def call_api(app_name, api_name, args=None):
    \"\"\"Call registry API tool via HTTP (remote execution only).\"\"\"
    if args is None:
        args = {{}}

    url = "{url_with_trajectory}"
    payload = json.dumps({{
        "function_name": api_name,
        "app_name": app_name,
        "args": args
    }}).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={{"Content-Type": "application/json", "accept": "application/json"}},
        method="POST",
    )
    try:
        loop = asyncio.get_event_loop()
        def _do_request():
            with urllib.request.urlopen(req, timeout={timeout_seconds}) as resp:
                return resp.read().decode()
        response_data = await loop.run_in_executor(None, _do_request)
        try:
            return json.loads(response_data)
        except json.JSONDecodeError:
            return response_data
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            err_body = ""
        raise Exception(
            f"HTTP {{e.code}} calling {{api_name}} (app={{app_name!r}}): {{err_body or e.reason}}"
        ) from e
    except socket.timeout:
        raise Exception(
            f"Tool call {{api_name!r}} (app={{app_name!r}}) timed out after {timeout_seconds} seconds"
        ) from None
    except urllib.error.URLError as e:
        if isinstance(e.reason, socket.timeout):
            raise Exception(
                f"Tool call {{api_name!r}} (app={{app_name!r}}) timed out after {timeout_seconds} seconds"
            ) from e
        raise Exception(
            f"URL error calling {{api_name}} (app={{app_name!r}}): {{e.reason!s}}"
        ) from e
    except Exception as e:
        raise Exception(f"Error calling API {{api_name}}: {{str(e)}}") from e
"""

    # Backwards compatibility aliases
    @staticmethod
    def create_call_api_code(function_call_url: str, trajectory_path: str = "") -> str:
        """Deprecated: Use create_remote_call_api_code instead."""
        return CallApiHelper.create_remote_call_api_code(function_call_url, trajectory_path)
