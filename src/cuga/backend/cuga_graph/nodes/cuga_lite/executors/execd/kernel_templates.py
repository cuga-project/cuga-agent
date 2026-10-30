"""Python source the execd executor runs in the agent's kernel.

The filesystem tools, ``run_command`` and the call-budget wrapper run inside
the sandbox next to the files, so they are sent to the kernel as source. The
host-side helpers they share (``/workspace`` rewrite, ``read_file`` slice and
grep) are sent from their own source, so host and kernel cannot drift.
"""

import re
from typing import Callable

from ..filesystem.paths import normalize_shell_command_paths


# A bare ``/workspace``. ``normalize_shell_command_paths`` rewrites only
# ``/workspace/...`` (to ``./...``) and leaves the root itself, which under
# opensandbox is already the thread's own.
_BARE_WORKSPACE = re.compile(r"(?<![\w./])/workspace(?![\w/])")


def rewrite_workspace_paths(command: str, workspace: str) -> str:
    """Map the agent's ``/workspace`` paths onto the thread's directory.

    The agent sees its workspace as ``/workspace``, while in the sandbox that is
    the parent of every thread's directory. ``/workspace/x`` becomes ``./x``
    (commands run from the thread's directory) and a bare ``/workspace`` the
    directory itself, so ``write_file('/workspace/x.py')`` followed by
    ``run_command('python /workspace/x.py')`` finds the file.

    This runs on the host for the ``run_command`` tool and, as source, in the
    kernel for ``run_command`` called from code, so both read paths the same way.
    It is a convenience, not a boundary: the sandbox confines a thread to its
    directory whatever path a command names.
    """
    return _BARE_WORKSPACE.sub(workspace, normalize_shell_command_paths(command))


def _kernel_function(kernel_name: str, entry: str, prelude: list[str], functions: list[Callable]) -> str:
    """Kernel Python binding ``kernel_name`` to a host function, from its own source.

    The kernel then runs the very code the host tool runs, so the two cannot
    drift. The source runs in a namespace of its own, so only ``kernel_name``
    lands in the kernel's globals.
    """
    import inspect as _inspect
    import textwrap as _textwrap

    source = "\n".join([*prelude, *(_textwrap.dedent(_inspect.getsource(fn)) for fn in functions)])
    return (
        "def _cuga_load_function(_src, _entry):\n"
        "    _ns = {}\n"
        "    exec(_src, _ns)\n"
        "    return _ns[_entry]\n"
        f"{kernel_name} = _cuga_load_function({source!r}, {entry!r})\n"
        "del _cuga_load_function\n"
    )


def _workspace_rewrite_code() -> str:
    """Kernel Python defining ``_cuga_sh_rewrite_paths``: the host's ``/workspace`` rewrite."""
    from ..filesystem import paths as _paths

    return _kernel_function(
        "_cuga_sh_rewrite_paths",
        "rewrite_workspace_paths",
        [
            "import re",
            f"VIRTUAL_WORKSPACE_ROOT = {_paths.VIRTUAL_WORKSPACE_ROOT!r}",
            f"_BARE_WORKSPACE = re.compile({_BARE_WORKSPACE.pattern!r})",
        ],
        [normalize_shell_command_paths, rewrite_workspace_paths],
    )


def _slice_and_grep_code() -> str:
    """Kernel Python defining ``_cuga_slice_and_grep``: the host ``read_file``'s line slice and grep."""
    from ..filesystem.workspace_fs import _apply_slice_and_grep

    return _kernel_function(
        "_cuga_slice_and_grep",
        "_apply_slice_and_grep",
        ["import re", "from typing import Optional"],
        [_apply_slice_and_grep],
    )


# Name of the callback the in-kernel tools charge the call budget through.
CHARGE_NAME = "_cuga_charge_tool_call"


def charged_tools_code(names: list[str]) -> str:
    """Kernel Python wrapping each in-kernel tool so it charges the budget before it runs."""
    lines = [
        "def _cuga_charged(_fn):",
        "    async def _cuga_charged_call(*args, **kwargs):",
        f"        await {CHARGE_NAME}(_fn.__name__)",
        "        return await _fn(*args, **kwargs)",
        "    _cuga_charged_call.__name__ = _fn.__name__",
        "    _cuga_charged_call.__doc__ = _fn.__doc__",
        "    return _cuga_charged_call",
        "",
    ]
    for name in names:
        lines.append(f"if {name!r} in globals(): {name} = _cuga_charged({name})")
    return "\n".join(lines) + "\n"


