"""Consolidated runtime filesystem tools (no MCP).

One class — ``WorkspaceFilesystem`` — exposes the LLM-facing filesystem
tool surface and serves both the chat agent and cuga-lite. Storage is
abstracted behind a :class:`FilesystemBackend`:

* ``HostWorkspaceBackend`` (default) — host ``<cwd>/cuga_workspace`` tree,
  per-thread or shared per ``settings.skills.enabled``.
* ``RemoteSandboxBackend`` — opensandbox remote ``files.*`` API.

LLM tools (8): ``read_file``, ``write_file``, ``edit_file``, ``list_files``,
``make_directory``, ``move_file``, ``search_files``, ``get_file_info``.

``download_file`` / ``upload_file`` are callable methods/module functions but
are deliberately NOT returned by :meth:`as_structured_tools` — they are host
↔ sandbox transfer plumbing, not agent tools.
"""

from __future__ import annotations

import re
import textwrap
from pathlib import PurePosixPath
from typing import Any, Callable, List, Optional

from langchain_core.tools import StructuredTool

from .backends import FilesystemBackend, HostWorkspaceBackend
from .models import ReadFileInput


def _apply_slice_and_grep(
    content: str,
    start_line: Optional[int],
    end_line: Optional[int],
    grep_pattern: Optional[str],
) -> str:
    """Shared read_file post-processing (line slice + per-line regex filter)."""
    if start_line is None and end_line is None and grep_pattern is None:
        return content
    lines = content.splitlines()
    n = len(lines)
    if n == 0:
        return "(empty file)"
    s = 1 if start_line is None else max(1, start_line)
    e = n if end_line is None else end_line
    if s > n:
        return f"[read_file] start_line {s} is past end of file ({n} lines)"
    e = max(s, min(e, n))
    try:
        rx = re.compile(grep_pattern) if grep_pattern else None
    except re.error as exc:
        return f"[read_file error] invalid grep_pattern: {exc}"
    out: list[str] = []
    for i in range(s - 1, e):
        line = lines[i]
        if rx is not None and not rx.search(line):
            continue
        out.append(f"{i + 1}|{line}" if rx is not None else line)
    if rx is not None and not out:
        return f"(no lines matched grep_pattern in lines {s}-{e})"
    return "\n".join(out) if out else ""


def _apply_file_edits(content: str, edits: List[dict], path: str, dry_run: bool) -> tuple[str, str]:
    """Return (new_content, diff_message). Raises ValueError on bad edits."""
    original = content
    for edit in edits:
        old_text = edit["oldText"]
        new_text = edit["newText"]
        if old_text not in content:
            raise ValueError(f"Text to replace not found: {old_text[:50]}...")
        count = content.count(old_text)
        if count > 1:
            raise ValueError(f"Text appears {count} times, must be unique: {old_text[:50]}...")
        content = content.replace(old_text, new_text)

    if original == content:
        return content, "No changes made"

    diff_lines = [f"--- {path}", f"+++ {path}"]
    for old_line, new_line in zip(original.splitlines(), content.splitlines()):
        if old_line != new_line:
            diff_lines.append(f"- {old_line}")
            diff_lines.append(f"+ {new_line}")
    status = "Dry run - no changes made" if dry_run else "Changes applied successfully"
    return content, f"{status}\n\n" + "\n".join(diff_lines)


def _is_python_script_path(path: str) -> bool:
    return PurePosixPath(path.replace("\\", "/")).suffix.lower() == ".py"


