"""
Runtime execution of tool guards for policy enforcement.

This module provides runtime validation of tool calls against registered
ToolGuide policies with policy_code.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from types import SimpleNamespace
from loguru import logger

from toolguard.runtime.data_types import (
    FileTwin,
    PolicyViolationException,
    RuntimeDomain,
    ToolGuardCodeResult,
    ToolGuardSpec,
    ToolGuardSpecItem,
    ToolGuardsCodeGenerationResult,
)
from toolguard.runtime.runtime import load_toolguards_from_memory

from cuga.backend.cuga_graph.policy.models import Policy, PolicyType, ToolGuide
from cuga.backend.cuga_graph.policy.storage import PolicyStorage
from cuga.backend.cuga_graph.policy.tool_guard.tool_invoker import ToolGuardInvoker


class ToolGuardRuntime:
    """
    Runtime system for executing tool guards during tool invocation.

    This class:
    1. Initializes a ToolGuardInvoker for tool execution
    2. Loads all ToolGuide policies with policy_code
    3. Creates a mapping: tool_name -> List[ToolGuide with code]
    4. Prebuilds umbrella guard modules per tool
    5. Executes guard validation through toolguard runtime
    """

    def __init__(self, tool_provider, policy_storage: PolicyStorage) -> None:
        """
        Initialize the ToolGuardRuntime.

        Args:
            tool_provider: CUGA's tool provider instance
            policy_storage: PolicyStorage instance to load policies from
        """
        self.tool_provider = tool_provider
        self.policy_storage = policy_storage
        self.invoker = ToolGuardInvoker(tool_provider)
        self.tool_to_guards: Dict[str, List[ToolGuide]] = {}
        self._runtime = None
        self._runtime_domain: Optional[RuntimeDomain] = None
        self._initialized = False
        logger.debug("Created ToolGuardRuntime instance")

    async def initialize(self) -> None:
        """
        Initialize the runtime by loading all ToolGuide policies with code.

        This method:
        1. Fetches all ToolGuide policies from storage
        2. Filters for policies that have tool_guards with policy_code
        3. Builds the tool_to_guards mapping
        4. Creates umbrella guard functions per tool
        5. Builds an in-memory ToolGuard runtime
        """
        logger.info("Initializing ToolGuardRuntime...")
        self._reset_state()

        policies = await self.policy_storage.list_policies(
            policy_type=PolicyType.TOOL_GUIDE, enabled_only=True
        )
        logger.debug(f"Found {len(policies)} ToolGuide policies")

        # Filter to ensure we only have ToolGuide instances
        tool_guide_policies = [p for p in policies if isinstance(p, ToolGuide)]
        self._build_tool_to_guards_mapping(tool_guide_policies)

        if self.tool_to_guards:
            self._runtime_domain = self._load_runtime_domain()
            self._runtime = self._build_runtime()

        self._initialized = True
        self._log_initialization_summary()

    def _reset_state(self) -> None:
        """Reset internal state for reinitialization."""
        if self._runtime is not None:
            try:
                self._runtime.__exit__(None, None, None)
            except Exception:
                logger.exception("Error while exiting previous ToolGuard runtime")
        self.tool_to_guards = {}
        self._runtime = None
        self._runtime_domain = None

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

        for tool_name, tool_guard in policy.tool_guards.items():
            if not tool_guard.policy_code:
                logger.debug(
                    f"Tool guard for '{tool_name}' in policy '{policy.name}' "
                    f"has no policy_code, skipping"
                )
                continue

            if tool_name not in self.tool_to_guards:
                self.tool_to_guards[tool_name] = []

            self.tool_to_guards[tool_name].append(policy)
            logger.debug(
                f"Registered guard for tool '{tool_name}' from policy '{policy.name}'"
            )

    def _log_initialization_summary(self) -> None:
        """Log summary of initialization results."""
        logger.info(
            f"✅ ToolGuardRuntime initialized with guards for "
            f"{len(self.tool_to_guards)} tools"
        )
        for tool_name, guards in self.tool_to_guards.items():
            logger.debug(
                f"  - Tool '{tool_name}': {len(guards)} guard(s) "
                f"({', '.join(g.name for g in guards)})"
            )

    def _build_runtime(self):
        """Build an in-memory ToolGuard runtime from registered guard policies."""
        if self._runtime_domain is None:
            raise RuntimeError("ToolGuard runtime domain is not loaded")

        file_twins: List[FileTwin] = [
            self._runtime_domain.app_types,
            self._runtime_domain.app_api,
            self._runtime_domain.app_api_impl,
        ]
        tools: Dict[str, ToolGuardCodeResult] = {}

        for tool_name, guards in self.tool_to_guards.items():
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
            domain=self._runtime_domain,
            tools=tools,
        )

        runtime = load_toolguards_from_memory(result)
        runtime.__enter__()
        return runtime

    def _load_runtime_domain(self) -> RuntimeDomain:
        """
        Load RuntimeDomain files saved by ToolGuardManager.

        Returns:
            RuntimeDomain with loaded domain files

        Raises:
            RuntimeError: If domain directory or files are not found
        """
        domain_dir = Path.cwd() / ".cuga" / "toolguard" / "domain"
        self._validate_domain_directory(domain_dir)

        app_dirs = self._get_sorted_app_directories(domain_dir)
        selected_domain = self._find_complete_domain(domain_dir, app_dirs)

        if selected_domain is None:
            raise RuntimeError(
                f"No complete ToolGuard domain found under {domain_dir}"
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
            raise RuntimeError(
                f"No ToolGuard app directories found under {domain_dir}"
            )
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
            self._process_policy_guard(
                policy, tool_name, index, guard_blocks, guard_calls
            )

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
            logger.warning(
                f"Policy '{policy.name}' missing tool_guard for '{tool_name}', skipping"
            )
            return

        guard_func_name = self._extract_guard_function_name(tool_guard.policy_code)
        if not guard_func_name:
            logger.warning(
                f"Could not find guard function in policy code for '{policy.name}', skipping"
            )
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
        
        guard_calls.extend([
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
        ])

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
        normalized = "".join(
            ch if ch.isalnum() else "_" for ch in name.lower()
        ).strip("_")
        
        # Use "tool" as base if normalization results in empty string
        base = normalized if normalized else "tool"
        
        # Add short hash suffix for disambiguation
        name_hash = hashlib.sha256(name.encode()).hexdigest()[:8]
        
        return f"{base}_{name_hash}"

    async def guard_tool_call(
        self,
        app_name: str,
        function_name: str,
        arguments: Dict[str, Any]
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

        # Check if this tool has any guards
        if function_name not in self.tool_to_guards:
            logger.debug(f"No guards registered for tool '{function_name}'")
            return None

        if self._runtime is None:
            logger.warning(
                f"ToolGuard runtime unavailable for guarded tool '{function_name}', "
                "skipping validation"
            )
            return None

        guards = self.tool_to_guards[function_name]
        logger.debug(
            f"Validating tool call '{function_name}' against "
            f"{len(guards)} guard(s) using umbrella runtime"
        )

        try:
            args_obj = SimpleNamespace(**arguments)
            await self._runtime.guard_toolcall(
                tool_name=function_name,
                args=arguments | {"args": args_obj},
                delegate=self.invoker,
            )
        except PolicyViolationException as e:
            error = str(e)
            logger.warning(
                f"Tool guard blocked call to '{function_name}': {error}"
            )
            return error
        except Exception as e:
            logger.error(
                f"Error executing umbrella guard for tool '{function_name}': {e}",
                exc_info=True
            )
            # Fail closed: treat internal guard errors as a violation so a buggy
            # or malformed guard cannot silently bypass policy enforcement.
            return (
                f"Internal guard error for '{function_name}': {e}. "
                "Tool call blocked as a safety precaution."
            )

        logger.debug(f"Tool call '{function_name}' passed all guards")
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
        """Release in-memory ToolGuard runtime resources."""
        if self._runtime is not None:
            try:
                self._runtime.__exit__(None, None, None)
            except Exception:
                logger.exception("Error while shutting down ToolGuard runtime")
            self._runtime = None
        self._initialized = False
        logger.debug("ToolGuardRuntime shutdown complete")