"""
E2B Sandbox Execution Module

Handles code execution in E2B remote sandboxes with caching support.
Accepts pre-wrapped code from sandbox.py to maintain consistency across execution modes.
"""

import ast
import asyncio
import time
from typing import Any, Dict
from langfuse import observe, get_client

from loguru import logger

from cuga.config import settings

langfuse = get_client()

# E2B sandbox imports (optional)
try:
    from e2b_code_interpreter import Sandbox

    E2B_AVAILABLE = True
except ImportError:
    E2B_AVAILABLE = False
    Sandbox = None

# Constant thread ID for "single" mode (uses cache with global ID)
GLOBAL_THREAD_ID = "__global__"


# ============================================================================
# Sandbox Cache (moved from e2b_sandbox_cache.py)
# ============================================================================


class SandboxCacheEntry:
    """Entry in the sandbox cache containing sandbox instance and metadata."""

    def __init__(self, sandbox: "Sandbox", thread_id: str):
        self.sandbox = sandbox
        self.thread_id = thread_id
        self.created_at = time.time()
        self.last_used = time.time()
        self.use_count = 0

    def mark_used(self):
        """Update last used timestamp and increment use count - KEY FIX."""
        self.last_used = time.time()
        self.use_count += 1

    def is_expired_idle(self, idle_ttl: int) -> bool:
        """Check if expired based on idle time - KEY FIX."""
        idle_time = time.time() - self.last_used  # Use last_used, not created_at
        return idle_time > idle_ttl

    def is_expired_age(self, max_age: int) -> bool:
        """Check if expired based on absolute age."""
        if max_age == 0:
            return False  # Disabled
        age = time.time() - self.created_at
        return age > max_age

    def get_age(self) -> float:
        """Get age in seconds since creation."""
        return time.time() - self.created_at

    def get_idle_time(self) -> float:
        """Get idle time in seconds since last use."""
        return time.time() - self.last_used


