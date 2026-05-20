"""
Tool Call Tracker

Tracks tool/API calls during agent execution for observability.
Uses contextvars for thread-safe tracking across async execution.

For custom tool providers, use the `tracked_tool` decorator:

    from cuga.backend.cuga_graph.nodes.cuga_lite.tool_call_tracker import tracked_tool

    @tracked_tool(operation_id="getUsers", app_name="my_api")
    async def get_users(limit: int = 10) -> list:
        return await fetch_users(limit)
"""

import contextvars
import functools
import time
from datetime import datetime
from typing import List, Dict, Any, Optional, Callable, TypeVar
from loguru import logger


_tool_calls_context: contextvars.ContextVar[List[Dict[str, Any]]] = contextvars.ContextVar(
    'tool_calls', default=None
)

_tracking_enabled_context: contextvars.ContextVar[bool] = contextvars.ContextVar(
    'tracking_enabled', default=False
)

F = TypeVar('F', bound=Callable[..., Any])


class ToolCallTracker:
    """Context manager for tracking tool calls during execution."""

    @staticmethod
    def is_enabled() -> bool:
        """Check if tool call tracking is enabled for this execution context."""
        return _tracking_enabled_context.get()

    @staticmethod
    def start_tracking(enabled: bool = True) -> None:
        """Start a new tracking session.

        Args:
            enabled: Whether tracking should be enabled for this session
        """
        _tracking_enabled_context.set(enabled)
        if enabled:
            _tool_calls_context.set([])
            logger.debug("Tool call tracking started")

    @staticmethod
    def stop_tracking() -> List[Dict[str, Any]]:
        """Stop tracking and return collected tool calls."""
        if not ToolCallTracker.is_enabled():
            return []

        calls = _tool_calls_context.get()
        _tool_calls_context.set(None)
        _tracking_enabled_context.set(False)
        logger.debug(f"Tool call tracking stopped, collected {len(calls) if calls else 0} calls")
        return calls or []

    @staticmethod
    def record_call(
        tool_name: str,
        arguments: Dict[str, Any],
        result: Any = None,
        app_name: Optional[str] = None,
        operation_id: Optional[str] = None,
        duration_ms: Optional[float] = None,
        error: Optional[str] = None,
    ) -> None:
        """Record a tool call.

        Args:
            tool_name: Name of the tool as used by the agent
            arguments: Arguments passed to the tool
            result: Result returned by the tool
            app_name: Name of the app/server
            operation_id: Original OpenAPI operationId (if available)
            duration_ms: Duration of the call in milliseconds
            error: Error message if the call failed
        """
        if not ToolCallTracker.is_enabled():
            return

        calls = _tool_calls_context.get()
        if calls is None:
            return

        record = {
            "name": tool_name,
            "arguments": arguments,
            "result": result,
            "app_name": app_name,
            "operation_id": operation_id,
            "timestamp": datetime.now().isoformat(),
            "duration_ms": duration_ms,
            "error": error,
        }

        calls.append(record)

    @staticmethod
    def get_current_calls() -> List[Dict[str, Any]]:
        """Get the current list of tracked calls without stopping tracking."""
        if not ToolCallTracker.is_enabled():
            return []
        return _tool_calls_context.get() or []


def tracked_tool(
    _func: Optional[F] = None,
    *,
    app_name: Optional[str] = None,
) -> Callable[[F], F]:
    """
    Decorator to automatically track tool calls in custom tool providers.

    Use this decorator on tool functions to enable tracking when
    `track_tool_calls=True` is passed to `agent.invoke()`.

    Args:
        app_name: Optional name of the app/service this tool belongs to

    Example:
        ```python
        from cuga import tracked_tool

        # Simple usage - just add the decorator
        @tracked_tool
        def multiply(a: int, b: int) -> int:
            return a * b

        # With app_name for grouping
        @tracked_tool(app_name="calculator")
        def add(a: int, b: int) -> int:
            return a + b

        # Works with async functions too
        @tracked_tool(app_name="user_service")
        async def get_user(user_id: int) -> dict:
            return {"id": user_id, "name": "John"}

        # Can combine with LangChain @tool decorator
        from langchain_core.tools import tool

        @tool
        @tracked_tool(app_name="math")
        def divide(a: int, b: int) -> float:
            '''Divide two numbers'''
            return a / b
        ```

    The decorator automatically captures:
    - Tool name (from function name, used as operation_id)
    - Arguments passed to the tool
    - Result or error
    - Duration in milliseconds
    - Timestamp
    """

    def decorator(func: F) -> F:
        func_name = func.__name__

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            start_time = time.time()
            result = None
            error_msg = None

            try:
                result = await func(*args, **kwargs)
                return result
            except Exception as e:
                error_msg = str(e)
                raise
            finally:
                duration_ms = (time.time() - start_time) * 1000
                ToolCallTracker.record_call(
                    tool_name=func_name,
                    arguments=kwargs if kwargs else dict(zip(func.__code__.co_varnames, args)),
                    result=result,
                    app_name=app_name,
                    operation_id=func_name,
                    duration_ms=duration_ms,
                    error=error_msg,
                )

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            start_time = time.time()
            result = None
            error_msg = None

            try:
                result = func(*args, **kwargs)
                return result
            except Exception as e:
                error_msg = str(e)
                raise
            finally:
                duration_ms = (time.time() - start_time) * 1000
                ToolCallTracker.record_call(
                    tool_name=func_name,
                    arguments=kwargs if kwargs else dict(zip(func.__code__.co_varnames, args)),
                    result=result,
                    app_name=app_name,
                    operation_id=func_name,
                    duration_ms=duration_ms,
                    error=error_msg,
                )

        import asyncio

        if asyncio.iscoroutinefunction(func):
            return async_wrapper  # type: ignore
        return sync_wrapper  # type: ignore

    # Support both @tracked_tool and @tracked_tool() syntax
    if _func is not None:
        return decorator(_func)
    return decorator
