"""Shared metadata for the LLM-facing filesystem tools (no transfer helpers)."""

FILESYSTEM_TOOL_DESCRIPTIONS: dict[str, str] = {
    'read_file': "Read a text file from the workspace. Pass a relative path "
    "(e.g. `./output.txt`). Optionally pass start_line and end_line "
    "(1-based, inclusive) to read a slice, and/or grep_pattern (Python "
    "regex per line) to filter lines. When grep_pattern is set, matching "
    "lines are prefixed with 'LINE|'.",
    'write_file': "Write text content into a file in the workspace. Use relative paths "
    "(e.g. `./script.js`). Overwrites existing files; parent directories "
    "are created automatically. For `.py` scripts, content is syntax-checked "
    "before write — top-level lines must start at column 0 (no leading indent "
    "from triple-quoted strings in your code block).",
    'edit_file': "Make exact-text edits to a file. `edits` is a list of "
    "{oldText, newText}; each oldText must occur exactly once. Returns a "
    "git-style diff. Pass dryRun=true to preview without writing.",
    'list_files': "List files and directories in the workspace as JSON. Pass a relative "
    "path (default `.` = workspace root) and an optional glob pattern.",
    'make_directory': "Create a directory (and parents) in the workspace.",
    'move_file': "Move or rename a file/directory within the workspace. Fails if the "
    "destination already exists.",
    'search_files': "Recursively search the workspace for entries matching a glob pattern "
    "(use `**/*.ext` for recursion). Returns a single newline-separated "
    "string of matching relative paths (one per line) — NOT a list. Empty "
    "string means no matches; use `.splitlines()` to get individual paths, "
    "and check with `if not result:` rather than indexing into it.",
    'get_file_info': "Return metadata (size, timestamps, permissions, type) for a file or "
    "directory in the workspace.",
}

FILESYSTEM_TOOL_NAMES = tuple(FILESYSTEM_TOOL_DESCRIPTIONS)