class E2BSandboxCache:
    """
    Optimized cache manager for E2B sandbox instances.

    Maintains one sandbox per thread_id with lazy cleanup and reactive error handling.
    Performance improvements:
    - Idle-based TTL instead of creation-based
    - Local timestamp checks (no E2B calls during cleanup)
    - Reactive error handling instead of proactive health checks
    - Optional periodic cleanup on create
    """

    _instance = None
    _sandboxes: Dict[str, SandboxCacheEntry] = {}
    _idle_ttl: int = 600  # Idle timeout
    _max_age: int = 86400  # Max age for single mode
    _ttl_buffer: int = 60  # Safety buffer
    _cleanup_on_create: bool = True
    _cleanup_frequency: int = 0
    _create_count: int = 0
    _mode: str = "per-session"

    def __new__(cls):
        """Singleton pattern to ensure one cache instance."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        """Initialize the cache."""
        if not hasattr(self, "_initialized"):
            self._sandboxes = {}
            self._idle_ttl = 600
            self._max_age = 86400
            self._ttl_buffer = 60
            self._cleanup_on_create = True
            self._cleanup_frequency = 0
            self._create_count = 0
            self._mode = "per-session"
            self._initialized = True

    def configure(
        self,
        mode: str = "per-session",
        idle_ttl: int = 600,
        max_age: int = 86400,
        ttl_buffer: int = 60,
        cleanup_on_create: bool = True,
        cleanup_frequency: int = 0,
    ):
        """Configure cache settings from config."""
        self._mode = mode
        self._idle_ttl = idle_ttl
        self._max_age = max_age
        self._ttl_buffer = ttl_buffer
        self._cleanup_on_create = cleanup_on_create
        self._cleanup_frequency = cleanup_frequency
        logger.info(
            f"E2B sandbox cache configured: mode={mode}, idle_ttl={idle_ttl}s, "
            f"max_age={max_age}s, buffer={ttl_buffer}s, cleanup_on_create={cleanup_on_create}, "
            f"cleanup_freq={cleanup_frequency}"
        )

    @observe(as_type="span")
    def get_or_create(self, thread_id: str) -> "Sandbox":
        """
        Get existing sandbox for thread_id or create new one.
        OPTIMIZED: Uses lazy cleanup with local timestamp checks only.

        Args:
            thread_id: Unique identifier for the conversation thread

        Returns:
            E2B Sandbox instance

        Raises:
            RuntimeError: If E2B is not available or sandbox creation fails
        """
        if not E2B_AVAILABLE:
            raise RuntimeError("e2b-code-interpreter package not installed")

        # OPTIMIZATION: Lazy cleanup - only check THIS sandbox (local checks, no E2B calls)
        start = time.time()
        self._lazy_cleanup(thread_id)
        langfuse.update_current_span(metadata={"lazy_cleanup": time.time() - start})

        # Check if we have a valid cached sandbox
        start = time.time()
        if thread_id in self._sandboxes:
            entry = self._sandboxes[thread_id]
            # Valid cached sandbox found - mark as used and return
            entry.mark_used()
            logger.info(
                f"Reusing cached sandbox for thread {thread_id} "
                f"(age: {entry.get_age():.1f}s, uses: {entry.use_count}, "
                f"idle: {entry.get_idle_time():.1f}s)"
            )
            langfuse.update_current_span(metadata={"found": time.time() - start})
            return entry.sandbox

        langfuse.update_current_span(metadata={"not_found": time.time() - start})

        # Create new sandbox
        return self._create_sandbox(thread_id)

    def _remove_sandbox(self, thread_id: str):
        """Remove and cleanup sandbox for given thread_id."""
        if thread_id in self._sandboxes:
            entry = self._sandboxes[thread_id]
            try:
                entry.sandbox.kill()
                logger.debug(f"Killed sandbox for thread {thread_id}")
            except Exception as e:
                logger.debug(f"Error killing sandbox for thread {thread_id}: {e}")
            finally:
                del self._sandboxes[thread_id]

    def _create_sandbox(self, thread_id: str) -> "Sandbox":
        """
        Create new sandbox with retry logic and optional periodic cleanup.
        OPTIMIZED: Adds TTL buffer and runs periodic cleanup on create.
        """
        actual_timeout = self._idle_ttl + self._ttl_buffer

        logger.info(
            f"Creating new E2B sandbox for thread '{thread_id}' "
            f"(idle_ttl: {self._idle_ttl}s, buffer: {self._ttl_buffer}s, actual_timeout: {actual_timeout}s)"
        )

        with langfuse.start_as_current_observation(
            as_type="span",
            name="create-e2b-sandbox",
            input={
                "e2b_sandbox_mode": self._mode,
                "idle_ttl": self._idle_ttl,
                "ttl_buffer": self._ttl_buffer,
                "actual_timeout": actual_timeout,
            },
        ):
            try:
                # Try to create sandbox with retry
                max_retries = 2
                last_error = None

                for attempt in range(max_retries):
                    try:
                        start = time.time()
                        sandbox = Sandbox.create(timeout=actual_timeout)
                        langfuse.update_current_span(metadata={"create": time.time() - start})

                        entry = SandboxCacheEntry(sandbox, thread_id)
                        entry.mark_used()
                        self._sandboxes[thread_id] = entry

                        # Periodic cleanup on create (optional)
                        self._create_count += 1
                        if self._cleanup_on_create:
                            cleanup_start = time.time()
                            self._periodic_cleanup_all()
                            langfuse.update_current_span(
                                metadata={"periodic_cleanup": time.time() - cleanup_start}
                            )
                        elif (
                            self._cleanup_frequency > 0 and self._create_count % self._cleanup_frequency == 0
                        ):
                            cleanup_start = time.time()
                            self._periodic_cleanup_all()
                            langfuse.update_current_span(
                                metadata={"periodic_cleanup_freq": time.time() - cleanup_start}
                            )

                        log_str = f"Successfully created sandbox for thread {thread_id} (total cached: {len(self._sandboxes)})"
                        logger.info(log_str)
                        langfuse.update_current_span(output=log_str)
                        return sandbox

                    except Exception as e:
                        last_error = e
                        if attempt < max_retries - 1:
                            logger.warning(
                                f"Sandbox creation failed (attempt {attempt + 1}/{max_retries}), retrying: {e}"
                            )
                            time.sleep(0.5 * (attempt + 1))  # Exponential backoff
                        else:
                            raise

                # If we get here, all retries failed
                log_str = f"Failed to create E2B sandbox for thread {thread_id} after {max_retries} attempts: {last_error}"
                logger.error(log_str)
                langfuse.update_current_span(output=log_str)
                raise RuntimeError(f"Failed to create E2B sandbox: {last_error}") from last_error

            except Exception as e:
                log_str = f"Failed to create E2B sandbox for thread {thread_id}: {e}"
                logger.error(log_str)
                langfuse.update_current_span(output=log_str)
                raise RuntimeError(f"Failed to create E2B sandbox: {e}") from e

    def _lazy_cleanup(self, thread_id: str):
        """
        Lazy cleanup: only check the requested sandbox.
        OPTIMIZED: All checks are LOCAL (no E2B calls).
        """
        if thread_id not in self._sandboxes:
            return

        entry = self._sandboxes[thread_id]

        # Check idle timeout (local check)
        if entry.is_expired_idle(self._idle_ttl):
            logger.info(
                f"Sandbox {thread_id} expired due to idle timeout "
                f"(idle: {entry.get_idle_time():.1f}s > {self._idle_ttl}s)"
            )
            self._remove_sandbox(thread_id)
            return

        # Check max age for single mode (local check)
        if self._mode == "single" and entry.is_expired_age(self._max_age):
            logger.info(
                f"Sandbox {thread_id} expired due to max age (age: {entry.get_age():.1f}s > {self._max_age}s)"
            )
            self._remove_sandbox(thread_id)
            return

    def _periodic_cleanup_all(self):
        """
        Check ALL sandboxes for expiration.
        OPTIMIZED: Still no E2B calls - only local timestamp checks.
        """
        expired_count = 0
        threads_to_remove = []

        for thread_id, entry in list(self._sandboxes.items()):
            # All checks are LOCAL
            if entry.is_expired_idle(self._idle_ttl):
                threads_to_remove.append(thread_id)
                expired_count += 1
            elif self._mode == "single" and entry.is_expired_age(self._max_age):
                threads_to_remove.append(thread_id)
                expired_count += 1

        # Remove expired sandboxes
        for thread_id in threads_to_remove:
            self._remove_sandbox(thread_id)

        if expired_count > 0:
            logger.info(f"Periodic cleanup removed {expired_count} expired sandboxes")

    def remove(self, thread_id: str):
        """Manually remove sandbox for specific thread_id."""
        if thread_id in self._sandboxes:
            logger.info(f"Manually removing sandbox for thread {thread_id}")
            self._remove_sandbox(thread_id)

    def clear_all(self):
        """Clear all cached sandboxes."""
        logger.info(f"Clearing all cached sandboxes ({len(self._sandboxes)} total)")
        threads = list(self._sandboxes.keys())
        for thread_id in threads:
            self._remove_sandbox(thread_id)

    def execute_with_recovery(self, thread_id: str, code: str, **kwargs):
        """
        Execute code with automatic sandbox recovery on errors.
        OPTIMIZED: Reactive error handling instead of proactive health checks.

        Args:
            thread_id: Thread ID for sandbox caching
            code: Code to execute
            **kwargs: Additional arguments to pass to run_code

        Returns:
            Execution result from sandbox.run_code()
        """
        sandbox = self.get_or_create(thread_id)

        try:
            result = sandbox.run_code(code, **kwargs)
            return result
        except Exception as e:
            # Check if error indicates stale/expired sandbox
            if self._is_sandbox_stale_error(e):
                logger.info(f"Sandbox {thread_id} is stale/expired, replacing and retrying... (error: {e})")
                self._remove_sandbox(thread_id)

                # Retry with new sandbox
                sandbox = self.get_or_create(thread_id)
                result = sandbox.run_code(code, **kwargs)
                return result
            else:
                # Re-raise other errors
                raise

    def _is_sandbox_stale_error(self, error: Exception) -> bool:
        """
        Detect if error indicates sandbox is stale/expired/dead.
        Returns True if we should replace the sandbox and retry.
        """
        error_str = str(error).lower()

        # E2B-specific error indicators
        stale_indicators = [
            "sandbox not found",
            "sandbox expired",
            "sandbox killed",
            "connection timeout",
            "connection refused",
            "sandbox is not running",
            "timeout expired",
            "timed out",
            "session expired",
            "session not found",
        ]

        return any(indicator in error_str for indicator in stale_indicators)

    def get_stats(self) -> Dict:
        """Get cache statistics."""
        stats = {
            "total_sandboxes": len(self._sandboxes),
            "idle_ttl": self._idle_ttl,
            "max_age": self._max_age,
            "ttl_buffer": self._ttl_buffer,
            "mode": self._mode,
            "sandboxes": {},
        }

        for thread_id, entry in self._sandboxes.items():
            stats["sandboxes"][thread_id] = {
                "age_seconds": entry.get_age(),
                "idle_seconds": entry.get_idle_time(),
                "use_count": entry.use_count,
            }

        return stats


# Global cache instance
_sandbox_cache = E2BSandboxCache()
_cache_configured = False


def get_sandbox_cache() -> E2BSandboxCache:
    """Get the global sandbox cache instance, configured with settings."""
    global _cache_configured

    # Configure cache with settings on first access
    if not _cache_configured:
        _sandbox_cache.configure(
            mode=settings.advanced_features.e2b_sandbox_mode,
            idle_ttl=settings.advanced_features.e2b_sandbox_idle_ttl,
            max_age=settings.advanced_features.e2b_sandbox_max_age,
            ttl_buffer=settings.advanced_features.e2b_sandbox_ttl_buffer,
            cleanup_on_create=settings.advanced_features.e2b_cleanup_on_create,
            cleanup_frequency=settings.advanced_features.e2b_cleanup_frequency,
        )
        _cache_configured = True

    return _sandbox_cache


# ============================================================================
# E2B Execution Functions
# ============================================================================


@observe(as_type="span")
async def execute_in_e2b_sandbox_lite(
    user_code: str,
    context_locals: dict[str, Any] = None,
    thread_id: str = None,
    apps_list: list[str] = None,
    state: Any = None,
) -> tuple[str, dict[str, Any]]:
    """
    Execute code in E2B sandbox with automatic variable/tool serialization for lite mode.

    This high-level function is specific to lite mode. It handles serialization of
    variables and tools from context_locals, creates tool stubs, and combines them
    with the user code before executing in E2B.

    Args:
        user_code: User's wrapped Python code (e.g., with _async_main function)
        context_locals: Dictionary of variables and tools from previous execution
        thread_id: Thread ID for sandbox caching
        apps_list: List of app names (unused in current implementation)
        state: Optional AgentState instance. If provided, uses state.variables_manager.

    Returns:
        Tuple of (stdout_result, parsed_locals)

    Note: This function is specific to lite mode and handles context serialization.
    For other modes (balanced, etc.), use execute_code_in_e2b() directly with
    pre-formatted code from sandbox.py.
    """
    from cuga.backend.cuga_graph.state.agent_state import VariablesManager

    if not E2B_AVAILABLE:
        raise RuntimeError("e2b-code-interpreter package not installed")

    if context_locals is None:
        context_locals = {}

    try:
        # Serialize variables using VariablesManager
        # Use state's variables_manager if provided, otherwise create new one
        if state is not None and hasattr(state, 'variables_manager'):
            var_manager = state.variables_manager
        else:
            var_manager = VariablesManager()
        variables_code = var_manager.get_variables_formatted()

        # Separate simple variables from callable tools
        # Handle both plain callables and StructuredTool objects
        from langchain_core.tools import StructuredTool

        simple_vars = {}
        tool_funcs = {}
        for k, v in context_locals.items():
            if isinstance(v, StructuredTool):
                # It's a StructuredTool object - store it directly
                tool_funcs[k] = v
            elif callable(v) and not k.startswith("_"):
                # It's a plain callable - store it
                tool_funcs[k] = v
            else:
                # It's a simple variable
                simple_vars[k] = v

        # Add simple variables to variables code
        if simple_vars:
            vars_code_from_locals = "\n".join([f"{k} = {repr(v)}" for k, v in simple_vars.items()])
            variables_code = (
                variables_code + "\n" + vars_code_from_locals if variables_code else vars_code_from_locals
            )

        # Create stub functions for tools that redirect to call_api
        # In lite mode, tools are called directly by name like: digital_sales_get_my_accounts_my_accounts_get()
        # We need to create async stubs that call call_api with the correct app and api names
        tools_code = ""
        for tool_name, tool_obj in tool_funcs.items():
            # Check if this is a StructuredTool object
            is_structured_tool = isinstance(tool_obj, StructuredTool)

            # Parse tool name to extract app_name
            # Format is typically: {app_name}_{api_name}
            # First, try to match against known apps from apps_list
            app_name = None
            if apps_list:
                for known_app in apps_list:
                    if tool_name.startswith(known_app + "_") or tool_name == known_app:
                        app_name = known_app
                        logger.debug(f"Matched tool {tool_name} to app {app_name} from apps_list")
                        break

            # If no match, try heuristic parsing
            if not app_name:
                parts = tool_name.split("_")
                if len(parts) >= 2:
                    # Heuristic: app name is usually 1-2 words at the start
                    # For digital_sales_get_my_accounts_my_accounts_get -> app is "digital_sales"
                    # Look for common API verb patterns
                    api_verbs = ["get", "post", "put", "delete", "create", "update", "list", "fetch"]
                    app_parts = []
                    for i, part in enumerate(parts):
                        if part.lower() in api_verbs:
                            break
                        app_parts.append(part)

                    if app_parts:
                        app_name = "_".join(app_parts)
                    else:
                        app_name = parts[0]  # Fallback to first part
                else:
                    app_name = tool_name  # Single word tool name

            # Try to extract parameter names from the tool's schema
            param_names = []
            try:
                # For StructuredTool objects, check the tool itself for args_schema
                # For plain functions, check if they have args_schema attached
                args_schema = None
                if is_structured_tool:
                    args_schema = getattr(tool_obj, 'args_schema', None)
                    logger.debug(
                        f"Checking schema for StructuredTool {tool_name}: has_args_schema={args_schema is not None}"
                    )
                else:
                    args_schema = getattr(tool_obj, 'args_schema', None)
                    logger.debug(
                        f"Checking schema for function {tool_name}: has_args_schema={args_schema is not None}"
                    )

                if args_schema:
                    logger.debug(f"  args_schema type: {type(args_schema)}")
                    logger.debug(f"  has model_fields: {hasattr(args_schema, 'model_fields')}")
                    logger.debug(f"  has __fields__: {hasattr(args_schema, '__fields__')}")
                    # Get field names from Pydantic model
                    if hasattr(args_schema, 'model_fields'):
                        param_names = list(args_schema.model_fields.keys())
                        logger.info(f"Extracted param_names from model_fields for {tool_name}: {param_names}")
                    elif hasattr(args_schema, '__fields__'):
                        param_names = list(args_schema.__fields__.keys())
                        logger.info(f"Extracted param_names from __fields__ for {tool_name}: {param_names}")
                else:
                    logger.warning(f"No args_schema found for {tool_name}")
            except Exception as e:
                logger.error(f"Could not extract param names for {tool_name}: {e}", exc_info=True)

            # Generate stub that accepts both positional and keyword arguments
            # and maps positional args to parameter names
            if param_names:
                logger.info(f"Generating stub WITH positional args for {tool_name}, params={param_names}")
                stub = f"""
