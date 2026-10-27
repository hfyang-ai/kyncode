from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch
from typing import Literal

Effect = Literal["allow", "deny", "ask"]


@dataclass(frozen=True)
class Rule:
    tool_name: str
    pattern: str
    effect: Effect

    def matches(self, tool_name: str, content: str) -> bool:
        if self.tool_name != tool_name:
            return False
        return fnmatch(content, self.pattern)
