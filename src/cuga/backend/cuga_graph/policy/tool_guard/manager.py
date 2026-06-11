"""
Manager for generating tool guard examples using the toolguard library.

This module provides integration between CUGA's policy system and toolguard's
example generation capabilities to create violating and compliance examples
for tool usage policies.
"""

import asyncio
import re
import shutil
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple, get_args, get_origin

from langchain_core.tools import StructuredTool
from loguru import logger
from toolguard.buildtime.buildtime import (
    ToolGuardSpec,
    generate_guard_examples,
    generate_guards_code,
)
from toolguard.buildtime.llm import LangchainModelWrapper
from toolguard.extra.langchain_to_oas import langchain_tools_to_openapi
from toolguard.runtime.data_types import (
    ToolGuardsCodeGenerationResult,
    ToolGuardSpecItem,
)

from cuga.backend.cuga_graph.policy.models import PolicyType, ToolGuide
from cuga.sdk import CugaAgent


class ToolGuardManager:
    def __init__(
        self,
        agent: CugaAgent,
    ):
        self.langchain_tools: List[StructuredTool] = []  # Store LangChain tools
        self.tools_dict: Dict[str, Any] = {}  # Store OpenAPI dict for ToolGuard
        self._tool_app_names: Dict[str, List[str]] = {}
        self._initialized = False
        self._init_lock = asyncio.Lock()

        # Validate tool_provider upfront
        if agent.tool_provider is None:
            raise ValueError(
                "Agent tool_provider is not initialized. Ensure the CugaAgent has a valid "
                "tool_provider before creating ToolGuardManager."
            )

        # Validate cuga_folder upfront
        if not agent.cuga_folder:
            raise ValueError(
                "Agent cuga_folder is not set. Ensure the CugaAgent has a valid "
                "cuga_folder path before creating ToolGuardManager."
            )

        self.tool_provider = agent.tool_provider

        # Wrap CUGA's LLM with adapter for ToolGuard compatibility
        if agent._model is None:
            raise ValueError(
                "Agent model is not initialized. Ensure the CugaAgent has a valid model "
                "before creating ToolGuardManager."
            )

        self.llm = LangchainModelWrapper(agent._model)
        logger.info(
            f"Initialized ToolGuardManager with {type(agent._model).__name__} via LangchainModelWrapper"
        )
        # Use Path to properly handle path concatenation and ensure directory exists
        work_dir_path = Path(agent.cuga_folder) / "toolguard"
        work_dir_path.mkdir(parents=True, exist_ok=True)
        self.work_dir = str(work_dir_path)
        logger.debug(f"ToolGuard working directory: {self.work_dir}")

    async def initialize(
        self,
    ) -> None:
        async with self._init_lock:
            if self._initialized:
                logger.debug("ToolGuardManager already initialized, skipping")
                return

            logger.info("Initializing ToolGuardManager...")

            # Get raw LangChain tools from the provider when available. ToolGuard
            # generation needs the original function annotations/metadata; guarded
            # wrapper tools intentionally replace the callable and can lose return
            # annotations such as `-> str`.
            if hasattr(self.tool_provider, "get_all_raw_tools"):
                self.langchain_tools = await self.tool_provider.get_all_raw_tools()
            else:
                self.langchain_tools = await self.tool_provider.get_all_tools()

            self._tool_app_names = await self._build_tool_app_name_index()

            # Convert LangChain tools to OpenAPI dict using ToolGuard's utility.
            self.tools_dict = langchain_tools_to_openapi(self.langchain_tools)
            self._enrich_response_schemas_from_annotations()

            self._initialized = True
            logger.info(f"✅ ToolGuardManager initialized with {len(self.langchain_tools)} tools")

    def _ensure_initialized(self) -> None:
        """Ensure manager is initialized, raise RuntimeError if not."""
        if not self._initialized or not self.langchain_tools:
            raise RuntimeError(
                "ToolGuardManager not initialized. Call initialize() first with a tool provider."
            )

    async def _build_tool_app_name_index(self) -> Dict[str, List[str]]:
        """Build a mapping of tool name to provider app names that expose it."""
        tool_app_names: Dict[str, List[str]] = {}
        apps = await self.tool_provider.get_apps()
        for app in apps:
            if hasattr(self.tool_provider, "get_raw_tools"):
                tools = await self.tool_provider.get_raw_tools(app.name)
            else:
                tools = await self.tool_provider.get_tools(app.name)
            for tool in tools:
                tool_app_names.setdefault(tool.name, [])
                if app.name not in tool_app_names[tool.name]:
                    tool_app_names[tool.name].append(app.name)
        return tool_app_names

    def _schema_from_return_annotation(self, annotation: Any) -> Optional[Dict[str, Any]]:
        """Convert a Python return annotation into a minimal JSON schema."""
        if annotation is None:
            return None

        origin = get_origin(annotation)
        if origin is not None:
            args = [arg for arg in get_args(annotation) if arg is not type(None)]
            if len(args) == 1:
                return self._schema_from_return_annotation(args[0])
            if origin is list:
                return {"type": "array"}
            if origin is dict:
                return {"type": "object"}

        type_mapping = {
            str: {"type": "string"},
            int: {"type": "integer"},
            float: {"type": "number"},
            bool: {"type": "boolean"},
            list: {"type": "array"},
            dict: {"type": "object"},
        }
        return type_mapping.get(annotation)

    def _get_tool_return_schema(self, tool: StructuredTool) -> Optional[Dict[str, Any]]:
        """Return a JSON schema inferred from the original tool return annotation."""
        for callable_attr in ("func", "coroutine"):
            candidate = getattr(tool, callable_attr, None)
            annotations = getattr(candidate, "__annotations__", None)
            if not annotations or "return" not in annotations:
                continue

            schema = self._schema_from_return_annotation(annotations["return"])
            if schema:
                return schema

        return None

    def _enrich_response_schemas_from_annotations(self) -> None:
        """
        Add missing OpenAPI response schemas from LangChain tool return annotations.

        ToolGuard's LangChain-to-OpenAPI conversion may emit a response schema with
        only a title. For tools such as `get_membership() -> str`, this can lead
        generated guards to treat the response like an object and extract fields
        that do not exist. Supplying the primitive response type lets generation
        know the tool directly returns a string.
        """
        schemas_by_tool = {
            tool.name: schema
            for tool in self.langchain_tools
            if (schema := self._get_tool_return_schema(tool)) is not None
        }

        if not schemas_by_tool:
            return

        paths = self.tools_dict.get("paths", {}) if isinstance(self.tools_dict, dict) else {}
        for path_item in paths.values():
            if not isinstance(path_item, dict):
                continue

            for operation in path_item.values():
                if not isinstance(operation, dict):
                    continue

                tool_name = operation.get("operationId")
                schema = schemas_by_tool.get(tool_name)
                if not schema:
                    continue

                response_schema = (
                    operation.setdefault("responses", {})
                    .setdefault("200", {})
                    .setdefault("content", {})
                    .setdefault("application/json", {})
                    .setdefault("schema", {})
                )

                # Preserve rich schemas from registries/OpenAPI specs. Only enrich
                # placeholders such as {"title": "get_membership_output"}.
                if "type" not in response_schema and "properties" not in response_schema:
                    response_schema.update(schema)
                    logger.debug(
                        f"Enriched ToolGuard response schema for '{tool_name}' from return annotation: {schema}"
                    )

    def _validate_policy_and_tool(self, policy: ToolGuide, target_tool: str) -> None:
        """Validate policy type and target tool."""
        if policy.type != PolicyType.TOOL_GUIDE:
            raise ValueError(
                f"Policy must be of type 'tool_guide', got '{policy.type}'. "
                f"Only tool_guide policies can generate examples."
            )

        if "*" not in policy.target_tools and target_tool not in policy.target_tools:
            raise ValueError(
                f"Tool '{target_tool}' is not in policy.target_tools. Policy targets: {policy.target_tools}"
            )

        tool_names = [tool.name for tool in self.langchain_tools]
        if target_tool not in tool_names:
            raise ValueError(
                f"Tool '{target_tool}' not found in available tools. Available tools: {tool_names}"
            )

    def _validate_app_name(self, app_name: str) -> str:
        """
        Validate and sanitize app_name to prevent path traversal attacks.

        Args:
            app_name: Application name to validate

        Returns:
            The validated app_name

        Raises:
            ValueError: If app_name contains unsafe characters or patterns
        """
        # Check for path separators and traversal segments
        if '/' in app_name or '\\' in app_name:
            raise ValueError(f"Invalid app_name '{app_name}': path separators ('/', '\\') are not allowed")

        if '..' in app_name:
            raise ValueError(f"Invalid app_name '{app_name}': path traversal segments ('..') are not allowed")

        # Validate against safe whitelist: alphanumeric, underscore, and hyphen only
        if not re.match(r'^[A-Za-z0-9_-]+$', app_name):
            raise ValueError(
                f"Invalid app_name '{app_name}': only alphanumeric characters, "
                f"underscores, and hyphens are allowed (pattern: /^[A-Za-z0-9_-]+$/)"
            )

        return app_name

    def _infer_app_name_from_tool(self, target_tool: str) -> str:
        """
        Infer app_name from provider apps/tools.

        Args:
            target_tool: Name of the tool to infer app_name for

        Returns:
            App name if exactly one provider app exposes the tool

        Raises:
            ValueError: If no app or multiple apps expose the target tool
        """
        app_names = self._tool_app_names.get(target_tool, [])
        if len(app_names) == 1:
            app_name = app_names[0]
            logger.debug(f"Inferred app_name '{app_name}' from provider app index for tool '{target_tool}'")
            return app_name

        if len(app_names) > 1:
            raise ValueError(
                f"Tool '{target_tool}' is exposed by multiple apps: {app_names}. "
                "Pass app_name explicitly when generating ToolGuard code."
            )

        # Fallback for providers that expose metadata but could not be indexed.
        for tool in self.langchain_tools:
            if tool.name == target_tool and tool.func and hasattr(tool.func, '_app_name'):
                app_name = getattr(tool.func, '_app_name')
                logger.debug(f"Inferred app_name '{app_name}' from tool '{target_tool}' metadata")
                return app_name

        raise ValueError(
            f"Could not infer app_name for tool '{target_tool}'. "
            "Pass app_name explicitly when generating ToolGuard code."
        )

    def _build_description(self, policy: ToolGuide) -> str:
        """Build description from policy, concatenating guide_content if present."""
        description = policy.description
        if policy.guide_content:
            description = f"{description}\n\n{policy.guide_content}"
        return description

    def _create_spec_item(
        self,
        policy: ToolGuide,
        violating_examples: Optional[List[str]] = None,
        compliance_examples: Optional[List[str]] = None,
    ) -> ToolGuardSpecItem:
        """Create ToolGuardSpecItem from policy."""
        description = self._build_description(policy)

        kwargs: Dict[str, Any] = {
            "name": policy.name,
            "description": description,
            "references": [policy.guide_content] if policy.guide_content else [],
        }

        if violating_examples is not None:
            kwargs["violation_examples"] = violating_examples
        if compliance_examples is not None:
            kwargs["compliance_examples"] = compliance_examples

        return ToolGuardSpecItem(**kwargs)

    @contextmanager
    def _temp_directory(self) -> Iterator[Path]:
        """Context manager for temporary toolguard directory.

        Creates a unique temporary subdirectory per invocation to avoid
        race conditions when generate_examples() or generate_guard_code()
        are called concurrently.
        """
        import uuid

        tmp_dir = Path(self.work_dir) / f"tmp_{uuid.uuid4().hex}"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        try:
            yield tmp_dir
        finally:
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir)
                logger.debug(f"Cleaned up temporary directory: {tmp_dir}")

    def _save_domain_files(self, result: ToolGuardsCodeGenerationResult) -> None:
        """Save RuntimeDomain files to domain directory."""
        work_dir_path = Path(self.work_dir)
        domain_dir = work_dir_path / "domain"
        domain_dir.mkdir(parents=True, exist_ok=True)

        for attr_name in ["app_types", "app_api", "app_api_impl"]:
            domain_file = getattr(result.domain, attr_name)
            domain_file.save(domain_dir)
            logger.info(f"Saved {attr_name} to {domain_dir / domain_file.file_name}")

    async def generate_examples(self, policy: ToolGuide, target_tool: str) -> Tuple[List[str], List[str]]:
        """
        Generate violating and compliance examples for a specific tool in a ToolGuide policy.

        Args:
            policy: ToolGuide policy to generate examples for
            target_tool: Specific tool name to generate examples for

        Returns:
            Tuple of (violating_examples, compliance_examples)

        Raises:
            RuntimeError: If manager not initialized
            ValueError: If policy is not a ToolGuide or target_tool not in policy.target_tools
        """
        self._ensure_initialized()
        self._validate_policy_and_tool(policy, target_tool)

        logger.info(f"Generating examples for tool '{target_tool}'...")

        # Create ToolGuardSpecItem with policy information
        spec_item = self._create_spec_item(policy)

        # Create ToolGuardSpec with the spec item
        spec = ToolGuardSpec(tool_name=target_tool, policy_items=[spec_item])

        # Generate examples using toolguard
        with self._temp_directory() as tmp_dir:
            try:
                updated_specs = await generate_guard_examples(
                    tools=self.tools_dict,  # Pass the OpenAPI dict
                    tool_specs=[spec],
                    llm=self.llm,  # type: ignore
                    work_dir=str(tmp_dir),
                )

                # Extract examples from the updated spec
                if updated_specs:
                    updated_spec = updated_specs[0]
                    if updated_spec.policy_items:
                        policy_item = updated_spec.policy_items[0]

                        violating_examples = policy_item.violation_examples
                        compliance_examples = policy_item.compliance_examples

                        logger.info(
                            f"✅ Generated {len(violating_examples)} violating and {len(compliance_examples)} "
                            f"compliance examples for tool '{target_tool}'"
                        )

                        return violating_examples, compliance_examples
                    else:
                        logger.warning(f"No policy items in updated spec for tool '{target_tool}'")
                        return [], []
                else:
                    logger.warning(f"No results returned for tool '{target_tool}'")
                    return [], []

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"❌ Failed to generate examples for tool '{target_tool}': {e}")
                raise RuntimeError(f"Failed to generate examples for tool '{target_tool}'") from e

    async def generate_guard_code(
        self, policy: ToolGuide, target_tool: str, app_name: Optional[str] = None
    ) -> str:
        """
        Generate guard code for a specific tool in a ToolGuide policy.

        This method creates a ToolGuardSpec from the policy, validates it has examples,
        calls toolguard's generate_guards_code, saves the RuntimeDomain to a file,
        and returns the generated guard code content.

        Args:
            policy: ToolGuide policy to generate guard code for
            target_tool: Specific tool name to generate guard code for
            app_name: Application name for the generated code. If None, will be auto-detected
                     from provider apps/tools when unambiguous

        Returns:
            String containing the generated guard code

        Raises:
            RuntimeError: If manager not initialized
            ValueError: If policy is not a ToolGuide, target_tool not in policy.target_tools,
                       if the policy doesn't have examples for the target tool,
                       or if app_name contains unsafe characters
        """
        self._ensure_initialized()
        self._validate_policy_and_tool(policy, target_tool)

        # Auto-detect app_name if not provided
        if app_name is None:
            app_name = self._infer_app_name_from_tool(target_tool)
            logger.info(f"Auto-detected app_name '{app_name}' for tool '{target_tool}'")

        # Validate app_name to prevent path traversal attacks
        app_name = self._validate_app_name(app_name)

        logger.info(f"Generating guard code for tool '{target_tool}' with app_name '{app_name}'...")

        # Check if policy has tool_guards for this specific tool
        tool_guard = None
        if policy.tool_guards and target_tool in policy.tool_guards:
            tool_guard = policy.tool_guards[target_tool]

        # Validate that we have examples (either from tool_guards or need to generate them first)
        if tool_guard:
            violating_examples = tool_guard.violating_examples
            compliance_examples = tool_guard.compliance_examples
        else:
            violating_examples = []
            compliance_examples = []

        # Ensure we have examples
        if not violating_examples and not compliance_examples:
            raise ValueError(
                f"Policy for tool '{target_tool}' must have examples before generating guard code. "
                f"Call generate_examples() first to create examples, or provide them in the policy's tool_guards."
            )

        # Create ToolGuardSpecItem with policy information and examples
        spec_item = self._create_spec_item(
            policy, violating_examples=violating_examples, compliance_examples=compliance_examples
        )

        # Create ToolGuardSpec with the spec item
        spec = ToolGuardSpec(tool_name=target_tool, policy_items=[spec_item])

        # Generate guard code using toolguard
        with self._temp_directory() as tmp_dir:
            try:
                result: ToolGuardsCodeGenerationResult = await generate_guards_code(
                    tools=self.tools_dict,  # Pass the OpenAPI dict
                    tool_specs=[spec],
                    work_dir=str(tmp_dir),
                    llm=self.llm,  # type: ignore
                    app_name=app_name,
                )

                # Save RuntimeDomain files directly under toolguard directory (not in tmp)
                self._save_domain_files(result)

                # Extract guard code from the result
                if target_tool in result.tools:
                    tool_result = result.tools[target_tool]

                    # Get the item guard file content (should be only one)
                    if not tool_result.item_guard_files:
                        raise ValueError(f"No item guard files generated for tool '{target_tool}'")

                    if len(tool_result.item_guard_files) > 1:
                        logger.warning(
                            f"Multiple item guard files found for tool '{target_tool}', using the first one"
                        )

                    item_guard_file = tool_result.item_guard_files[0]
                    if item_guard_file is None:
                        raise ValueError(f"Item guard file is None for tool '{target_tool}'")

                    guard_code = item_guard_file.content

                    logger.info(
                        f"✅ Generated guard code for tool '{target_tool}' "
                        f"(guard function: {tool_result.guard_fn_name})"
                    )

                    return guard_code
                else:
                    raise ValueError(
                        f"Tool '{target_tool}' not found in generation results. "
                        f"Available tools: {list(result.tools.keys())}"
                    )

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"❌ Failed to generate guard code for tool '{target_tool}': {e}")
                raise RuntimeError(f"Failed to generate guard code for tool '{target_tool}'") from e

    @property
    def is_initialized(self) -> bool:
        """Check if the manager has been initialized."""
        return self._initialized