def shell_tool_code(workspace_root: str, index_url: str = "", timeout: int = 120) -> str:
    """Inline ``run_command`` that runs directly in the sandbox.

    Same reasoning as ``filesystem_tools_code``: the agent's kernel already
    runs inside execd at ``workspace_root``, so a shell command belongs right
    here — no HTTP callback to CUGA and back.

    Without this the tool fell through to the generic registry stub, which is
    wrong twice over: ``run_command`` is not a registry API, and the stub is
    ``(**kwargs)`` while the prompt documents ``await run_command("cmd")``,
    so every call died with "takes 0 positional arguments but 1 was given".

    Output contract matches ``format_run_command_output``: stdout on success,
    stdout + "\n[stderr]\n" + stderr on failure.
    """
    return f'''
import asyncio as _cuga_sh_aio
import importlib as _cuga_sh_importlib
import os as _cuga_sh_os
import re as _cuga_sh_re
import signal as _cuga_sh_signal

_CUGA_SH_ROOT = {workspace_root!r}
_CUGA_SH_VENV = _cuga_sh_os.path.join(_CUGA_SH_ROOT, ".venv")
_CUGA_SH_INDEX = {index_url!r}
# Shorter than the block's own limit, so a hung command is reported here, not as a lost block.
_CUGA_SH_TIMEOUT = {int(timeout)!r}
{_workspace_rewrite_code()}

# Matches `uv pip install` or `pip install` so we can rewrite them to target
# the thread venv explicitly and refresh the kernel's import cache afterwards.
_CUGA_SH_INSTALL_RE = _cuga_sh_re.compile(r"\\buv\\s+pip\\s+install\\b|\\bpip\\s+install\\b")
# Matches an existing `--python ...` argument so we never double-inject it.
_CUGA_SH_PYTHON_ARG_RE = _cuga_sh_re.compile(r"--python\\s+\\S+|--python=\\S+")


def _cuga_sh_rewrite_install(cmd: str) -> str:
    """Inject `--python <venv>/bin/python` into every uv/pip install command.

    `uv pip install` respects `VIRTUAL_ENV` in theory, but under the sandbox image
    `uv` can resolve against the system Python when a project file is present in
    the cwd or when its interpreter discovery runs before the env var is read.
    Passing `--python` makes the target venv unambiguous regardless of any
    discovery heuristics.  Idempotent: skipped when `--python` is already there.
    """
    if not _CUGA_SH_INSTALL_RE.search(cmd) or _CUGA_SH_PYTHON_ARG_RE.search(cmd):
        return cmd
    _python = _cuga_sh_os.path.join(_CUGA_SH_VENV, "bin", "python")
    # Insert --python right after the `uv pip install` / `pip install` token.
    return _CUGA_SH_INSTALL_RE.sub(
        lambda m: m.group(0) + " --python " + _python,
        cmd,
        count=1,
    )


async def run_command(cmd: str) -> str:
    """Run a shell command in the workspace and return its output."""
    _env = dict(_cuga_sh_os.environ)
    _env["VIRTUAL_ENV"] = _CUGA_SH_VENV
    _env["UV_PYTHON"] = _cuga_sh_os.path.join(_CUGA_SH_VENV, "bin", "python")
    _env["PATH"] = _CUGA_SH_VENV + "/bin:" + _env.get("PATH", "")
    if _CUGA_SH_INDEX:
        _env["UV_INDEX_URL"] = _CUGA_SH_INDEX
        _env["PIP_INDEX_URL"] = _CUGA_SH_INDEX
        _env["UV_INDEX_STRATEGY"] = "first-index"
    _cmd = _cuga_sh_rewrite_install(_cuga_sh_rewrite_paths(cmd, _CUGA_SH_ROOT))
    try:
        # Its own process group, so a timeout can stop the whole command:
        # `sleep 8; echo x`, `a && b` or a pipe, not only the /bin/sh around them.
        _proc = await _cuga_sh_aio.create_subprocess_shell(
            _cmd,
            cwd=_CUGA_SH_ROOT,
            env=_env,
            stdout=_cuga_sh_aio.subprocess.PIPE,
            stderr=_cuga_sh_aio.subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as e:
        return "[run_command error] " + str(e)
    try:
        _out, _err = await _cuga_sh_aio.wait_for(_proc.communicate(), _CUGA_SH_TIMEOUT)
    except _cuga_sh_aio.TimeoutError:
        try:
            _cuga_sh_os.killpg(_proc.pid, _cuga_sh_signal.SIGKILL)
        except ProcessLookupError:
            pass
        await _proc.wait()
        return "(command timed out after " + str(_CUGA_SH_TIMEOUT) + "s)\\n[stderr]\\nCommand timed out"
    _out_s = _out.decode("utf-8", "replace")
    _err_s = _err.decode("utf-8", "replace")
    if _proc.returncode != 0:
        # Like the local shell: a failure is always visible, even with nothing on stderr.
        _err_s = (_err_s.rstrip("\\n") + "\\n" if _err_s.strip() else "") + "(exit code " + str(_proc.returncode) + ")"
        return _out_s + "\\n[stderr]\\n" + _err_s
    # After a successful install refresh the kernel's import finder cache so
    # newly installed packages are importable immediately without a restart.
    if _proc.returncode == 0 and _CUGA_SH_INSTALL_RE.search(cmd):
        _cuga_sh_importlib.invalidate_caches()
    return _out_s or "(command completed with no output)"
'''


