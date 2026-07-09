"""
Runtime execution of tool guards for policy enforcement.

This module provides runtime validation of tool calls against registered
ToolGuide policies with policy_code.
"""

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence, Tuple

from loguru import logger
from toolguard.runtime.data_types import (
    FileTwin,
    PolicyViolationException,
    RuntimeDomain,
    ToolGuardCodeResult,
    ToolGuardsCodeGenerationResult,
    ToolGuardSpec,
    ToolGuardSpecItem,
)
from toolguard.runtime.runtime import load_toolguards_from_memory

from cuga.backend.cuga_graph.policy.models import PolicyType, ToolGuide
from cuga.backend.cuga_graph.policy.storage import PolicyStorage
from cuga.backend.cuga_graph.policy.tool_guard.tool_invoker import ToolGuardInvoker


class ToolGuardRuntime:
    """
    Runtime system for executing tool guards during tool invocation.

    This class:
    1. Manages policy storage lifecycle (connect/disconnect)
    2. Initializes a ToolGuardInvoker for tool execution
    3. Loads all ToolGuide policies with policy_code
    4. Creates a mapping: tool_name -> List[ToolGuide with code]
    5. Prebuilds umbrella guard modules per tool
    6. Executes guard validation through toolguard runtime
    """

    def __init__(
        self,
        tool_provider,
        enable_policies: bool = False,
        policy_storage: Optional[PolicyStorage] = None,
        cuga_folder: str = ".cuga",
    ) -> None:
        """
        Initialize the ToolGuardRuntime.

        Args:
            tool_provider: CUGA's tool provider instance
            enable_policies: Whether to enable policy enforcement
            policy_storage: Optional PolicyStorage instance (will be created if None and enable_policies=True)
            cuga_folder: Folder containing ToolGuard domain files
        """
        self.tool_provider = tool_provider
        self.enable_policies = enable_policies
        self.policy_storage = policy_storage
        self.cuga_folder = cuga_folder
        self.invoker = ToolGuardInvoker(tool_provider)
        self.tool_to_guards: Dict[str, List[ToolGuide]] = {}
        self.invalid_tool_guards: Dict[str, List[ToolGuide]] = {}
        # Per-app runtime mapping to avoid cross-app collisions
        self._runtimes_by_app: Dict[str, Any] = {}
        self._runtime_domains_by_app: Dict[str, RuntimeDomain] = {}
        self._initialized = False
        self._policy_storage_owned = False  # Track if we created the storage
        logger.debug(f"Created ToolGuardRuntime instance (enable_policies={enable_policies})")

    async def initialize(self) -> None:
        """
        Initialize the runtime by connecting to policy storage and loading policies.

        This method:
        1. Connects to policy storage if policies are enabled
        2. Fetches all ToolGuide policies from storage
        3. Filters for policies that have tool_guards with policy_code
        4. Builds the tool_to_guards mapping
        5. Per-app runtimes will be lazily loaded on first use

        Raises:
            RuntimeError: If policy system is enabled but storage connection fails (fail-closed)
        """
        logger.info("Initializing ToolGuardRuntime...")
        self._reset_state()

        # Connect to policy storage if policies are enabled
        if self.enable_policies:
            if self.policy_storage is None:
                # Create policy storage if not provided
                from cuga.backend.cuga_graph.policy.storage import PolicyStorage

                self.policy_storage = PolicyStorage()
                self._policy_storage_owned = True
                logger.debug("Created PolicyStorage instance")

            # Validate policy_storage has required interface
            self._validate_policy_storage()

            try:
                await self.policy_storage.connect()
                logger.info("✅ Connected policy storage for ToolGuardRuntime")
            except Exception as e:
                logger.error(f"Failed to connect policy storage: {e}")
                # Fail closed: if policy enforcement is enabled but storage fails,
                # don't allow the service to start without policy validation
                raise RuntimeError(f"Policy system is enabled but PolicyStorage.connect() failed: {e}") from e

        # Load policies if storage is available
        if self.policy_storage is not None:
            policies = await self.policy_storage.list_policies(
                policy_type=PolicyType.TOOL_GUIDE, enabled_only=True
            )
            logger.debug(f"Found {len(policies)} ToolGuide policies")

            # Filter to ensure we only have ToolGuide instances
            tool_guide_policies = [p for p in policies if isinstance(p, ToolGuide)]
            self._build_tool_to_guards_mapping(tool_guide_policies)
        else:
            logger.debug("No policy storage available, skipping policy loading")

        self._initialized = True
        self._log_initialization_summary()

    def _validate_policy_storage(self) -> None:
        """
        Validate that policy_storage has the required interface.

        Raises:
            ValueError: If policy_storage doesn't implement required methods
        """
        if self.policy_storage is None:
            return

        required_methods = ['connect', 'disconnect', 'list_policies', 'get_policy']
        missing_methods = []

        for method in required_methods:
            if not hasattr(self.policy_storage, method):
                missing_methods.append(method)

        if missing_methods:
            raise ValueError(
                f"policy_storage must implement the following methods: {', '.join(missing_methods)}. "
                f"Provided object type: {type(self.policy_storage).__name__}"
            )

        logger.debug("✅ Policy storage interface validation passed")

    def _reset_state(self) -> None:
        """Reset internal state for reinitialization."""
        # Clean up all per-app runtimes
        for app_name, runtime in self._runtimes_by_app.items():
            if runtime is not None:
                try:
                    runtime.__exit__(None, None, None)
                except Exception:
                    logger.exception(f"Error while exiting ToolGuard runtime for app '{app_name}'")
        self.tool_to_guards = {}
        self.invalid_tool_guards = {}
        self._runtimes_by_app = {}
        self._runtime_domains_by_app = {}

    def _build_tool_to_guards_mapping(self, policies: Sequence[ToolGuide]) -> None:
        """
        Build mapping from tool names to their guard policies.

        Args:
            policies: Sequence of ToolGuide policies to process
        """
        for policy in policies:
            if not policy.tool_guards:
                logger.debug(f"Policy '{policy.name}' has no tool_guards, skipping")
                continue

            self._register_policy_guards(policy)

    def _register_policy_guards(self, policy: ToolGuide) -> None:
        """
        Register guards from a policy for all its tools.

        Args:
            policy: ToolGuide policy to register
        """
        if not policy.tool_guards:
            return

        if not getattr(policy, "guards_enabled", True):
            logger.info(f"Guards for policy '{policy.name}' skipped: guards_enabled=False")
            return

        for tool_name, tool_guard in policy.tool_guards.items():
            if not tool_guard.policy_code:
                logger.debug(
                    f"Tool guard for '{tool_name}' in policy '{policy.name}' has no policy_code, skipping"
                )
                continue

            # Validate that policy_code contains at least one async def guard_* function
            guard_func_name = self._extract_guard_function_name(tool_guard.policy_code)
            if not guard_func_name:
                logger.error(
                    f"Tool guard for '{tool_name}' in policy '{policy.name}' "
                    f"has policy_code but no valid 'async def guard_*' function found. "
                    "Tracking as invalid so matching tool calls fail closed."
                )
                if tool_name not in self.invalid_tool_guards:
                    self.invalid_tool_guards[tool_name] = []
                self.invalid_tool_guards[tool_name].append(policy)
                continue

            if tool_name not in self.tool_to_guards:
                self.tool_to_guards[tool_name] = []

            self.tool_to_guards[tool_name].append(policy)
            logger.debug(
                f"Registered guard for tool '{tool_name}' from policy '{policy.name}' "
                f"with guard function '{guard_func_name}'"
            )

    def _log_initialization_summary(self) -> None:
        """Log summary of initialization results."""
        logger.info(f"✅ ToolGuardRuntime initialized with guards for {len(self.tool_to_guards)} tools")
        for tool_name, guards in self.tool_to_guards.items():
            logger.debug(
                f"  - Tool '{tool_name}': {len(guards)} guard(s) ({', '.join(g.name for g in guards)})"
            )

    def _build_runtime(self, app_name: str):
        """
        Build an in-memory ToolGuard runtime from registered guard policies for a specific app.

        Args:
            app_name: Name of the application to build runtime for

        Returns:
            Runtime instance for the specified app
        """
        runtime_domain = self._runtime_domains_by_app.get(app_name)
        if runtime_domain is None:
            raise RuntimeError(f"ToolGuard runtime domain not loaded for app '{app_name}'")

        file_twins: List[FileTwin] = [
            runtime_domain.app_types,
            runtime_domain.app_api,
            runtime_domain.app_api_impl,
        ]
        tools: Dict[str, ToolGuardCodeResult] = {}

        for tool_name, all_guards in self.tool_to_guards.items():
            # Filter guards to only those applicable to this app
            guards = [
                guard
                for guard in all_guards
                if guard.target_apps is None or not guard.target_apps or app_name in guard.target_apps
            ]

            # Skip this tool if no guards apply to this app
            if not guards:
                logger.debug(
                    f"Skipping tool '{tool_name}' for app '{app_name}' - "
                    f"no applicable guards (out of {len(all_guards)} total)"
                )
                continue

            module_name = self._module_name_for_tool(tool_name)
            guard_fn_name = self._guard_function_name_for_tool(tool_name)
            guard_module_path = Path(*module_name.split(".")).with_suffix(".py")

            module_content = self._build_tool_guard_module(
                tool_name=tool_name,
                guards=guards,
                guard_fn_name=guard_fn_name,
            )

            guard_file = FileTwin(
                file_name=guard_module_path,
                content=module_content,
            )
            file_twins.append(guard_file)

            tools[tool_name] = ToolGuardCodeResult(
                tool=ToolGuardSpec(
                    tool_name=tool_name,
                    policy_items=[
                        ToolGuardSpecItem(
                            name=policy.name,
                            description=f"Runtime guard from policy '{policy.name}'",
                        )
                        for policy in guards
                    ],
                ),
                guard_fn_name=guard_fn_name,
                guard_file=guard_file,
                item_guard_files=[],
                test_files=[],
            )

        result = ToolGuardsCodeGenerationResult(
            out_dir=Path("."),
            domain=runtime_domain,
            tools=tools,
        )

        runtime = load_toolguards_from_memory(result)
        runtime.__enter__()
        return runtime

    def _load_runtime_domain(self, app_name: str) -> RuntimeDomain:
        """
        Load RuntimeDomain files saved by ToolGuardManager for a specific app.

        Args:
            app_name: Name of the application to load domain for

        Returns:
            RuntimeDomain with loaded domain files for the specified app

        Raises:
            RuntimeError: If domain directory or files are not found
        """
        domain_dir = Path(self.cuga_folder) / "toolguard" / "domain"
        self._validate_domain_directory(domain_dir)

        # Try exact match first
        selected_domain = self._find_complete_domain_for_app(domain_dir, app_name)

        # If exact match not found, try fuzzy match (e.g., "crm" -> "crm_demo")
        if selected_domain is None:
            logger.debug(f"Exact domain match not found for '{app_name}', trying fuzzy match...")
            for dir_path in domain_dir.iterdir():
                if dir_path.is_dir() and app_name in dir_path.name:
                    candidate_name = dir_path.name
                    selected_domain = self._find_complete_domain_for_app(domain_dir, candidate_name)
                    if selected_domain:
                        logger.info(
                            f"Found domain for app '{app_name}' using fuzzy match: '{candidate_name}'"
                        )
                        break

        if selected_domain is None:
            available_apps = [d.name for d in domain_dir.iterdir() if d.is_dir()]
            raise RuntimeError(
                f"No complete ToolGuard domain found for app '{app_name}' under {domain_dir}. "
                f"Available apps: {', '.join(available_apps) if available_apps else 'none'}"
            )

        return self._create_runtime_domain(domain_dir, selected_domain)

    def _validate_domain_directory(self, domain_dir: Path) -> None:
        """
        Validate that the domain directory exists.

        Args:
            domain_dir: Path to domain directory

        Raises:
            RuntimeError: If domain directory doesn't exist
        """
        if not domain_dir.exists():
            raise RuntimeError(
                f"ToolGuard domain directory not found: {domain_dir}. "
                "Generate tool guard code first so ToolGuardManager saves the domain files."
            )

    def _get_sorted_app_directories(self, domain_dir: Path) -> List[Path]:
        """
        Get app directories sorted by modification time (newest first).

        Args:
            domain_dir: Path to domain directory

        Returns:
            List of app directory paths

        Raises:
            RuntimeError: If no app directories found
        """
        app_dirs = sorted(
            [path for path in domain_dir.iterdir() if path.is_dir()],
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if not app_dirs:
            raise RuntimeError(f"No ToolGuard app directories found under {domain_dir}")
        return app_dirs

    def _find_complete_domain(
        self, domain_dir: Path, app_dirs: List[Path]
    ) -> Optional[Tuple[str, Path, Path, Path]]:
        """
        Find the first complete domain with all required files.

        Args:
            domain_dir: Path to domain directory
            app_dirs: List of app directories to search

        Returns:
            Tuple of (app_name, types_path, api_path, impl_path) or None
        """
        for app_dir in app_dirs:
            app_name = app_dir.name
            app_types_rel = Path(app_name) / f"{app_name}_types.py"
            app_api_rel = Path(app_name) / f"i_{app_name}.py"
            app_api_impl_rel = Path(app_name) / f"{app_name}_impl.py"

            candidate_paths = [
                domain_dir / app_types_rel,
                domain_dir / app_api_rel,
                domain_dir / app_api_impl_rel,
            ]
            if all(path.exists() for path in candidate_paths):
                return (app_name, app_types_rel, app_api_rel, app_api_impl_rel)

        return None

    def _find_complete_domain_for_app(
        self, domain_dir: Path, app_name: str
    ) -> Optional[Tuple[str, Path, Path, Path]]:
        """
        Find complete domain files for a specific app.

        Args:
            domain_dir: Path to domain directory
            app_name: Name of the app to find domain for

        Returns:
            Tuple of (app_name, types_path, api_path, impl_path) or None
        """
        app_types_rel = Path(app_name) / f"{app_name}_types.py"
        app_api_rel = Path(app_name) / f"i_{app_name}.py"
        app_api_impl_rel = Path(app_name) / f"{app_name}_impl.py"

        candidate_paths = [
            domain_dir / app_types_rel,
            domain_dir / app_api_rel,
            domain_dir / app_api_impl_rel,
        ]
        if all(path.exists() for path in candidate_paths):
            return (app_name, app_types_rel, app_api_rel, app_api_impl_rel)

        return None

    def _create_runtime_domain(
        self, domain_dir: Path, selected_domain: Tuple[str, Path, Path, Path]
    ) -> RuntimeDomain:
        """
        Create RuntimeDomain from selected domain files.

        Args:
            domain_dir: Path to domain directory
            selected_domain: Tuple of (app_name, types_path, api_path, impl_path)

        Returns:
            RuntimeDomain instance
        """
        app_name, app_types_rel, app_api_rel, app_api_impl_rel = selected_domain

        api_content = FileTwin.load_from(domain_dir, app_api_rel).content
        api_impl_content = FileTwin.load_from(domain_dir, app_api_impl_rel).content

        app_api_class_name = self._extract_class_name(
            api_content, f"I{''.join(part.capitalize() for part in app_name.split('_'))}"
        )
        app_api_impl_class_name = self._extract_class_name(
            api_impl_content, ''.join(part.capitalize() for part in app_name.split('_'))
        )

        return RuntimeDomain(
            app_name=app_name,
            app_types=FileTwin.load_from(domain_dir, app_types_rel),
            app_api_class_name=app_api_class_name,
            app_api=FileTwin.load_from(domain_dir, app_api_rel),
            app_api_size=0,
            app_api_impl_class_name=app_api_impl_class_name,
            app_api_impl=FileTwin.load_from(domain_dir, app_api_impl_rel),
        )

    def _extract_class_name(self, content: str, default: str) -> str:
        """
        Extract class name from Python source code.

        Args:
            content: Python source code
            default: Default class name if not found

        Returns:
            Extracted or default class name
        """
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("class "):
                return stripped.split()[1].split("(")[0].rstrip(":")
        return default

    def _build_tool_guard_module(
        self,
        tool_name: str,
        guards: List[ToolGuide],
        guard_fn_name: str,
    ) -> str:
        """
        Create a module containing a single umbrella guard function for one tool.

        Args:
            tool_name: Name of the tool
            guards: List of ToolGuide policies for this tool
            guard_fn_name: Name for the umbrella guard function

        Returns:
            Generated Python module content as string
        """
        guard_blocks: List[str] = []
        guard_calls: List[str] = []

        for index, policy in enumerate(guards):
            self._process_policy_guard(policy, tool_name, index, guard_blocks, guard_calls)

        return self._generate_module_content(guard_fn_name, guard_blocks, guard_calls)

    def _process_policy_guard(
        self,
        policy: ToolGuide,
        tool_name: str,
        index: int,
        guard_blocks: List[str],
        guard_calls: List[str],
    ) -> None:
        """
        Process a single policy guard and add to blocks and calls.

        Args:
            policy: ToolGuide policy to process
            tool_name: Name of the tool
            index: Index of this guard
            guard_blocks: List to append guard code blocks to
            guard_calls: List to append guard call statements to
        """
        tool_guard = policy.tool_guards.get(tool_name) if policy.tool_guards else None
        if not tool_guard or not tool_guard.policy_code:
            logger.warning(f"Policy '{policy.name}' missing tool_guard for '{tool_name}', skipping")
            return

        guard_func_name = self._extract_guard_function_name(tool_guard.policy_code)
        if not guard_func_name:
            logger.warning(f"Could not find guard function in policy code for '{policy.name}', skipping")
            return

        validate_alias = f"_guard_validate_{index}"

        guard_blocks.append(
            f"# Policy: {policy.name}\n"
            f"{tool_guard.policy_code}\n"
            f"# Assign the specific guard function for this policy\n"
            f"{validate_alias} = {guard_func_name}\n"
        )

        # Sanitize policy name for safe embedding in generated Python code
        policy_name_literal = repr(policy.name)

        guard_calls.extend(
            [
                "    try:",
                f"        await {validate_alias}(api=api, args=args)",
                "    except PolicyViolationException as e:",
                "        error_msg = str(e)",
                "        # Check if error already contains policy name to avoid duplication",
                f"        _policy_name = {policy_name_literal}",
                "        _prefix = f\"[{_policy_name}]\"",
                "        if not error_msg.startswith(_prefix):",
                "            error_msg = f\"{_prefix} {error_msg}\"",
                "        violations.append(error_msg)",
            ]
        )

    def _extract_guard_function_name(self, policy_code: str) -> Optional[str]:
        """
        Extract guard function name from policy code.

        Args:
            policy_code: Generated policy code

        Returns:
            Guard function name or None if not found
        """
        for line in policy_code.split('\n'):
            line = line.strip()
            if line.startswith('async def guard_'):
                # Extract function name: "async def guard_xxx(..." -> "guard_xxx"
                return line.split('(')[0].replace('async def ', '').strip()
        return None

    def _generate_module_content(
        self, guard_fn_name: str, guard_blocks: List[str], guard_calls: List[str]
    ) -> str:
        """
        Generate the complete module content.

        Args:
            guard_fn_name: Name for the umbrella guard function
            guard_blocks: List of guard code blocks
            guard_calls: List of guard call statements

        Returns:
            Complete module content as string
        """
        if not guard_calls:
            guard_calls = ["    return None"]
        else:
            guard_calls = [
                "    violations = []",
                *guard_calls,
                "    if violations:",
                "        raise PolicyViolationException(\"\\n\".join(violations))",
            ]

        return (
            "from toolguard.runtime.data_types import (\n"
            "    PolicyViolationException,\n"
            "    assert_any_condition_met,\n"
            ")\n"
            "from toolguard.runtime.rules import rule\n\n"
            f"{''.join(guard_blocks)}\n"
            f"async def {guard_fn_name}(api, args):\n"
            f"{chr(10).join(guard_calls)}\n"
        )

    def _module_name_for_tool(self, tool_name: str) -> str:
        """
        Convert a tool name to a valid python module name.

        Args:
            tool_name: Name of the tool

        Returns:
            Valid Python module name
        """
        normalized = self._normalize_name(tool_name)
        return f"cuga_toolguard_runtime.generated.guard_{normalized}"

    def _guard_function_name_for_tool(self, tool_name: str) -> str:
        """
        Convert a tool name to a valid umbrella guard function name.

        Args:
            tool_name: Name of the tool

        Returns:
            Valid Python function name
        """
        normalized = self._normalize_name(tool_name)
        return f"guard_{normalized}"

    def _normalize_name(self, name: str) -> str:
        """
        Normalize a name to be a valid Python identifier with disambiguation.

        Args:
            name: Name to normalize

        Returns:
            Normalized name safe for use as Python identifier with hash suffix
        """
        import hashlib

        # Create readable normalized portion
        normalized = "".join(ch if ch.isalnum() else "_" for ch in name.lower()).strip("_")

        # Use "tool" as base if normalization results in empty string
        base = normalized if normalized else "tool"

        # Add short hash suffix for disambiguation
        name_hash = hashlib.sha256(name.encode()).hexdigest()[:8]

        return f"{base}_{name_hash}"

    async def _get_or_create_runtime_for_app(self, app_name: str):
        """
        Get or lazily create a runtime for the specified app.

        Args:
            app_name: Name of the application

        Returns:
            Runtime instance for the app, or None if it cannot be created
        """
        # Return cached runtime if available
        if app_name in self._runtimes_by_app:
            return self._runtimes_by_app[app_name]

        # Try to load and build runtime for this app
        try:
            logger.info(f"Loading runtime domain for app '{app_name}'...")
            runtime_domain = self._load_runtime_domain(app_name)
            self._runtime_domains_by_app[app_name] = runtime_domain

            logger.info(f"Building runtime for app '{app_name}'...")
            runtime = self._build_runtime(app_name)
            self._runtimes_by_app[app_name] = runtime

            logger.info(f"✅ Runtime initialized for app '{app_name}'")
            return runtime
        except Exception as e:
            logger.error(f"Failed to initialize runtime for app '{app_name}': {e}", exc_info=True)
            # Cache None to avoid repeated failed attempts
            self._runtimes_by_app[app_name] = None
            return None

    async def _type_cast_arguments(
        self, arguments: Dict[str, Any], app_name: str, function_name: str
    ) -> Dict[str, Any]:
        """
        Type-cast arguments using the tool's existing LangChain/Pydantic args_schema.

        This avoids reconstructing generated model names from tool names or parsing
        generated source files. If no schema is available, the original arguments
        are returned unchanged.
        """
        try:
            tools = await self.invoker._get_tools()
            tool = tools.get(function_name)
            if tool is None:
                logger.debug(
                    f"Could not find tool '{function_name}' for app '{app_name}', skipping type casting"
                )
                return arguments

            args_schema = getattr(tool, "args_schema", None)
            if args_schema is None:
                logger.debug(f"Tool '{function_name}' has no args_schema, skipping type casting")
                return arguments

            if hasattr(args_schema, "model_validate"):
                validated = args_schema.model_validate(arguments)
                typed_arguments = validated.model_dump()
                logger.debug(f"Type-cast arguments for '{function_name}' using Pydantic v2 args_schema")
                return arguments | typed_arguments

            if hasattr(args_schema, "parse_obj"):
                validated = args_schema.parse_obj(arguments)
                typed_arguments = validated.dict()
                logger.debug(f"Type-cast arguments for '{function_name}' using Pydantic v1 args_schema")
                return arguments | typed_arguments

            logger.debug(
                f"Tool '{function_name}' args_schema does not expose Pydantic validation APIs, "
                "skipping type casting"
            )
            return arguments

        except Exception as e:
            logger.warning(
                f"Error during args_schema type casting for '{function_name}' in app '{app_name}': {e}. "
                "Using original arguments.",
                exc_info=True,
            )
            return arguments

    async def guard_tool_call(
        self, app_name: str, function_name: str, arguments: Dict[str, Any]
    ) -> Optional[str]:
        """
        Validate a tool call against registered guards.

        This method delegates validation to the ToolGuard runtime using a
        prebuilt umbrella guard function for the requested tool.

        Args:
            app_name: Name of the application calling the tool
            function_name: Name of the tool/function being called
            arguments: Arguments being passed to the tool

        Returns:
            Error message string if validation fails, None if validation passes
        """
        if not self._initialized:
            logger.warning("ToolGuardRuntime not initialized, skipping validation")
            return None

        invalid_guards = [
            guard
            for guard in self.invalid_tool_guards.get(function_name, [])
            if guard.target_apps is None or not guard.target_apps or app_name in guard.target_apps
        ]
        if invalid_guards:
            policy_names = ", ".join(guard.name for guard in invalid_guards)
            logger.warning(
                f"Tool guard policy_code is invalid for tool '{function_name}' in app '{app_name}'. "
                f"Blocking tool call because declared guard policy/policies cannot be enforced: {policy_names}"
            )
            return (
                f"Invalid ToolGuard policy_code for '{function_name}' in app '{app_name}'. "
                f"Tool call blocked because declared guard policy/policies cannot be enforced: {policy_names}."
            )

        # Check if this tool has any guards
        if function_name not in self.tool_to_guards:
            logger.debug(f"No guards registered for tool '{function_name}'")
            return None

        # Filter guards to only those applicable to this app
        all_guards = self.tool_to_guards[function_name]
        guards = [
            guard
            for guard in all_guards
            if guard.target_apps is None or not guard.target_apps or app_name in guard.target_apps
        ]

        if not guards:
            target_apps_summary = {guard.name: guard.target_apps for guard in all_guards if guard.target_apps}
            logger.warning(
                f"Tool '{function_name}' has {len(all_guards)} registered guard(s), but none apply "
                f"to app '{app_name}' after target_apps filtering. Tool call will proceed unguarded. "
                f"Configured target_apps: {target_apps_summary}"
            )
            return None

        # Get or create app-specific runtime
        runtime = await self._get_or_create_runtime_for_app(app_name)
        if runtime is None:
            logger.warning(
                f"ToolGuard runtime unavailable for app '{app_name}' and tool '{function_name}', "
                "blocking validation because applicable guard policies exist"
            )
            return (
                f"ToolGuard runtime unavailable for '{function_name}' in app '{app_name}'. "
                "Tool call blocked because an applicable guard policy exists but could not be loaded."
            )

        logger.debug(
            f"Validating tool call '{function_name}' for app '{app_name}' against "
            f"{len(guards)} applicable guard(s) (out of {len(all_guards)} total) using umbrella runtime"
        )

        try:
            # Type-cast arguments to their proper types before validation
            typed_arguments = await self._type_cast_arguments(arguments, app_name, function_name)

            if "args" in typed_arguments:
                logger.error(
                    f"Tool '{function_name}' in app '{app_name}' has a parameter named 'args', "
                    "which conflicts with ToolGuard's injected guard argument namespace. "
                    "Blocking to avoid corrupting guard inputs."
                )
                return (
                    f"Internal guard argument collision for '{function_name}': tool parameter 'args' "
                    "conflicts with ToolGuard runtime metadata. Tool call blocked as a safety precaution."
                )

            args_obj = SimpleNamespace(**typed_arguments)
            await runtime.guard_toolcall(
                tool_name=function_name,
                args=typed_arguments | {"args": args_obj},
                delegate=self.invoker,
            )
        except PolicyViolationException as e:
            error = str(e)
            logger.warning(f"Tool guard blocked call to '{function_name}' for app '{app_name}': {error}")
            return error
        except Exception as e:
            logger.error(
                f"Error executing umbrella guard for tool '{function_name}' in app '{app_name}': {e}",
                exc_info=True,
            )
            # Fail closed: treat internal guard errors as a violation so a buggy
            # or malformed guard cannot silently bypass policy enforcement.
            return (
                f"Internal guard error for '{function_name}': {e}. Tool call blocked as a safety precaution."
            )

        logger.debug(f"Tool call '{function_name}' for app '{app_name}' passed all guards")
        return None

    @property
    def is_initialized(self) -> bool:
        """Check if the runtime has been initialized."""
        return self._initialized

    def get_guarded_tools(self) -> List[str]:
        """
        Get list of tool names that have guards registered.

        Returns:
            List of tool names with active guards
        """
        return list(self.tool_to_guards.keys())

    def get_guards_for_tool(self, tool_name: str) -> List[ToolGuide]:
        """
        Get all guards registered for a specific tool.

        Args:
            tool_name: Name of the tool

        Returns:
            List of ToolGuide policies with guards for this tool
        """
        return self.tool_to_guards.get(tool_name, [])

    async def shutdown(self) -> None:
        """Release in-memory ToolGuard runtime resources and disconnect policy storage."""
        # Clean up all per-app runtimes
        for app_name, runtime in self._runtimes_by_app.items():
            if runtime is not None:
                try:
                    runtime.__exit__(None, None, None)
                except Exception:
                    logger.exception(f"Error while shutting down ToolGuard runtime for app '{app_name}'")
        self._runtimes_by_app = {}
        self._runtime_domains_by_app = {}

        # Disconnect policy storage if we own it
        if self.policy_storage is not None and self._policy_storage_owned:
            try:
                await self.policy_storage.disconnect()
                logger.debug("Disconnected policy storage")
            except Exception as e:
                logger.warning(f"Error disconnecting policy storage during shutdown: {e}")
            self.policy_storage = None

        self._initialized = False
        logger.debug("ToolGuardRuntime shutdown complete")
