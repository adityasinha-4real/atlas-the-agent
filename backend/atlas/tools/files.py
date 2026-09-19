"""``file_read`` / ``file_write`` — artifact I/O confined to a path jail.

All paths are resolved *inside* a single workspace directory (design doc §1.6:
"hardcode the two policies — path jail for file ops"). Any attempt to escape the
jail via ``..``, an absolute path, or a symlink target outside the root is
rejected as a tool error, never executed.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, Field

from atlas.tools.base import Tool, ToolError, ToolResult

_MAX_WRITE_CHARS = 100_000
_MAX_READ_CHARS = 20_000


class _Workspace:
    """Resolves and validates paths against a fixed root directory."""

    def __init__(self, root: str) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def resolve(self, relative: str) -> Path:
        candidate = (self._root / relative).resolve()
        if candidate != self._root and self._root not in candidate.parents:
            raise ToolError(
                f"Path {relative!r} escapes the workspace jail; access denied."
            )
        return candidate


class FileReadArgs(BaseModel):
    path: str = Field(description="Workspace-relative path to read.", min_length=1)


class FileWriteArgs(BaseModel):
    path: str = Field(description="Workspace-relative path to write.", min_length=1)
    content: str = Field(description="UTF-8 text to write.", max_length=_MAX_WRITE_CHARS)


class FileReadTool(Tool):
    name: ClassVar[str] = "file_read"
    description: ClassVar[str] = (
        "Read a UTF-8 text file from the agent workspace. Paths are relative to "
        "the workspace root; escaping it is not permitted."
    )
    Args: ClassVar[type[BaseModel]] = FileReadArgs

    def __init__(self, workspace: _Workspace) -> None:
        self._ws = workspace

    async def run(self, args: BaseModel) -> ToolResult:
        assert isinstance(args, FileReadArgs)
        path = self._ws.resolve(args.path)
        if not path.exists() or not path.is_file():
            raise ToolError(f"File not found: {args.path!r}.")
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ToolError("File is not valid UTF-8 text.") from exc
        if len(text) > _MAX_READ_CHARS:
            text = text[:_MAX_READ_CHARS] + "\n…[truncated]"
        return ToolResult.success(text)


class FileWriteTool(Tool):
    name: ClassVar[str] = "file_write"
    description: ClassVar[str] = (
        "Write UTF-8 text to a file in the agent workspace, creating parent "
        "directories as needed. Overwrites existing files. Paths are relative to "
        "the workspace root; escaping it is not permitted."
    )
    Args: ClassVar[type[BaseModel]] = FileWriteArgs

    def __init__(self, workspace: _Workspace) -> None:
        self._ws = workspace

    async def run(self, args: BaseModel) -> ToolResult:
        assert isinstance(args, FileWriteArgs)
        path = self._ws.resolve(args.path)
        if path.is_dir():
            raise ToolError(f"Path {args.path!r} is a directory.")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(args.content, encoding="utf-8")
        return ToolResult.success(f"Wrote {len(args.content)} chars to {args.path}.")


def build_file_tools(workspace_dir: str) -> list[Tool]:
    """Construct the file tools sharing one jailed workspace."""
    workspace = _Workspace(workspace_dir)
    return [FileReadTool(workspace), FileWriteTool(workspace)]
