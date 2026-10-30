# Code Executor Module

A modular, secure code execution system supporting both local and E2B sandbox environments.

## Directory Structure

```
executors/
├── __init__.py              # Public API exports
├── code_executor.py         # Main CodeExecutor class
├── test_code_executor.py    # Comprehensive test suite
├── README.md                # This file
│
├── common/                  # Shared utilities
│   ├── __init__.py
│   ├── security.py          # SecurityValidator - import validation & filtering
│   ├── code_wrapper.py      # CodeWrapper - async code wrapping
│   ├── variable_utils.py    # VariableUtils - variable management
│   └── restricted_environment.py  # RestrictedEnvironment - safe execution env
│
├── local/                   # Local execution mode
│   ├── __init__.py
│   └── local_executor.py    # LocalExecutor - restricted local execution
│
└── e2b/                     # E2B sandbox mode
    ├── __init__.py
    └── e2b_executor.py      # E2BExecutor - remote sandbox execution
```

## Architecture

### Core Components

#### `CodeExecutor` (code_executor.py)
Main static class providing the public API for code execution.

**Key Method:**
```python
CodeExecutor.eval_with_tools_async(
    code: str,
    _locals: dict[str, Any],
    state: AgentState,
    thread_id: Optional[str] = None,
    apps_list: Optional[List[str]] = None,
    mode: Optional[Literal['local', 'e2b']] = None,
) -> tuple[str, dict[str, Any]]
```

**Parameters:**
- `code`: Python code to execute
- `_locals`: Local variables/context for execution
- `state`: AgentState instance with variables_manager
- `thread_id`: Thread ID for E2B sandbox caching (optional)
- `apps_list`: List of app names for parsing tool names (optional)
- `mode`: Execution mode ('local' or 'e2b'). If None, uses settings.

**Returns:** Tuple of (execution result, new variables dictionary)

### Common Utilities (`common/`)

#### `SecurityValidator` (security.py)
Handles all security validation for code execution.

**Features:**
- Import validation (whitelist/blacklist)
- Wrapped code validation (defense in depth)
- Safe locals filtering
- Global namespace safety assertions

**Constants:**
- `DANGEROUS_IMPORTS`: Blocked modules (os, sys, subprocess, etc.)
- `ALLOWED_IMPORTS`: Whitelisted modules (asyncio, json, pandas, etc.)

#### `CodeWrapper` (code_wrapper.py)
Wraps user code in async execution context.

**Features:**
- Automatic async function wrapping
- Smart print statement injection for expressions
- Locals capture

#### `VariableUtils` (variable_utils.py)
Manages variables during and after execution.

**Features:**
- Serializability checking
- New variable filtering
- Variable reordering (printed vars to end)
- VariablesManager integration

#### `RestrictedEnvironment` (restricted_environment.py)
Creates restricted execution environments for local mode.

**Features:**
- Restricted import function factory
- Safe builtins dictionary
- Restricted globals with pandas support

### Execution Modes

#### Local Mode (`local/`)

**`LocalExecutor`** (local_executor.py)
Executes code locally in a restricted Python environment.

**Features:**
- Restricted import system
- Safe builtins only
- Configurable timeout (default 30s)
- Stdout capture

#### E2B Mode (`e2b/`)

**`E2BExecutor`** (e2b_executor.py)
Executes code in E2B remote sandbox.

**Features:**
- Tool serialization for remote execution
- Variable state management
- Multiple sandbox modes (per-session, single, ephemeral)
- HTTP-based registry tool stubs

**Sandbox Modes:**
- `per-session`: One sandbox per thread_id (cached)
- `single`: One global sandbox (reused)
- `ephemeral`: New sandbox per execution (no caching)

## Usage Examples

### Basic Usage

```python
from cuga.backend.cuga_graph.nodes.cuga_lite.executors import CodeExecutor

result, new_vars = await CodeExecutor.eval_with_tools_async(
    code="x = 5 + 3\nprint(x)",
    _locals={},
    state=agent_state,
)
```

### With Tools

```python
async def my_tool(arg: str) -> str:
    return f"Processed: {arg}"

result, new_vars = await CodeExecutor.eval_with_tools_async(
    code="result = await my_tool('hello')",
    _locals={'my_tool': my_tool},
    state=agent_state,
)
```

### Explicit Mode Selection

```python
# Force local execution
result, new_vars = await CodeExecutor.eval_with_tools_async(
    code="import pandas as pd\ndf = pd.DataFrame({'a': [1,2,3]})",
    _locals={},
    state=agent_state,
    mode='local',
)

# Force E2B execution
result, new_vars = await CodeExecutor.eval_with_tools_async(
    code="x = expensive_computation()",
    _locals={'expensive_computation': my_func},
    state=agent_state,
    mode='e2b',
)
```

