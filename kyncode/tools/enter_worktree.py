from __future__ import annotations

import secrets
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from kyncode.tools.base import Tool, ToolResult
from kyncode.worktree.slug import validate_slug

if TYPE_CHECKING:
    from kyncode.worktree.manager import WorktreeManager


class EnterWorktreeParams(BaseModel):
    name: str | None = Field(
        default=None,
        description=(
            'Optional name for the worktree. Each "/"-separated segment may '
            "contain only letters, digits, dots, underscores, and dashes; "
            "max 64 chars total. A random name is generated if not provided."
        ),
    )


class EnterWorktreeTool(Tool):
    name = "EnterWorktree"
    description = (
        "Create an isolated git worktree and switch the session into it, so experimental changes "
        "stay out of the main working directory. Use ExitWorktree to leave it."
    )
    params_model = EnterWorktreeParams
    category = "command"

    def __init__(self, worktree_manager: WorktreeManager) -> None:
        self._manager = worktree_manager

    async def execute(self, params: EnterWorktreeParams) -> ToolResult:
        if self._manager.get_current_session() is not None:
            return ToolResult(output="Already in a worktree session", is_error=True)

        slug = params.name or f"wt-{secrets.token_hex(4)}"

        err = validate_slug(slug)
        if err:
            return ToolResult(output=f"Invalid worktree name: {err}", is_error=True)

        try:
            wt = await self._manager.create(slug)
            session = await self._manager.enter(slug)
        except Exception as e:
            return ToolResult(output=f"Error creating worktree: {e}", is_error=True)

        branch_info = f" on branch {wt.branch}" if wt.branch else ""
        return ToolResult(
            output=(
                f"Created worktree at {session.worktree_path}{branch_info}. "
                "The session is now working in the worktree. "
                "Use ExitWorktree to leave mid-session, or exit the session to be prompted."
            )
        )
