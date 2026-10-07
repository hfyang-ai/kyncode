from __future__ import annotations

from kyncode.commands.handlers.clear import CLEAR_COMMAND
from kyncode.commands.handlers.compact import COMPACT_COMMAND
from kyncode.commands.handlers.help import HELP_COMMAND
from kyncode.commands.handlers.mcp import MCP_COMMAND
from kyncode.commands.handlers.memory import MEMORY_COMMAND
from kyncode.commands.handlers.plan import PLAN_COMMAND
from kyncode.commands.handlers.rewind import REWIND_COMMAND
from kyncode.commands.handlers.sandbox import SANDBOX_COMMAND
from kyncode.commands.handlers.session import SESSION_COMMAND
from kyncode.commands.handlers.skill import SKILL_COMMAND
from kyncode.commands.handlers.status import STATUS_COMMAND
from kyncode.commands.registry import CommandRegistry

ALL_COMMANDS = [
    HELP_COMMAND,
    COMPACT_COMMAND,
    CLEAR_COMMAND,
    PLAN_COMMAND,
    SESSION_COMMAND,
    MCP_COMMAND,
    MEMORY_COMMAND,
    SANDBOX_COMMAND,
    REWIND_COMMAND,
    STATUS_COMMAND,
    SKILL_COMMAND,
]


def register_all_commands(registry: CommandRegistry) -> None:
    for cmd in ALL_COMMANDS:
        registry.register_sync(cmd)
