"""Tool Provider Interface

Defines the interface for providing tools to CugaAgent from different sources.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional
from langchain_core.tools import StructuredTool
from pydantic import BaseModel


class AppDefinition(BaseModel):
    """Definition of an application/service that provides tools."""

    name: str
    url: Optional[str] = None
    description: Optional[str] = None
    type: str = "api"


class ToolProviderInterface(ABC):
    """Abstract interface for tool providers.

    Implementations provide tools from different sources:
    - ToolRegistryProvider: Tools from the MCP registry (separate process)
    - DirectLangChainToolsProvider: LangChain tools passed directly at runtime
    """

    @abstractmethod
    async def get_apps(self) -> List[AppDefinition]:
        """Get list of available applications/services.

        Returns:
            List of AppDefinition objects with app metadata
        """
        pass

    @abstractmethod
    async def get_tools(self, app_name: str) -> List[StructuredTool]:
        """Get tools for a specific application.

        Args:
            app_name: Name of the application

        Returns:
            List of LangChain StructuredTool objects
        """
        pass

    @abstractmethod
    async def get_all_tools(self) -> List[StructuredTool]:
        """Get all available tools from all applications.

        Returns:
            List of all LangChain StructuredTool objects
        """
        pass

    @abstractmethod
    async def initialize(self):
        """Initialize the tool provider (e.g., connect to registry, validate tools)."""
        pass
