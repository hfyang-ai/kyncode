from __future__ import annotations

from enum import Enum
from typing import Literal

from kyncode.tools.base import ToolCategory

DecisionEffect = Literal["allow", "deny", "ask"]


class PermissionMode(str, Enum):
    DEFAULT = "default"
    ACCEPT_EDITS = "acceptEdits"
    PLAN = "plan"
    BYPASS = "bypassPermissions"


# Fallback decision per tool category when no earlier permission layer
# (dangerous-command check, path sandbox, rule engine) produced a verdict.
#
#   read    - always allowed; reading is non-destructive.
#   write   - ask by default; auto-allowed in acceptEdits/bypass.
#   command - ask unless bypass; OS sandbox may still auto-allow earlier.
#   network - always ask except bypass (external side effects).
_MODE_MATRIX: dict[PermissionMode, dict[ToolCategory, DecisionEffect]] = {
    PermissionMode.DEFAULT: {
        "read": "allow",
        "write": "ask",
        "command": "ask",
        "network": "ask",
    },
    PermissionMode.ACCEPT_EDITS: {
        "read": "allow",
        "write": "allow",
        "command": "ask",
        "network": "ask",
    },
    PermissionMode.PLAN: {
        "read": "allow",
        "write": "ask",
        "command": "ask",
        "network": "ask",
    },
    PermissionMode.BYPASS: {
        "read": "allow",
        "write": "allow",
        "command": "allow",
        "network": "allow",
    },
}


def mode_decide(mode: PermissionMode, category: ToolCategory) -> DecisionEffect:
    """Return the fallback effect for a mode/category pair.

    An unknown mode or category propagates KeyError, as before; callers only
    pass validated enum values.
    """
    return _MODE_MATRIX[mode][category]