async def {tool_name}(*args, **kwargs):
    \"\"\"Stub for {tool_name} - calls via registry API\"\"\"
    # Parameter names: {param_names}
    param_names = {param_names}
    all_kwargs = dict(kwargs)
    # Map positional arguments to parameter names
    for i, arg in enumerate(args):
        if i < len(param_names):
            all_kwargs[param_names[i]] = arg
    return await call_api("{app_name}", "{tool_name}", all_kwargs)
"""
            else:
                # Fallback: no schema info, just pass kwargs
                logger.warning(f"Generating stub WITHOUT positional args for {tool_name} (no param_names)")
                stub = f"""
async def {tool_name}(**kwargs):
    \"\"\"Stub for {tool_name} - calls via registry API\"\"\"
    return await call_api("{app_name}", "{tool_name}", kwargs)
"""
            tools_code += stub

        # Get function_call_host for E2B (needs publicly accessible URL)
        from cuga.config import settings

        function_call_url = getattr(settings.server_ports, "function_call_host", None)
        if not function_call_url:
            function_call_url = getattr(settings.server_ports, "registry_host", None)
        if not function_call_url:
            logger.error(
                "E2B sandbox (lite mode) requires a publicly accessible URL. "
                "Please set 'function_call_host' or 'registry_host' in settings.toml."
            )
            function_call_url = "http://localhost:8001"

        # Get trajectory path for call_api
        from cuga.backend.activity_tracker.tracker import ActivityTracker
        from urllib.parse import quote

        tracker = ActivityTracker()
        trajectory_path = quote(tracker.get_current_trajectory_path())

        # Add call_api helper for registry tools (HTTP client)
        call_api_helper = f"""
