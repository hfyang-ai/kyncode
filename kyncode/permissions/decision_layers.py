from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from kyncode.permissions.dangerous import DangerousCommandDetector, is_safe_command
from kyncode.permissions.modes import DecisionEffect, PermissionMode, mode_decide
from kyncode.permissions.rules import Rule, RuleEngine, evaluate_rules
from kyncode.permissions.sandbox import PathSandbox

if TYPE_CHECKING:
    from kyncode.tools.base import Tool


_PLAN_MODE_ALLOWED_TOOLS = frozenset(
    {"Agent", "ToolSearch", "AskUserQuestion", "ExitPlanMode"}
)
# mcp_call is deliberately excluded from the plan allow-list: ToolSearch only reads
# schemas, while mcp_call hits external services and may mutate state, so plan mode
# still asks for confirmation through the regular permission rules.

_COMPOUND_SPLIT_RE = re.compile(r"\s*(?:&&|\|\||[;|])\s*")


@dataclass
class Decision:
    effect: DecisionEffect
    reason: str


@dataclass
class DecisionContext:
    """Inputs shared by every decision layer.

    The rule snapshot is resolved lazily and cached here so a single decision reuses
    one snapshot even when compound subcommands are evaluated one by one.
    """

    detector: DangerousCommandDetector
    sandbox: PathSandbox
    rule_engine: RuleEngine
    mode: PermissionMode
    plan_file_path: str = ""
    sandbox_enabled: bool = False
    _snapshot: list[Rule] | None = field(default=None, repr=False)

    def rules(self) -> list[Rule]:
        if self._snapshot is None:
            self._snapshot = self.rule_engine.snapshot()
        return self._snapshot


Layer = Callable[[DecisionContext, "Tool", str], "Decision | None"]


def split_compound_subcommands(content: str) -> list[str]:
    """Split a command on top-level shell separators for per-subcommand rule checks.

    This is intentionally a plain regex split (not the quote-aware splitter used by
    the safe-command analysis): changing it would change permission behavior.
    """
    subcommands = [
        segment.strip()
        for segment in _COMPOUND_SPLIT_RE.split(content)
        if segment.strip()
    ]
    return subcommands or [content]


def is_plan_file(target_path: str, plan_file_path: str) -> bool:
    if not plan_file_path or not target_path:
        return ".kyncode/plans/" in target_path
    try:
        abs_target = os.path.abspath(target_path)
        abs_plan = os.path.abspath(plan_file_path)
        if abs_target == abs_plan:
            return True
    except Exception:
        return False
    if os.path.basename(target_path) == os.path.basename(plan_file_path):
        return True
    return ".kyncode/plans/" in target_path


def plan_mode_layer(ctx: DecisionContext, tool: Tool, content: str) -> Decision | None:
    """Layer 0: plan-mode exceptions (allow-listed tools and plan file writes)."""
    if ctx.mode != PermissionMode.PLAN:
        return None
    if tool.name in _PLAN_MODE_ALLOWED_TOOLS:
        return Decision(effect="allow", reason="Plan mode: allowed tool")
    if (
        tool.name in ("WriteFile", "EditFile")
        and content
        and is_plan_file(content, ctx.plan_file_path)
    ):
        return Decision(effect="allow", reason="Plan mode: plan file write")
    return None


def safe_command_layer(ctx: DecisionContext, tool: Tool, content: str) -> Decision | None:
    """Layer 1: safe read-only commands are auto-allowed."""
    if tool.category == "command" and is_safe_command(content or ""):
        return Decision(effect="allow", reason="Safe read-only command")
    return None


def dangerous_command_layer(
    ctx: DecisionContext, tool: Tool, content: str
) -> Decision | None:
    """Layer 1b: dangerous command blacklist (Bash only)."""
    if tool.category != "command":
        return None
    hit, reason = ctx.detector.detect(content)
    if hit:
        return Decision(effect="deny", reason=f"危险命令拦截: {reason}")
    return None


def sandbox_auto_allow_layer(
    ctx: DecisionContext, tool: Tool, content: str
) -> Decision | None:
    """Layer 1c: OS sandbox auto-allow after the dangerous-command check.

    With kernel-level isolation enabled, command tools no longer need confirmation.
    Compound commands are checked per subcommand so deny/ask rules still apply.
    """
    if not (ctx.sandbox_enabled and tool.category == "command"):
        return None
    has_ask = False
    for subcommand in split_compound_subcommands(content):
        rule_result = evaluate_rules(ctx.rules(), tool.name, subcommand)
        if rule_result == "deny":
            return Decision(effect="deny", reason="权限规则拒绝")
        if rule_result == "ask":
            has_ask = True
    if has_ask:
        return Decision(effect="ask", reason="权限规则要求确认")
    return Decision(effect="allow", reason="OS 沙箱自动放行")


def path_sandbox_layer(ctx: DecisionContext, tool: Tool, content: str) -> Decision | None:
    """Layer 2: path sandbox for file tools."""
    if tool.category not in ("read", "write") or not content:
        return None
    # Protected paths are checked first: writes to permission config or skill
    # definitions are denied in every mode, including bypass.
    if tool.category == "write":
        ok, reason = ctx.sandbox.check_deny_write(content)
        if not ok:
            return Decision(effect="deny", reason=f"受保护路径: {reason}")
    ok, reason = ctx.sandbox.check(content)
    if not ok and ctx.mode != PermissionMode.BYPASS:
        return Decision(effect="ask", reason=f"路径沙箱拦截: {reason}")
    return None


def rules_layer(ctx: DecisionContext, tool: Tool, content: str) -> Decision | None:
    """Layer 3: rule engine match."""
    rule_result = evaluate_rules(ctx.rules(), tool.name, content)
    if rule_result == "allow":
        return Decision(effect="allow", reason="权限规则放行")
    if rule_result == "ask":
        return Decision(effect="ask", reason="权限规则要求确认")
    if rule_result == "deny":
        return Decision(effect="deny", reason="权限规则拒绝")
    return None


def mode_fallback_layer(
    ctx: DecisionContext, tool: Tool, content: str
) -> Decision | None:
    """Layer 4: permission-mode fallback."""
    effect = mode_decide(ctx.mode, tool.category)
    if effect == "allow":
        return Decision(effect="allow", reason=f"权限模式 {ctx.mode.value} 放行")
    if effect == "deny":
        return Decision(effect="deny", reason=f"权限模式 {ctx.mode.value} 拒绝")
    return None


# Order is behavior: each layer short-circuits the chain with its own decision.
LAYERS: tuple[Layer, ...] = (
    plan_mode_layer,
    safe_command_layer,
    dangerous_command_layer,
    sandbox_auto_allow_layer,
    path_sandbox_layer,
    rules_layer,
    mode_fallback_layer,
)


def decide(ctx: DecisionContext, tool: Tool, content: str) -> Decision:
    for layer in LAYERS:
        decision = layer(ctx, tool, content)
        if decision is not None:
            return decision
    # Layer 5: human-in-the-loop confirmation.
    return Decision(effect="ask", reason="需要用户确认")