def filesystem_tools_code(workspace_root: str) -> str:
    """Inline Python definitions for filesystem tools that run directly in the sandbox.

    No HTTP callback to CUGA needed — the agent's kernel already runs inside
    the execd sandbox at ``workspace_root``, so Path operations work directly.
    The signatures match WorkspaceFilesystem so the agent's generated code is identical
    regardless of which backend is active.
    """
    # workspace_root is e.g. /workspace/d57c950e-...
    return f'''
import base64 as _cuga_b64
import fnmatch as _cuga_fnm
import glob as _cuga_glob
import json as _cuga_json
import os as _cuga_os
import ast as _cuga_ast
import textwrap as _cuga_tw
from pathlib import Path as _CugaPath

_CUGA_WS_ROOT = _CugaPath({workspace_root!r})
{_slice_and_grep_code()}

def _cuga_resolve(path):
    raw = str(path or "").strip().replace("\\\\", "/")
    if not raw or raw in ("/workspace", "."):
        return _CUGA_WS_ROOT
    for pfx in ("/workspace/", "/workspace", "/tmp/"):
        if raw.startswith(pfx):
            raw = raw[len(pfx):]
            break
    import posixpath as _pp
    rel = _pp.normpath(raw.lstrip("/"))
    if rel == ".":
        return _CUGA_WS_ROOT
    if rel == ".." or rel.startswith("../"):
        raise ValueError("path escapes workspace: " + path)
    target = (_CUGA_WS_ROOT / rel).resolve()
    base   = _CUGA_WS_ROOT.resolve()
    if target != base and base not in target.parents:
        raise ValueError("path escapes workspace: " + path)
    return target

def _cuga_public(p):
    rel = _cuga_os.path.relpath(str(p), str(_CUGA_WS_ROOT.resolve()))
    return "/workspace" if rel == "." else "/workspace/" + rel.replace(_cuga_os.sep, "/")

def _cuga_validate_py(content, path):
    try:
        _cuga_ast.parse(content)
        return None
    except SyntaxError as e:
        return str(e)

async def write_file(path, content):
    """Write text content into a file in the workspace."""
    import ast as _a, textwrap as _tw
    p = _cuga_resolve(path)
    if str(path).endswith(".py"):
        dedented = _tw.dedent(content)
        err = _cuga_validate_py(dedented, path)
        if err:
            return "[write_file error] " + err + ". Rewrite and retry."
        content = dedented
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return "File written: " + _cuga_public(p) + " (" + str(len(content)) + " chars)"

async def read_file(path, start_line=None, end_line=None, grep_pattern=None):
    """Read a text file from the workspace."""
    p = _cuga_resolve(path)
    if not p.is_file():
        return "[read_file error] File not found: " + str(path)
    text = p.read_text(encoding="utf-8", errors="replace")
    return _cuga_slice_and_grep(text, start_line, end_line, grep_pattern)

async def list_files(path=".", pattern="*"):
    """List files and directories in the workspace as JSON."""
    import json as _j
    p = _cuga_resolve(path)
    if p == _CUGA_WS_ROOT:
        p.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        return "[list_files error] Path not found: " + str(path)
    entries = []
    for child in sorted(p.glob(pattern)):
        entries.append({{
            "name": child.name,
            "path": _cuga_public(child),
            "is_dir": child.is_dir(),
            "size_bytes": child.stat().st_size if child.is_file() else 0,
        }})
    return _j.dumps({{"sandbox_path": str(path), "entries": entries}})

async def make_directory(path):
    """Create a directory in the workspace."""
    p = _cuga_resolve(path)
    p.mkdir(parents=True, exist_ok=True)
    return "Directory created: " + _cuga_public(p)

async def move_file(source, destination):
    """Move a file within the workspace."""
    src = _cuga_resolve(source)
    dst = _cuga_resolve(destination)
    if dst.exists():
        return "[move_file error] Destination already exists: " + str(destination)
    dst.parent.mkdir(parents=True, exist_ok=True)
    _cuga_os.rename(str(src), str(dst))
    return "Moved " + _cuga_public(src) + " to " + _cuga_public(dst)

async def search_files(path, pattern, excludePatterns=None):
    """Search files in the workspace."""
    base = _cuga_resolve(path)
    exclude = excludePatterns or []
    results = []
    if "**" in pattern:
        for m in _cuga_glob.glob(str(base / pattern), recursive=True):
            rel = _cuga_os.path.relpath(m, str(base))
            if not any(
                _cuga_fnm.fnmatch(rel, ex)
                or _cuga_fnm.fnmatch(rel, "**/" + ex)
                or _cuga_fnm.fnmatch(rel, "**/" + ex + "/**")
                for ex in exclude
            ):
                results.append(_cuga_public(_CugaPath(m)))
    else:
        for item in sorted(_cuga_os.listdir(str(base))):
            if _cuga_fnm.fnmatch(item, pattern) and not any(_cuga_fnm.fnmatch(item, ex) for ex in exclude):
                results.append(_cuga_public(base / item))
    return "\\n".join(results)

async def get_file_info(path):
    """Get metadata for a file in the workspace."""
    p = _cuga_resolve(path)
    st = p.stat()
    from datetime import datetime as _dt
    return "\\n".join([
        "path: " + _cuga_public(p),
        "size: " + str(st.st_size),
        "created: " + _dt.fromtimestamp(st.st_ctime).isoformat(),
        "modified: " + _dt.fromtimestamp(st.st_mtime).isoformat(),
        "accessed: " + _dt.fromtimestamp(st.st_atime).isoformat(),
        "isDirectory: " + str(p.is_dir()),
        "isFile: " + str(p.is_file()),
        "permissions: " + oct(st.st_mode)[-3:],
    ])

async def edit_file(path, edits, dryRun=False):
    """Make exact-text edits to a file."""
    p = _cuga_resolve(path)
    if not p.is_file():
        return "[edit_file error] File not found: " + str(path)
    content = p.read_text(encoding="utf-8", errors="replace")
    new_content = content
    applied = []
    for edit in (edits or []):
        old, new = edit.get("oldText", ""), edit.get("newText", "")
        if new_content.count(old) != 1:
            return "[edit_file error] oldText not found exactly once: " + repr(old[:80])
        new_content = new_content.replace(old, new, 1)
        applied.append("- " + repr(old[:40]) + " → " + repr(new[:40]))
    if not dryRun and new_content != content:
        p.write_text(new_content, encoding="utf-8")
    return ("DRY RUN\\n" if dryRun else "") + "\\n".join(applied)


def _cuga_guard(_fn):
    """Return "[<tool> error] <message>" instead of raising, as the host filesystem tools do."""
    async def _cuga_guarded(*args, **kwargs):
        try:
            return await _fn(*args, **kwargs)
        except Exception as _exc:
            # The agent knows its workspace as /workspace, not as the sandbox's own path.
            _msg = str(_exc).replace(str(_CUGA_WS_ROOT.resolve()), "/workspace").replace(str(_CUGA_WS_ROOT), "/workspace")
            return "[" + _fn.__name__ + " error] " + _msg
    _cuga_guarded.__name__ = _fn.__name__
    _cuga_guarded.__doc__ = _fn.__doc__
    return _cuga_guarded


for _cuga_n in ("write_file", "read_file", "list_files", "make_directory", "move_file",
                "search_files", "get_file_info", "edit_file"):
    globals()[_cuga_n] = _cuga_guard(globals()[_cuga_n])
del _cuga_n
'''


__all__ = [
    "CHARGE_NAME",
    "charged_tools_code",
    "filesystem_tools_code",
    "rewrite_workspace_paths",
    "shell_tool_code",
]
