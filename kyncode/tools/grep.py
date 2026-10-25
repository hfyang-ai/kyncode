from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, Field

from kyncode.tools.base import SKIP_DIRS, Tool, ToolResult


class Params(BaseModel):
    pattern: str = Field(
        min_length=1, max_length=1024, description="Regex pattern to search for"
    )
    path: str = Field(
        default=".", min_length=1, max_length=4096, description="Base directory to search from"
    )
    include: str = Field(
        default="", description="Glob filter for filenames (e.g. '*.py')"
    )


class Grep(Tool):
    name = "Grep"
    description = (
        "Search file contents with a regex pattern, returning matches as 'line:content' "
        "(or 'file:line:content' when searching a directory). "
        "path may be a single file or a directory; directories are searched recursively "
        "and include filters filenames (e.g. '*.py')."
    )
    params_model = Params
    category = "read"

    async def execute(self, params: Params) -> ToolResult:
        base = Path(params.path)
        if not base.exists():
            return ToolResult(
                output=f"Error: path not found: {params.path}", is_error=True
            )

        try:
            regex = re.compile(params.pattern)
        except re.error as e:
            return ToolResult(output=f"Error: invalid regex: {e}", is_error=True)

        # 单文件：直接检索该文件，返回 line:content（文件已知，无需 file: 前缀）
        if base.is_file():
            return self._grep_file(base, regex)

        glob_pattern = params.include if params.include else "**/*"
        if not glob_pattern.startswith("**/"):
            glob_pattern = "**/" + glob_pattern

        results: list[str] = []
        for file_path in sorted(base.glob(glob_pattern)):
            if not file_path.is_file():
                continue
            if any(part in SKIP_DIRS for part in file_path.parts):
                continue
            try:
                text = file_path.read_text(encoding="utf-8", errors="ignore")
            except (OSError, UnicodeDecodeError):
                continue
            for line_num, line in enumerate(text.splitlines(), 1):
                if regex.search(line):
                    rel = file_path.relative_to(base)
                    results.append(f"{rel}:{line_num}:{line}")

        if not results:
            return ToolResult(output="No matches found.")
        return ToolResult(output="\n".join(results))

    @staticmethod
    def _grep_file(file_path: Path, regex: re.Pattern) -> ToolResult:
        try:
            text = file_path.read_text(encoding="utf-8", errors="ignore")
        except (OSError, UnicodeDecodeError):
            return ToolResult(
                output=f"Error: cannot read file: {file_path}", is_error=True
            )
        results = [
            f"{line_num}:{line}"
            for line_num, line in enumerate(text.splitlines(), 1)
            if regex.search(line)
        ]
        if not results:
            return ToolResult(output="No matches found.")
        return ToolResult(output="\n".join(results))
