from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from kyncode.tools.base import Tool, ToolResult

# 单次 ReadFile 最多读取的行数上限。默认 2000，允许分块读大文件时一次读多一点，
# 但封顶防止模型传离谱的 limit 一次性读回海量内容（token 级兜底交给
# context 层的 READBACK_TOKEN_LIMIT）。
MAX_READ_LINES = 5000

if TYPE_CHECKING:
    from kyncode.tools.file_state_cache import FileStateCache


class Params(BaseModel):
    file_path: str = Field(
        min_length=1,
        max_length=4096,
        description="Absolute or relative path to the file to read",
    )
    offset: int = Field(
        default=0,
        ge=0,
        description="Line offset to start reading from (0-based)",
    )
    limit: int = Field(
        default=2000,
        ge=1,
        le=MAX_READ_LINES,
        description="Maximum number of lines to read",
    )


class ReadFile(Tool):
    name = "ReadFile"
    description = (
        "Read a file and return its contents with line numbers. Use offset and limit to read "
        "a large file in chunks instead of loading it all at once."
    )
    params_model = Params
    category = "read"

    def __init__(self, file_state_cache: FileStateCache | None = None) -> None:
        self._state_cache = file_state_cache

    async def execute(self, params: Params) -> ToolResult:
        path = Path(params.file_path)
        if not path.exists():
            return ToolResult(
                output=f"Error: file not found: {params.file_path}", is_error=True
            )
        if not path.is_file():
            return ToolResult(
                output=f"Error: not a file: {params.file_path}", is_error=True
            )

        resolved = str(path.resolve())

        try:
            text = path.read_text(encoding="utf-8")
        except Exception as e:
            return ToolResult(output=f"Error reading file: {e}", is_error=True)

        if self._state_cache:
            try:
                mtime_ns = path.stat().st_mtime_ns
                self._state_cache.record(resolved, mtime_ns)
            except OSError:
                pass

        lines = text.splitlines()
        selected = lines[params.offset : params.offset + params.limit]
        numbered = [
            f"{i + params.offset + 1}\t{line}" for i, line in enumerate(selected)
        ]
        return ToolResult(output="\n".join(numbered))
