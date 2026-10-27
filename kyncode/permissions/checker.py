from __future__ import annotations

import json
import logging
from typing import Any

from kyncode.permissions.dangerous import DangerousCommandDetector
from kyncode.permissions.decision_layers import (
    Decision,
    DecisionContext,
    decide,
    is_plan_file,
)
from kyncode.permissions.modes import PermissionMode
from kyncode.permissions.rules import RuleEngine, extract_content
from kyncode.permissions.sandbox import PathSandbox
from kyncode.tools.base import Tool
from kyncode.tools.redact import redact_sensitive
from kyncode.tracing import TRACE_ARGS_LIMIT, current_agent_id, truncate

log = logging.getLogger(__name__)

__all__ = ["Decision", "PermissionChecker"]


class PermissionChecker:
    def __init__(
        self,
        detector: DangerousCommandDetector,
        sandbox: PathSandbox,
        rule_engine: RuleEngine,
        mode: PermissionMode = PermissionMode.DEFAULT,
        sandbox_enabled: bool = False,
    ) -> None:
        self.detector = detector
        self.sandbox = sandbox
        self.rule_engine = rule_engine
        self.mode = mode
        self.plan_file_path: str = ""
        # OS-level sandbox flag: when enabled, command tools may be auto-allowed
        # because the kernel provides the isolation guarantees.
        self.sandbox_enabled = sandbox_enabled

    @staticmethod
    def describe_tool_action(tool_name: str, arguments: dict[str, Any]) -> str:
        """Build a human-readable action description for HITL confirmation."""
        content = extract_content(tool_name, arguments)
        if content:
            return content
        # Fall back to an argument summary when no standard field can be extracted.
        parts = []
        for k, v in arguments.items():
            sv = str(v)
            if len(sv) > 80:
                sv = sv[:77] + "..."
            parts.append(f"{k}={sv}")
        return ", ".join(parts) if parts else tool_name

    def check(self, tool: Tool, arguments: dict[str, Any]) -> Decision:
        decision = self._decide(tool, arguments)
        # Audit log: every decision (allow/deny/ask) is recorded with redacted and
        # truncated arguments, so security audits can trace who decided what, under
        # which mode, and why.
        log.info(
            "permission_check %s",
            redact_sensitive(
                json.dumps(
                    {
                        "agent_id": current_agent_id.get(),
                        "tool": tool.name,
                        "category": tool.category,
                        "mode": self.mode.value,
                        "effect": decision.effect,
                        "reason": decision.reason,
                        "arguments": truncate(
                            json.dumps(arguments, ensure_ascii=False, default=str),
                            TRACE_ARGS_LIMIT,
                        ),
                    },
                    ensure_ascii=False,
                    default=str,
                )
            ),
        )
        return decision

    def _decide(self, tool: Tool, arguments: dict[str, Any]) -> Decision:
        content = extract_content(tool.name, arguments)
        ctx = DecisionContext(
            detector=self.detector,
            sandbox=self.sandbox,
            rule_engine=self.rule_engine,
            mode=self.mode,
            plan_file_path=self.plan_file_path,
            sandbox_enabled=self.sandbox_enabled,
        )
        return decide(ctx, tool, content)

    def _is_plan_file(self, target_path: str) -> bool:
        return is_plan_file(target_path, self.plan_file_path)
