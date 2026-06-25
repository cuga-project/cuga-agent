from abc import ABC, abstractmethod
from typing import Any, Optional
from cuga.backend.cuga_graph.state.agent_state import AgentState


class BaseExecutor(ABC):
    """Base interface for code executors."""

    @abstractmethod
    async def execute(
        self,
        wrapped_code: str,
        context_locals: dict[str, Any],
        timeout: int = 30,
    ) -> str:
        """Execute wrapped code and return stdout result.

        Args:
            wrapped_code: Wrapped Python code to execute
            context_locals: Dictionary of variables and tools
            timeout: Execution timeout in seconds

        Returns:
            Execution result string (stdout)

        Raises:
            Exception: For any execution errors
        """
        pass

    @abstractmethod
    def format_error(self, error: Exception) -> str:
        """Format an error for display.

        Args:
            error: The exception to format

        Returns:
            Formatted error string
        """
        pass


class RemoteExecutor(ABC):
    """Base interface for remote code executors (E2B, Docker, etc)."""

    @abstractmethod
    async def execute_for_cuga_lite(
        self,
        wrapped_code: str,
        context_locals: dict[str, Any],
        state: AgentState,
        thread_id: Optional[str] = None,
        apps_list: Optional[list[str]] = None,
    ) -> tuple[str, dict[str, Any]]:
        """Execute code for cuga_lite mode (captures all variables).

        Args:
            wrapped_code: Wrapped Python code to execute
            context_locals: Dictionary of variables and tools
            state: AgentState instance
            thread_id: Thread ID for sandbox caching
            apps_list: List of app names for parsing tool names

        Returns:
            Tuple of (execution result, new variables dictionary)
        """
        pass

    @abstractmethod
    async def execute_for_code_agent(
        self,
        wrapped_code: str,
        state: AgentState,
        thread_id: Optional[str] = None,
    ) -> str:
        """Execute code for CodeAgent mode (expects JSON on last line).

        Args:
            wrapped_code: Wrapped Python code to execute
            state: AgentState instance
            thread_id: Thread ID for sandbox caching

        Returns:
            Execution result string (stdout)
        """
        pass