### E2B with Caching

```python
result, new_vars = await CodeExecutor.eval_with_tools_async(
    code="x = expensive_computation()",
    _locals={'expensive_computation': my_func},
    state=agent_state,
    thread_id='session-123',  # Reuse sandbox for this thread
    mode='e2b',
)
```

## Security Model

### Local Mode
1. **Import Validation**: AST-based pre-execution scan
2. **Restricted Import**: Runtime import hook blocks dangerous modules
3. **Safe Builtins**: Limited builtin functions (no eval, exec, compile, open)
4. **Filtered Locals**: Dangerous modules removed from context
5. **Assertion Checks**: Final validation before execution

### Enforcing or relaxing the checks
One setting turns the security checks above on or off:

```toml
[execution]
enforce_code_security = true  # default
```

- `true` (default): import allowlist, AST checks on the wrapped code (blocked modules,
  dunder access, `eval`/`exec`), restricted `__import__`, safe builtins, filtered locals,
  globals without `os`/`sys`/`subprocess`, and the check that code uses a tool or variable.
- `false`: all of the above are off. Generated code gets real builtins, `os` and `sys`.
  A warning is logged once per process. Use it only for trusted runs, such as
  reproducing AppWorld benchmark scores.

The syntax check and code wrapping run either way. Skills and benchmark profiles do not
change this setting, and it does not change `[advanced_features] sandbox_mode` (where
code runs). Env override: `DYNACONF_EXECUTION__ENFORCE_CODE_SECURITY=false`.

### E2B Mode
1. **Import Validation**: Same pre-execution scan
2. **Remote Sandbox**: Code runs in isolated E2B container
3. **Tool Serialization**: Only safe tool definitions sent
4. **HTTP-based Tools**: Registry tools accessed via HTTP (no direct system access)

### Isolation model (execd mode)
With `sandbox_mode = "execd"` (or the Manage → Code sandbox toggle), generated Python,
`run_command`, the filesystem tools and the workspace panel run in the remote sandbox
(`execd/`). CUGA talks only to sandbox-api, never to execd directly.

**Between conversations: enforced by the sandbox, not by CUGA.**
- Each thread gets its own execd context: a separate Jupyter kernel (its own OS process
  and Python state) with its own workspace directory, `/workspace/<thread_id>`.
- sandbox-api and execd keep threads apart. From its kernel or from `POST /command`,
  code in one thread cannot read, overwrite, create or list files in another thread's
  workspace, and cannot list `/sandbox` or `/tmp`.
- Code cannot reach the sandbox internals: execd on loopback returns 401, Jupyter
  returns 403, and `EXECD_ACCESS_TOKEN` is not in the kernel environment.
- Code cannot read another kernel's connection file (its ports and HMAC key), so it
  cannot attach to another thread's kernel. A kernel can read only its own.
- This is checked by `scripts/25_test_workspace_isolation.sh` in the sandbox-api repo.
  It calls sandbox-api directly, so use a sandbox-api build where that test passes.
- sandbox-api names the workspace after the thread id it is sent, after mapping every
  character outside `[A-Za-z0-9_-]` to `-`. That would put `a.b` and `a-b` in one
  workspace, so `ExecdExecutor._workspace_name` sends such ids as a prefix plus a hash.
  UUIDs and other ids that the mapping keeps are sent unchanged.
- `POST /command` sends its environment (venv, package index, file-tool operands) in
  `envs`. sandbox-api must support that field, so deploy a sandbox-api with `envs`
  support before this CUGA version, or file tools, installs and skill copies fail.
- Tools do not run in the sandbox. Each tool is a stub that calls back into CUGA
  (`POST /sandbox/tools/call`). Every block has its own id and an HMAC token signed with
  a key that exists only in the CUGA process, so code in one thread cannot call another
  thread's tools. A call that outlives its block is refused.

**Inside one conversation: the same restrictions as the local executor.**
- `SecurityValidator` checks the code before it is sent, as in the other modes.
- In the kernel the block runs in a namespace of its own, built as `LocalExecutor`
  builds it: the same safe builtins (no `eval`, `exec`, `compile`, `getattr`, `vars`,
  `open`), imports limited to `SecurityValidator.ALLOWED_IMPORTS`, and only the block's
  tools and stored variables. CUGA's helpers in the kernel's globals (the tool callback
  client, the in-kernel tools' `os`, the block runner's `sys`) are not in that
  namespace. `exit()` ends the block early and keeps its variables, as under the local
  executor's runner. With `execution.enforce_code_security = false` the block runs in
  the kernel's globals, unrestricted, as relaxed execution does locally.