def _line_indent(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def _strip_block_indent(lines: list[str], block: int) -> list[str]:
    out: list[str] = []
    for line in lines:
        if _line_indent(line) >= block and line[:block].isspace():
            out.append(line[block:])
        else:
            out.append(line)
    return out


def _python_compiles(content: str) -> bool:
    try:
        compile(content, "<script>", "exec")
    except SyntaxError:
        return False
    return True


def _peel_agent_block_indent(content: str) -> str:
    """Strip one block-indent level when imports are at column 0 and the body is indented."""
    lines = content.splitlines()
    non_blank = [(i, _line_indent(ln)) for i, ln in enumerate(lines) if ln.strip()]
    if not non_blank or non_blank[0][1] != 0:
        return content
    later_indents = [ind for _, ind in non_blank[1:] if ind > 0]
    if not later_indents:
        return content
    block = min(later_indents)
    suffix = "\n" if content.endswith("\n") else ""
    return "\n".join(_strip_block_indent(lines, block)) + suffix


def _normalize_python_script_content(content: str) -> str:
    """Strip block-indent agents add inside triple-quoted script strings.

    ``textwrap.dedent`` handles the all-lines-indented case. Agents often put
    imports at column 0 and indent the rest one block level; peel that
    structurally so real syntax errors surface at the correct line.
    """
    content = textwrap.dedent(content).lstrip("\n")
    if _python_compiles(content):
        return content
    return _peel_agent_block_indent(content)


def _validate_python_script(content: str, path: str) -> str | None:
    """Return an error message when *content* is not valid Python, else None."""
    try:
        compile(content, path, "exec")
    except SyntaxError as exc:
        line_no = exc.lineno or "?"
        msg = f"Python syntax error at line {line_no}: {exc.msg}"
        lines = content.splitlines()
        if isinstance(line_no, int) and 1 <= line_no <= len(lines):
            msg += f"\n  >>> {lines[line_no - 1].rstrip()}"
        if "unexpected indent" in (exc.msg or "").lower():
            msg += (
                " When building script strings in a code block, do not indent lines inside "
                '"""...""" — top-level statements must start at column 0.'
            )
        return msg
    return None


class WorkspaceFilesystem:
    """LLM-facing filesystem operations over a pluggable backend."""

    def __init__(self, *, backend: FilesystemBackend, thread_id: Optional[str] = None) -> None:
        self.backend = backend
        self.thread_id = thread_id

    # ---- LLM-facing operations ------------------------------------------- #

    async def read_file(
        self,
        path: str,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
        grep_pattern: Optional[str] = None,
    ) -> str:
        try:
            content = await self.backend.read_text(path, operation="read_file")
        except Exception as exc:
            return f"[read_file error] {exc}"
        return _apply_slice_and_grep(content, start_line, end_line, grep_pattern)

    async def write_file(self, path: str, content: str) -> str:
        try:
            if _is_python_script_path(path):
                content = _normalize_python_script_content(content)
                py_err = _validate_python_script(content, path)
                if py_err:
                    return f"[write_file error] {py_err}. Rewrite and retry."
            display = await self.backend.write_text(path, content, operation="write_file")
            return f"File written: {display} ({len(content)} chars)"
        except Exception as exc:
            return f"[write_file error] {exc}"

    async def edit_file(self, path: str, edits: List[dict], dryRun: bool = False) -> str:
        try:
            content = await self.backend.read_text(path, operation="edit_file")
            new_content, message = _apply_file_edits(content, edits, path, dryRun)
            if not dryRun and new_content != content:
                await self.backend.write_text(path, new_content, operation="edit_file")
            return message
        except Exception as exc:
            return f"[edit_file error] {exc}"

    async def list_files(self, path: str = ".", pattern: str = "*") -> str:
        try:
            result = await self.backend.list_dir(path, pattern)
            return result.model_dump_json()
        except Exception as exc:
            return f"[list_files error] {exc}"

    async def make_directory(self, path: str) -> str:
        try:
            display = await self.backend.mkdir(path)
            return f"Directory created: {display}"
        except Exception as exc:
            return f"[make_directory error] {exc}"

    async def move_file(self, source: str, destination: str) -> str:
        try:
            src, dst = await self.backend.move(source, destination)
            return f"Moved {src} to {dst}"
        except Exception as exc:
            return f"[move_file error] {exc}"

    async def search_files(self, path: str, pattern: str, excludePatterns: Optional[List[str]] = None) -> str:
        try:
            results = await self.backend.search(path, pattern, excludePatterns or [])
            return "\n".join(results) if results else "No matches found"
        except Exception as exc:
            return f"[search_files error] {exc}"

    async def get_file_info(self, path: str) -> str:
        try:
            info = await self.backend.stat(path)
            return "\n".join(f"{k}: {v}" for k, v in info.items())
        except Exception as exc:
            return f"[get_file_info error] {exc}"

    # ---- NON-LLM transfer plumbing (never in as_structured_tools) -------- #

    async def download_file(self, sandbox_path: str, filename: Optional[str] = None) -> str:
        try:
            result = await self.backend.download(sandbox_path, filename)
            return f"File downloaded successfully: {result.model_dump_json()}"
        except Exception as exc:
            return f"[download_file error] {exc}"

    async def upload_file(self, local_path: str, sandbox_path: str) -> str:
        try:
            result = await self.backend.upload(local_path, sandbox_path)
            return f"File uploaded successfully: {result.model_dump_json()}"
        except Exception as exc:
            return f"[upload_file error] {exc}"

    # ---- StructuredTool surface ------------------------------------------ #

    def as_structured_tools(self) -> List[StructuredTool]:
        """The 8 LLM-facing filesystem tools (no download/upload)."""
        return [
            StructuredTool.from_function(
                coroutine=self.read_file,
                name="read_file",
                description=(
                    "Read a text file from the workspace. Pass a relative path "
                    "(e.g. `./output.txt`). Optionally pass start_line and end_line "
                    "(1-based, inclusive) to read a slice, and/or grep_pattern (Python "
                    "regex per line) to filter lines. When grep_pattern is set, matching "
                    "lines are prefixed with 'LINE|'."
                ),
                args_schema=ReadFileInput,
            ),
            StructuredTool.from_function(
                coroutine=self.write_file,
                name="write_file",
                description=(
                    "Write text content into a file in the workspace. Use relative paths "
                    "(e.g. `./script.js`). Overwrites existing files; parent directories "
                    "are created automatically. For `.py` scripts, content is syntax-checked "
                    "before write — top-level lines must start at column 0 (no leading indent "
                    "from triple-quoted strings in your code block)."
                ),
            ),
            StructuredTool.from_function(
                coroutine=self.edit_file,
                name="edit_file",
                description=(
                    "Make exact-text edits to a file. `edits` is a list of "
                    "{oldText, newText}; each oldText must occur exactly once. Returns a "
                    "git-style diff. Pass dryRun=true to preview without writing."
                ),
            ),
            StructuredTool.from_function(
                coroutine=self.list_files,
                name="list_files",
                description=(
                    "List files and directories in the workspace as JSON. Pass a relative "
                    "path (default `.` = workspace root) and an optional glob pattern."
                ),
            ),
            StructuredTool.from_function(
                coroutine=self.make_directory,
                name="make_directory",
                description="Create a directory (and parents) in the workspace.",
            ),
            StructuredTool.from_function(
                coroutine=self.move_file,
                name="move_file",
                description=(
                    "Move or rename a file/directory within the workspace. Fails if the "
                    "destination already exists."
                ),
            ),
            StructuredTool.from_function(
                coroutine=self.search_files,
                name="search_files",
                description=(
                    "Recursively search the workspace for entries matching a glob pattern "
                    "(use `**/*.ext` for recursion). Returns matching relative paths."
                ),
            ),
            StructuredTool.from_function(
                coroutine=self.get_file_info,
                name="get_file_info",
                description=(
                    "Return metadata (size, timestamps, permissions, type) for a file or "
                    "directory in the workspace."
                ),
            ),
        ]


def _make_backend(thread_id: Optional[str], backend: Optional[FilesystemBackend]) -> FilesystemBackend:
    return backend if backend is not None else HostWorkspaceBackend(thread_id)


def create_filesystem_tools(
    thread_id: Optional[str] = None,
    *,
    backend: Optional[FilesystemBackend] = None,
) -> List[StructuredTool]:
    """Return the 8 LLM-facing filesystem StructuredTools.

    Defaults to the host workspace backend (per-thread when skills are
    enabled, shared otherwise). Pass a ``RemoteSandboxBackend`` for the
    opensandbox mode.
    """
    fs = WorkspaceFilesystem(backend=_make_backend(thread_id, backend), thread_id=thread_id)
    return fs.as_structured_tools()


def get_transfer_callables(
    thread_id: Optional[str] = None,
    *,
    backend: Optional[FilesystemBackend] = None,
) -> tuple[Callable[..., Any], Callable[..., Any]]:
    """Return (download_file, upload_file) coroutines — NOT exposed to the LLM."""
    fs = WorkspaceFilesystem(backend=_make_backend(thread_id, backend), thread_id=thread_id)
    return fs.download_file, fs.upload_file


async def download_file(
    thread_id: Optional[str],
    sandbox_path: str,
    filename: Optional[str] = None,
    *,
    backend: Optional[FilesystemBackend] = None,
) -> str:
    fs = WorkspaceFilesystem(backend=_make_backend(thread_id, backend), thread_id=thread_id)
    return await fs.download_file(sandbox_path, filename)


async def upload_file(
    thread_id: Optional[str],
    local_path: str,
    sandbox_path: str,
    *,
    backend: Optional[FilesystemBackend] = None,
) -> str:
    fs = WorkspaceFilesystem(backend=_make_backend(thread_id, backend), thread_id=thread_id)
    return await fs.upload_file(local_path, sandbox_path)
