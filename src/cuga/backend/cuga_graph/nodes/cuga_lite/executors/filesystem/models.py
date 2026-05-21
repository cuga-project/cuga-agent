"""Pydantic models shared by the consolidated filesystem tools.

These previously lived in ``opensandbox_executor.py`` and were imported
*upward* by the local/native executors. They are the single source of
truth now; the old modules re-export them for backward compatibility.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class FileEntry(BaseModel):
    name: str
    path: str
    is_dir: bool
    size_bytes: Optional[int] = None


class ListFilesResult(BaseModel):
    sandbox_path: str
    entries: List[FileEntry]


class ReadFileInput(BaseModel):
    path: str = Field(description="Path of the file inside the workspace (relative, or under /workspace).")
    start_line: Optional[int] = Field(
        default=None,
        description="1-based first line to include (inclusive). Omit to start from line 1.",
    )
    end_line: Optional[int] = Field(
        default=None,
        description="1-based last line to include (inclusive). Omit to read through end of file.",
    )
    grep_pattern: Optional[str] = Field(
        default=None,
        description=(
            "Optional Python regex (re.search per line). Only lines that match are returned, "
            "e.g. 'error|warning' or 'TODO|FIXME'."
        ),
    )


class DownloadResult(BaseModel):
    sandbox_path: str = Field(description="Original path inside the workspace/sandbox")
    local_path: str = Field(description="Absolute path of the downloaded file in cuga_workspace")
    size_bytes: int = Field(description="File size in bytes")


class UploadResult(BaseModel):
    local_path: str = Field(description="Source file path that was uploaded")
    sandbox_path: str = Field(description="Destination path inside the workspace/sandbox")
