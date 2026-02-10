#!/usr/bin/env python3
"""
Secure MCP Filesystem Server - Python implementation using FastMCP
"""

import os
import sys
import base64
import json
import mimetypes
import argparse
from pathlib import Path
from typing import List, Optional, Dict, Any
from datetime import datetime
import glob
import fnmatch

from mcp.server.fastmcp import FastMCP

# Initialize FastMCP server
mcp = FastMCP(
    "secure-filesystem-server", port=int(os.environ.get("DYNACONF_SERVER_PORTS__FILESYSTEM_MCP", "8112"))
)

# Global allowed directories
allowed_directories: List[str] = []

# Global read-only mode flag - check command line args early for decorator evaluation
read_only_mode: bool = "--read-only" in sys.argv


def normalize_path(p: str) -> str:
    """Normalize a path to use forward slashes and resolve it."""
    return str(Path(p).resolve()).replace('\\', '/')


def expand_home(p: str) -> str:
    """Expand ~ to home directory."""
    return str(Path(p).expanduser())


def set_allowed_directories(dirs: List[str]) -> None:
    """Set the global allowed directories."""
    global allowed_directories
    allowed_directories = dirs


def validate_path(file_path: str) -> str:
    """
    Validate that a path is within allowed directories.
    Returns the normalized, resolved path if valid.
    Raises ValueError if path is outside allowed directories.
    """
    # Expand and resolve the path
    expanded = expand_home(file_path)
    resolved = normalize_path(expanded)

    # Check if path is within any allowed directory
    for allowed_dir in allowed_directories:
        if resolved.startswith(allowed_dir + '/') or resolved == allowed_dir:
            return resolved

    raise ValueError(f"Access denied: {file_path} is outside allowed directories")


