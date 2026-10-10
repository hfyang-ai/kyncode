from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from kyncode.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from kyncode.teams.manager import TeamManager


class TaskUpdateParams(BaseModel):
    task_id: str = Field(description="ID of the task to update")
    status: str | None = Field(
        default=None,
        description="New status: pending, in_progress, completed, or blocked",
    )
    assignee: str | None = Field(default=None, description="New assignee name")
    description: str | None = Field(default=None, description="New task description")
    add_blocks: list[str] | None = Field(
        default=None,
        description="Task IDs to add to the blocked-by-this set (this task now blocks them)",
    )
    add_blocked_by: list[str] | None = Field(
        default=None,
        description="Task IDs to add to the blocks-this set (they now block this task)",
    )


VALID_STATUSES = {"pending", "in_progress", "completed", "blocked"}


class TaskUpdateTool(Tool):
    name = "TaskUpdate"
    description = (
        "Update a task's status, assignee, description, or dependencies. status must be one of "
        "pending/in_progress/completed/blocked. add_blocks/add_blocked_by add (not replace) dependency relations."
    )
    params_model = TaskUpdateParams
    category = "command"

    def __init__(self, team_manager: TeamManager, team_name: str) -> None:
        self._team_manager = team_manager
        self._team_name = team_name

    async def execute(self, params: BaseModel) -> ToolResult:
        p: TaskUpdateParams = params  # type: ignore[assignment]

        if p.status and p.status not in VALID_STATUSES:
            return ToolResult(
                output=f"Invalid status '{p.status}'. Must be one of: {', '.join(sorted(VALID_STATUSES))}",
                is_error=True,
            )

        store = self._team_manager.get_task_store(self._team_name)
        if store is None:
            return ToolResult(
                output=f"Task store not found for team '{self._team_name}'",
                is_error=True,
            )

        task = store.update(
            task_id=p.task_id,
            status=p.status,
            assignee=p.assignee,
            description=p.description,
            add_blocks=p.add_blocks,
            add_blocked_by=p.add_blocked_by,
        )

        if task is None:
            return ToolResult(output=f"Task '{p.task_id}' not found", is_error=True)

        changes: list[str] = []
        if p.status:
            changes.append(f"status → {p.status}")
        if p.assignee is not None:
            changes.append(f"assignee → {p.assignee or '(unassigned)'}")
        if p.description is not None:
            changes.append("description updated")
        if p.add_blocks:
            changes.append(f"blocks += {', '.join(p.add_blocks)}")
        if p.add_blocked_by:
            changes.append(f"blocked_by += {', '.join(p.add_blocked_by)}")

        return ToolResult(
            output=f"Task {task.id} updated: {'; '.join(changes) if changes else 'no changes'}"
        )