# HTTP client for calling registry tools
import asyncio
import json
import urllib.request
import urllib.error

async def call_api(app_name, api_name, args=None):
    \"\"\"Call registry API tool via HTTP.\"\"\"
    if args is None:
        args = {{}}

    url = "{function_call_url}/functions/call?trajectory_path={trajectory_path}"
    headers = {{
        "accept": "application/json",
        "Content-Type": "application/json"
    }}
    payload = {{
        "function_name": api_name,
        "app_name": app_name,
        "args": args
    }}

    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers=headers, method='POST')

    loop = asyncio.get_event_loop()

    def _sync_call():
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                response_data = response.read().decode('utf-8')
                try:
                    response_data = json.loads(response_data)
                except Exception as e:
                    pass
                return response_data
        except urllib.error.HTTPError as e:
            print(e)
            raise Exception(f"HTTP Error: {{e.code}} - {{e.reason}}")
        except urllib.error.URLError as e:
            print(e)
            raise Exception(f"URL Error: {{e.reason}}")

    return await loop.run_in_executor(None, _sync_call)
"""

        # Build complete code with E2B-compatible structure
        # int() cast: the value is substituted into a code template sent to
        # the e2b sandbox. A non-int (e.g. a misconfigured env var coerced
        # to string) would inject syntactically broken Python and surface as
        # a NameError at sandbox runtime — fail early with a clear TypeError
        # here instead.
        sandbox_timeout = int(settings.advanced_features.sandbox_execution_timeout)
        complete_code = f"""
{call_api_helper}

