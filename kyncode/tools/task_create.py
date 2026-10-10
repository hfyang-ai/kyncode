from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from kyncode.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from kyncode.teams.manager import TeamManager


class TaskCreateParams(BaseModel):
    title: str = Field(description="Short, actionable task title")
    description: str = Field(default="", description="Detailed description of the task")
    assignee: str = Field(
        default="", description="Agent name to assign the task to (empty = unassigned)"
    )
    blocks: list[str] | None = Field(
        default=None,
        description="IDs of tasks this one blocks (they start only after this completes)",
    )
    blocked_by: list[str] | None = Field(
        default=None,
        description="IDs of tasks that block this one (it starts only after they complete)",
    )


class TaskCreateTool(Tool):
    name = "TaskCreate"
    description = (
        "Create a task in the shared team task board. Use blocks/blocked_by to declare "
        "dependencies on other task IDs (see field descriptions)."
    )
    params_model = TaskCreateParams
    category = "command"

    def __init__(
        self, team_manager: TeamManager, team_name: str, agent_name: str = ""
    ) -> None:
        self._team_manager = team_manager
        self._team_name = team_name
        self._agent_name = agent_name

    async def execute(self, params: BaseModel) -> ToolResult:
        p: TaskCreateParams = params  # type: ignore[assignment]

        store = self._team_manager.get_task_store(self._team_name)
        if store is None:
            return ToolResult(
                output=f"Task store not found for team '{self._team_name}'",
                is_error=True,
            )

        task = store.create(
            title=p.title,
            description=p.description,
            assignee=p.assignee,
            blocks=p.blocks,
            blocked_by=p.blocked_by,
            created_by=self._agent_name,
        )

        return ToolResult(
            output=(
                f"Task created:\n"
                f"  ID: {task.id}\n"
                f"  Title: {task.title}\n"
                f"  Status: {task.status}\n"
                f"  Assignee: {task.assignee or '(unassigned)'}"
            )
        )