- Two execd-only checks on top:
  - The tool callback answers only the tools the block names as identifiers. A tool
    reached any other way is not registered for the block, so CUGA refuses it. Tool
    approval looks at the same code.
  - Code is refused before it is sent if it reads a `_cuga*` name its own scope did not
    bind, reads `globals`, `vars`, `eval`, `exec` or `compile` without binding it
    itself, or uses `asyncio` subprocess calls (`create_subprocess_*`,
    `subprocess_shell`, `subprocess_exec`, `asyncio.subprocess`). The bootstrap keeps
    its own names (`os`, `site`, `importlib`, `urllib`) inside a function, so they never
    reach the kernel's globals.
- Like the local executor's, these restrictions run in the same process as the code, so
  they are not a boundary. Treat the sandbox as the boundary: what agent code can do is
  limited by what the sandbox allows (its filesystem, network policy and the tools CUGA
  answers).

**Paths.** The agent sees its workspace as `/workspace`. In the kernel and in shell
commands, `/workspace/x` is rewritten to `./x` and a bare `/workspace` to the thread's
directory, by one function (`rewrite_workspace_paths`) that both `run_command`
variants use. So a file that `write_file('/workspace/x.py')` wrote is found by
`run_command('python /workspace/x.py')`.

**Shared and persistent state.**
- All kernels in the sandbox use one interpreter and the image's site-packages. Each
  thread gets its own venv in its workspace, prepended to `sys.path`, so packages it
  installs shadow the shared ones without affecting other threads.
- `execd_package_index` is exported into the kernel so pip/uv can use it, so agent code
  can read it. Do not put credentials in that URL.
- Egress is denied by the sandbox network policy by default. Allow only the CUGA
  callback (`sandbox_callback_url`) and the package index.
- Deleting a conversation releases its kernel. Turning the sandbox off releases the
  kernels this process holds; kernels from before a restart or on other replicas stay
  until sandbox-api's TTL. Workspace files stay in the sandbox either way.

### Allowed Modules
- `asyncio`, `json`, `pandas`, `numpy`, `statistics`
- `datetime`, `math`, `collections`, `itertools`
- `functools`, `re`, `typing`

### Blocked Modules
- `os`, `sys`, `subprocess`, `pathlib`
- `shutil`, `glob`, `importlib`
- `eval`, `exec`, `compile`, `open`

## Configuration

Execution mode is controlled by settings:

```python
# In settings.toml or environment
[advanced_features]
e2b_sandbox = true  # Enable E2B mode
e2b_sandbox_mode = "per-session"  # or "single" or "ephemeral"

[server_ports]
function_call_host = "http://your-registry-url:8001"  # For E2B registry tools
```

## Error Handling

All errors are caught and formatted with full tracebacks:

```python
result, new_vars = await CodeExecutor.eval_with_tools_async(...)
# If error occurs:
# result = "Error during execution: <error details>\n<traceback>"
# new_vars = {}
```

Timeout errors (30s default):
```
"Error during execution: Execution timed out after 30 seconds"
```

## Variable Management

New variables are automatically:
1. Filtered (only serializable, non-internal)
2. Reordered (printed variables moved to end)
3. Added to VariablesManager
4. Summarized in result output

Example output:
```
8

## New Variables Created:
### x
**Type:** int
**Value:** 8
**Description:** Created during code execution
```

## Testing

Run tests with:
```bash
uv run pytest src/cuga/backend/cuga_graph/nodes/cuga_lite/executors/tests/test_code_executor.py -v
```

All tests include:
- Basic execution
- Variable management
- Async tool execution
- Security validation
- Import restrictions
- Pandas support
- Variable reordering
- Timeout handling
- Expression auto-print
- Mode auto-detection

## Public API

```python
from cuga.backend.cuga_graph.nodes.cuga_lite.executors import CodeExecutor

# Main class-based API (recommended)
await CodeExecutor.eval_with_tools_async(...)

# Legacy function API (for backward compatibility)
from cuga.backend.cuga_graph.nodes.cuga_lite.executors import eval_with_tools_async
await eval_with_tools_async(...)
```

## Design Principles

1. **Separation of Concerns**: Each module has a single, well-defined responsibility
2. **Mode Isolation**: Local and E2B executors are completely separate
3. **Shared Utilities**: Common functionality in the `common/` package
4. **Security First**: Multiple layers of validation and sandboxing
5. **Backward Compatible**: Legacy function wrapper maintains compatibility
6. **Testable**: Comprehensive test suite with 100% coverage of critical paths
7. **Type Safe**: Full type hints for better IDE support and error detection

## Future Enhancements

- [ ] Configurable timeout per execution
- [ ] Custom allowed modules per execution
- [ ] Execution metrics and profiling
- [ ] Async context manager for sandbox lifecycle
- [ ] Variable diff tracking between executions
- [ ] WebAssembly-based local sandbox option
- [ ] Docker-based local sandbox option