def format_size(size_bytes: int) -> str:
    """Format file size in human-readable format."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f}{unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f}PB"


async def get_file_stats(file_path: str) -> Dict[str, Any]:
    """Get detailed file statistics."""
    stats = os.stat(file_path)
    return {
        "size": stats.st_size,
        "created": datetime.fromtimestamp(stats.st_ctime).isoformat(),
        "modified": datetime.fromtimestamp(stats.st_mtime).isoformat(),
        "accessed": datetime.fromtimestamp(stats.st_atime).isoformat(),
        "isDirectory": os.path.isdir(file_path),
        "isFile": os.path.isfile(file_path),
        "permissions": oct(stats.st_mode)[-3:],
    }


async def read_file_content(file_path: str) -> str:
    """Read file content as text."""
    with open(file_path, 'r', encoding='utf-8') as f:
        return f.read()


async def write_file_content(file_path: str, content: str) -> None:
    """Write content to file."""
    # Ensure parent directory exists
    os.makedirs(os.path.dirname(file_path) or '.', exist_ok=True)
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)


async def tail_file(file_path: str, n: int) -> str:
    """Read last n lines of a file."""
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    return ''.join(lines[-n:])


async def head_file(file_path: str, n: int) -> str:
    """Read first n lines of a file."""
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = []
        for i, line in enumerate(f):
            if i >= n:
                break
            lines.append(line)
    return ''.join(lines)


async def apply_file_edits(file_path: str, edits: List[Dict[str, str]], dry_run: bool = False) -> str:
    """Apply edits to a file and return a diff-style result."""
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    original_content = content

    for edit in edits:
        old_text = edit['oldText']
        new_text = edit['newText']

        if old_text not in content:
            raise ValueError(f"Text to replace not found: {old_text[:50]}...")

        # Count occurrences
        count = content.count(old_text)
        if count > 1:
            raise ValueError(f"Text appears {count} times, must be unique: {old_text[:50]}...")

        content = content.replace(old_text, new_text)

    if not dry_run:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)

    # Generate diff
    if original_content == content:
        return "No changes made"

    diff_lines = []
    diff_lines.append(f"--- {file_path}")
    diff_lines.append(f"+++ {file_path}")

    original_lines = original_content.splitlines(keepends=True)
    new_lines = content.splitlines(keepends=True)

    # Simple diff generation
    for i, (old_line, new_line) in enumerate(zip(original_lines, new_lines)):
        if old_line != new_line:
            diff_lines.append(f"- {old_line.rstrip()}")
            diff_lines.append(f"+ {new_line.rstrip()}")

    status = "Dry run - no changes made" if dry_run else "Changes applied successfully"
    return f"{status}\n\n" + '\n'.join(diff_lines)


async def search_files_recursive(
    root_path: str, pattern: str, exclude_patterns: List[str] = None
) -> List[str]:
    """Recursively search for files matching a pattern."""
    if exclude_patterns is None:
        exclude_patterns = []

    results = []

    # Handle glob patterns
    if '**' in pattern:
        # Use glob for recursive patterns
        glob_pattern = os.path.join(root_path, pattern)
        matches = glob.glob(glob_pattern, recursive=True)

        for match in matches:
            rel_path = os.path.relpath(match, root_path)

            # Check exclusions
            excluded = False
            for exclude in exclude_patterns:
                if (
                    fnmatch.fnmatch(rel_path, exclude)
                    or fnmatch.fnmatch(rel_path, f"**/{exclude}")
                    or fnmatch.fnmatch(rel_path, f"**/{exclude}/**")
                ):
                    excluded = True
                    break

            if not excluded:
                results.append(match)
    else:
        # Simple pattern in current directory
        for item in os.listdir(root_path):
            item_path = os.path.join(root_path, item)
            if fnmatch.fnmatch(item, pattern):
                results.append(item_path)

    return results


# Tool definitions using FastMCP decorators


def conditional_tool(func):
    """Conditionally register tool based on read_only_mode flag."""
    if not read_only_mode or func.__name__ == "read_text_file":
        return mcp.tool()(func)
    return func


@conditional_tool
async def read_text_file(path: str, tail: Optional[int] = None, head: Optional[int] = None) -> str:
    """
    Read the complete contents of a file from the file system as text.
    Handles various text encodings and provides detailed error messages if the file cannot be read.
    Use the 'head' parameter to read only the first N lines of a file, or the 'tail' parameter
    to read only the last N lines of a file. Only works within allowed directories.

    Args:
        path: Path to the file to read
        tail: If provided, returns only the last N lines of the file
        head: If provided, returns only the first N lines of the file
    """
    if tail and head:
        raise ValueError("Cannot specify both head and tail parameters simultaneously")

    valid_path = validate_path(path)

    if tail:
        return await tail_file(valid_path, tail)

    if head:
        return await head_file(valid_path, head)

    return await read_file_content(valid_path)


@conditional_tool
async def read_media_file(path: str) -> Dict[str, str]:
    """
    Read an image or audio file. Returns the base64 encoded data and MIME type.
    Only works within allowed directories.

    Args:
        path: Path to the media file
    """
    valid_path = validate_path(path)

    # Determine MIME type
    mime_type, _ = mimetypes.guess_type(valid_path)
    if not mime_type:
        mime_type = "application/octet-stream"

    # Read file as binary and encode to base64
    with open(valid_path, 'rb') as f:
        data = base64.b64encode(f.read()).decode('utf-8')

    return {
        "mimeType": mime_type,
        "data": data,
        "type": "image"
        if mime_type.startswith("image/")
        else "audio"
        if mime_type.startswith("audio/")
        else "blob",
    }


@conditional_tool
async def read_multiple_files(paths: List[str]) -> str:
    """
    Read the contents of multiple files simultaneously. This is more efficient than reading
    files one by one when you need to analyze or compare multiple files. Each file's content
    is returned with its path as a reference. Failed reads for individual files won't stop
    the entire operation. Only works within allowed directories.

    Args:
        paths: Array of file paths to read
    """
    if not paths:
        raise ValueError("At least one file path must be provided")

    results = []
    for file_path in paths:
        try:
            valid_path = validate_path(file_path)
            content = await read_file_content(valid_path)
            results.append(f"{file_path}:\n{content}\n")
        except Exception as e:
            results.append(f"{file_path}: Error - {str(e)}")

    return "\n---\n".join(results)


@conditional_tool
async def write_file(path: str, content: str) -> str:
    """
    Create a new file or completely overwrite an existing file with new content.
    Use with caution as it will overwrite existing files without warning.
    Handles text content with proper encoding. Only works within allowed directories.

    Args:
        path: Path to the file to write
        content: Content to write to the file
    """
    valid_path = validate_path(path)
    await write_file_content(valid_path, content)
    return f"Successfully wrote to {path}"


@conditional_tool
async def edit_file(path: str, edits: List[Dict[str, str]], dryRun: bool = False) -> str:
    """
    Make line-based edits to a text file. Each edit replaces exact line sequences
    with new content. Returns a git-style diff showing the changes made.
    Only works within allowed directories.

    Args:
        path: Path to the file to edit
        edits: List of edit operations, each with 'oldText' and 'newText'
        dryRun: Preview changes using git-style diff format without applying them
    """
    valid_path = validate_path(path)
    return await apply_file_edits(valid_path, edits, dryRun)


@conditional_tool
async def create_directory(path: str) -> str:
    """
    Create a new directory or ensure a directory exists. Can create multiple nested
    directories in one operation. If the directory already exists, this operation
    will succeed silently. Perfect for setting up directory structures for projects
    or ensuring required paths exist. Only works within allowed directories.

    Args:
        path: Path to the directory to create
    """
    valid_path = validate_path(path)
    os.makedirs(valid_path, exist_ok=True)
    return f"Successfully created directory {path}"


@conditional_tool
async def list_directory(path: str) -> str:
    """
    Get a detailed listing of all files and directories in a specified path.
    Results clearly distinguish between files and directories with [FILE] and [DIR]
    prefixes. This tool is essential for understanding directory structure and
    finding specific files within a directory. Only works within allowed directories.

    Args:
        path: Path to the directory to list
    """
    valid_path = validate_path(path)
    entries = os.listdir(valid_path)

    formatted = []
    for entry in sorted(entries):
        entry_path = os.path.join(valid_path, entry)
        prefix = "[DIR]" if os.path.isdir(entry_path) else "[FILE]"
        formatted.append(f"{prefix} {entry}")

    return "\n".join(formatted)


@conditional_tool
async def list_directory_with_sizes(path: str, sortBy: str = "name") -> str:
    """
    Get a detailed listing of all files and directories in a specified path, including sizes.
    Results clearly distinguish between files and directories with [FILE] and [DIR] prefixes.
    Only works within allowed directories.

    Args:
        path: Path to the directory to list
        sortBy: Sort entries by 'name' or 'size' (default: 'name')
    """
    valid_path = validate_path(path)
    entries = os.listdir(valid_path)

    # Collect entry details
    detailed_entries = []
    for entry in entries:
        entry_path = os.path.join(valid_path, entry)
        is_dir = os.path.isdir(entry_path)

        try:
            stats = os.stat(entry_path)
            size = stats.st_size if not is_dir else 0
        except Exception:
            size = 0

        detailed_entries.append({'name': entry, 'is_dir': is_dir, 'size': size})

    # Sort entries
    if sortBy == 'size':
        detailed_entries.sort(key=lambda x: x['size'], reverse=True)
    else:
        detailed_entries.sort(key=lambda x: x['name'])

    # Format output
    formatted = []
    for entry in detailed_entries:
        prefix = "[DIR]" if entry['is_dir'] else "[FILE]"
        name = entry['name'].ljust(30)
        size_str = "" if entry['is_dir'] else format_size(entry['size']).rjust(10)
        formatted.append(f"{prefix} {name} {size_str}")

    # Add summary
    total_files = sum(1 for e in detailed_entries if not e['is_dir'])
    total_dirs = sum(1 for e in detailed_entries if e['is_dir'])
    total_size = sum(e['size'] for e in detailed_entries if not e['is_dir'])

    formatted.append("")
    formatted.append(f"Total: {total_files} files, {total_dirs} directories")
    formatted.append(f"Combined size: {format_size(total_size)}")

    return "\n".join(formatted)


@conditional_tool
async def directory_tree(path: str, excludePatterns: List[str] = None) -> str:
    """
    Get a recursive tree view of files and directories as a JSON structure.
    Each entry includes 'name', 'type' (file/directory), and 'children' for directories.
    Files have no children array, while directories always have a children array
    (which may be empty). The output is formatted with 2-space indentation for readability.
    Only works within allowed directories.

    Args:
        path: Path to the root directory
        excludePatterns: List of patterns to exclude
    """
    if excludePatterns is None:
        excludePatterns = []

    def build_tree(current_path: str, root_path: str) -> List[Dict[str, Any]]:
        valid_path = validate_path(current_path)
        entries = os.listdir(valid_path)
        result = []

        for entry in sorted(entries):
            entry_path = os.path.join(current_path, entry)
            rel_path = os.path.relpath(entry_path, root_path)

            # Check exclusions
            excluded = False
            for pattern in excludePatterns:
                if (
                    fnmatch.fnmatch(rel_path, pattern)
                    or fnmatch.fnmatch(rel_path, f"**/{pattern}")
                    or fnmatch.fnmatch(rel_path, f"**/{pattern}/**")
                ):
                    excluded = True
                    break

            if excluded:
                continue

            is_dir = os.path.isdir(entry_path)
            entry_data = {'name': entry, 'type': 'directory' if is_dir else 'file'}

            if is_dir:
                entry_data['children'] = build_tree(entry_path, root_path)

            result.append(entry_data)

        return result

    root_path = validate_path(path)
    tree_data = build_tree(root_path, root_path)
    return json.dumps(tree_data, indent=2)


@conditional_tool
async def move_file(source: str, destination: str) -> str:
    """
    Move or rename files and directories. Can move files between directories and rename
    them in a single operation. If the destination exists, the operation will fail.
    Works across different directories and can be used for simple renaming within the
    same directory. Both source and destination must be within allowed directories.

    Args:
        source: Source path
        destination: Destination path
    """
    valid_source = validate_path(source)
    valid_dest = validate_path(destination)

    if os.path.exists(valid_dest):
        raise ValueError(f"Destination already exists: {destination}")

    os.rename(valid_source, valid_dest)
    return f"Successfully moved {source} to {destination}"


@conditional_tool
async def search_files(path: str, pattern: str, excludePatterns: List[str] = None) -> str:
    """
    Recursively search for files and directories matching a pattern.
    The patterns should be glob-style patterns that match paths relative to the working directory.
    Use pattern like '*.ext' to match files in current directory, and '**/*.ext' to match
    files in all subdirectories. Returns full paths to all matching items.
    Only searches within allowed directories.

    Args:
        path: Root path to search from
        pattern: Glob pattern to match
        excludePatterns: List of patterns to exclude
    """
    if excludePatterns is None:
        excludePatterns = []

    valid_path = validate_path(path)
    results = await search_files_recursive(valid_path, pattern, excludePatterns)

    if not results:
        return "No matches found"

    return "\n".join(results)


@conditional_tool
async def get_file_info(path: str) -> str:
    """
    Retrieve detailed metadata about a file or directory. Returns comprehensive information
    including size, creation time, last modified time, permissions, and type. This tool is
    perfect for understanding file characteristics without reading the actual content.
    Only works within allowed directories.

    Args:
        path: Path to the file or directory
    """
    valid_path = validate_path(path)
    info = await get_file_stats(valid_path)

    return "\n".join(f"{key}: {value}" for key, value in info.items())


@conditional_tool
async def list_allowed_directories() -> str:
    """
    Returns the list of directories that this server is allowed to access.
    Subdirectories within these allowed directories are also accessible.
    Use this to understand which directories and their nested paths are available
    before trying to access files.
    """
    if not allowed_directories:
        return "No allowed directories configured"

    return "Allowed directories:\n" + "\n".join(allowed_directories)


# Main entry point
def main():
    """Main entry point for the server."""
    parser = argparse.ArgumentParser(
        description="Secure MCP Filesystem Server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--read-only",
        action="store_true",
        help="Expose only the read_text_file tool, hide all other tools",
    )
    parser.add_argument(
        "directories",
        nargs="*",
        help="Allowed directories (at least one required)",
    )

    args = parser.parse_args()

    global read_only_mode
    read_only_mode = args.read_only

    if not args.directories:
        print(
            "Usage: mcp-server-filesystem [--read-only] [allowed-directory] [additional-directories...]",
            file=sys.stderr,
        )
        print("Note: Allowed directories can be provided via:", file=sys.stderr)
        print("  1. Command-line arguments (shown above)", file=sys.stderr)
        print("  2. MCP roots protocol (if client supports it)", file=sys.stderr)
        print(
            "At least one directory must be provided by EITHER method for the server to operate.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Process and validate allowed directories
    dirs = []
    for dir_path in args.directories:
        expanded = expand_home(dir_path)
        absolute = os.path.abspath(expanded)

        try:
            # Resolve symlinks
            resolved = os.path.realpath(absolute)
            normalized = normalize_path(resolved)

            # Validate directory exists and is accessible
            if not os.path.exists(normalized):
                print(f"Error: Directory does not exist: {dir_path}", file=sys.stderr)
                sys.exit(1)

            if not os.path.isdir(normalized):
                print(f"Error: {dir_path} is not a directory", file=sys.stderr)
                sys.exit(1)

            dirs.append(normalized)
        except Exception as e:
            print(f"Error accessing directory {dir_path}: {e}", file=sys.stderr)
            sys.exit(1)

    # Set allowed directories
    set_allowed_directories(dirs)

    print("Secure MCP Filesystem Server running", file=sys.stderr)
    print(f"Allowed directories: {dirs}", file=sys.stderr)
    if read_only_mode:
        print("Read-only mode: Only read_text_file tool is exposed", file=sys.stderr)

    # Run the server
    mcp.run(transport="sse")


if __name__ == "__main__":
    main()