# Tool function stubs
{tools_code}

# Variables from previous execution
{variables_code}

{user_code}

# Execute and capture locals
async def main():
    _result_locals = await asyncio.wait_for(_async_main(), timeout={sandbox_timeout})
    print("!!!===!!!")
    print(_result_locals)

if __name__ == "__main__":
    await main()
"""

        # Execute using the low-level E2B executor
        raw_output = await execute_code_in_e2b(
            code_content=complete_code,
            thread_id=thread_id,
        )

        # Parse the output to extract result and locals
        if "!!!===!!!" in raw_output:
            result, locals_str = raw_output.split("!!!===!!!", 1)
            result = result.strip()

            # Parse locals from output
            result_locals = {}
            lines = locals_str.strip().split('\n')
            for line in reversed(lines):
                if line.strip().startswith('{'):
                    try:
                        result_locals = ast.literal_eval(line.strip())
                        break
                    except (ValueError, SyntaxError):
                        continue

            return result, result_locals
        else:
            # No delimiter found, return full output
            return raw_output, {}

    except Exception as e:
        error_msg = f"E2B sandbox execution failed: {e}"
        logger.error(error_msg)
        raise RuntimeError(error_msg) from e


@observe(as_type="span")
async def execute_code_in_e2b(
    code_content: str,
    thread_id: str = None,
) -> str:
    """Execute pre-wrapped code in E2B remote sandbox (async).

    This is the low-level E2B execution function used by all modes (lite, balanced, etc.).
    It accepts code that has already been wrapped and prepared by the caller.
    It does NOT do any additional wrapping or code generation - it executes the code as-is.

    Args:
        code_content: Complete Python code ready for execution (includes preamble,
            variables, and wrapped user code)
        thread_id: Thread ID for sandbox caching (if None, creates ephemeral sandbox)

    Returns:
        stdout output as string

    Raises:
        RuntimeError: If E2B execution fails
    """
    if not E2B_AVAILABLE:
        raise RuntimeError("e2b-code-interpreter package not installed")

    try:
        logger.debug("Executing code in E2B sandbox")

        # Debug: Print the complete code being sent to E2B
        logger.info("=" * 80)
        logger.info("CODE SENT TO E2B SANDBOX:")
        logger.info("=" * 80)
        logger.info(code_content)
        logger.info("=" * 80)

        # Get or create sandbox based on thread_id and e2b_sandbox_mode
        loop = asyncio.get_event_loop()
        sandbox_mode = settings.advanced_features.e2b_sandbox_mode

        if sandbox_mode == "per-session" and thread_id:
            # Use cached sandbox for this thread
            cache = get_sandbox_cache()
            sandbox = cache.get_or_create(thread_id)
            logger.debug(f"Executing in E2B sandbox {sandbox.sandbox_id} for thread {thread_id}")
            start = time.time()
            execution = await loop.run_in_executor(None, sandbox.run_code, code_content)
            langfuse.update_current_span(metadata={"run_code": time.time() - start})
        elif sandbox_mode == "single":
            # Use single global sandbox (via cache with constant thread_id)
            cache = get_sandbox_cache()
            sandbox = cache.get_or_create(GLOBAL_THREAD_ID)
            logger.debug(f"Executing in global E2B sandbox {sandbox.sandbox_id}")
            start = time.time()
            execution = await loop.run_in_executor(None, sandbox.run_code, code_content)
            langfuse.update_current_span(metadata={"run_code": time.time() - start})
        else:
            # Create ephemeral sandbox (no caching) - default mode (per-call)
            ttl = (
                settings.advanced_features.e2b_sandbox_idle_ttl
                + settings.advanced_features.e2b_sandbox_ttl_buffer
            )
            logger.debug(f"Creating ephemeral E2B sandbox (per-call mode, timeout: {ttl}s)")
            with langfuse.start_as_current_observation(
                as_type="span",
                name="create-e2b-sandbox",
                input={"e2b_sandbox_mode": settings.advanced_features.e2b_sandbox_mode},
            ):
                start = time.time()
                sandbox = Sandbox.create(timeout=ttl)
                metadata = {"create": time.time() - start}
                start = time.time()
                execution = await loop.run_in_executor(None, sandbox.run_code, code_content)
                metadata["run_code"] = time.time() - start
                langfuse.update_current_span(
                    metadata=metadata,
                    output=f"Created ephemeral E2B sandbox (per-call mode, timeout: {ttl}s)",
                )
        # Log execution details
        logger.debug(f"E2B execution completed - has error: {execution.error is not None}")
        if execution.logs.stdout:
            logger.debug(f"E2B stdout: {execution.logs.stdout}")
        if execution.logs.stderr:
            logger.debug(f"E2B stderr: {execution.logs.stderr}")

        # Check for execution errors
        if execution.error:
            error_msg = f"E2B execution error: {execution.error.name} - {execution.error.value}"
            logger.error(error_msg)
            # Include stderr if available
            if execution.logs.stderr:
                error_msg += f"\nStderr: {chr(10).join(execution.logs.stderr)}"
            langfuse.update_current_span(output=error_msg)
            return error_msg

        # Return stdout
        stdout_output = "\n".join(execution.logs.stdout)

        # Include stderr in output if present (warnings, etc.)
        if execution.logs.stderr:
            stderr_output = "\n".join(execution.logs.stderr)
            if stderr_output:
                logger.warning(f"E2B stderr output: {stderr_output}")

        if not stdout_output:
            logger.warning("E2B execution completed but produced no stdout output")

        langfuse.update_current_span(output=stdout_output)

        return stdout_output

    except Exception as e:
        error_msg = f"E2B sandbox execution failed: {e}"
        logger.error(error_msg)
        import traceback

        logger.error(traceback.format_exc())
        return error_msg
