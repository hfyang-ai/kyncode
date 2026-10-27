from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from kyncode.permissions.checker import Decision, PermissionChecker
from kyncode.permissions.dangerous import DangerousCommandDetector
from kyncode.permissions.decision_layers import (
    LAYERS,
    DecisionContext,
    decide,
    is_plan_file,
    split_compound_subcommands,
)
from kyncode.permissions.modes import PermissionMode
from kyncode.permissions.rules import Rule, RuleEngine, parse_rule
from kyncode.permissions.sandbox import PathSandbox
from kyncode.tools.base import Tool, ToolResult


class _FakeTool(Tool):
    def __init__(self, name: str = "Fake", category: str = "read") -> None:
        self.name = name
        self.category = category  # type: ignore[assignment]
        self.description = "fake"
        self.params_model = None  # type: ignore[assignment]

    async def execute(self, params: Any) -> ToolResult:  # pragma: no cover
        return ToolResult(output="")


class _CountingRuleEngine(RuleEngine):
    def __init__(self, rules: list[Rule] | None = None) -> None:
        super().__init__()
        self.calls = 0
        self._rules = rules or []

    def snapshot(self) -> list[Rule]:
        self.calls += 1
        return list(self._rules)


def _ctx(
    *,
    mode: PermissionMode = PermissionMode.DEFAULT,
    sandbox: PathSandbox | None = None,
    rule_engine: RuleEngine | None = None,
    plan_file_path: str = "",
    sandbox_enabled: bool = False,
) -> DecisionContext:
    tmpdir = Path(tempfile.mkdtemp())
    return DecisionContext(
        detector=DangerousCommandDetector(),
        sandbox=sandbox or PathSandbox(str(tmpdir)),
        rule_engine=rule_engine or RuleEngine(),
        mode=mode,
        plan_file_path=plan_file_path,
        sandbox_enabled=sandbox_enabled,
    )


def test_layer_chain_order_is_explicit() -> None:
    names = [layer.__name__ for layer in LAYERS]
    assert names == [
        "plan_mode_layer",
        "safe_command_layer",
        "dangerous_command_layer",
        "sandbox_auto_allow_layer",
        "path_sandbox_layer",
        "rules_layer",
        "mode_fallback_layer",
    ]


def test_plan_mode_allowed_tool_short_circuits_before_rules() -> None:
    engine = _CountingRuleEngine()
    ctx = _ctx(mode=PermissionMode.PLAN, rule_engine=engine)
    decision = decide(ctx, _FakeTool("Agent", "command"), "ls")
    assert decision == Decision(effect="allow", reason="Plan mode: allowed tool")
    assert engine.calls == 0


def test_plan_mode_plan_file_write_allows() -> None:
    ctx = _ctx(mode=PermissionMode.PLAN, plan_file_path="/ws/.kyncode/plans/p.md")
    tool = _FakeTool("WriteFile", "write")
    decision = decide(ctx, tool, "/ws/.kyncode/plans/p.md")
    assert decision == Decision(effect="allow", reason="Plan mode: plan file write")


def test_plan_mode_other_write_still_falls_through() -> None:
    ctx = _ctx(mode=PermissionMode.PLAN)
    decision = decide(ctx, _FakeTool("WriteFile", "write"), "/ws/a.txt")
    assert decision.effect == "ask"


def test_safe_command_allows() -> None:
    ctx = _ctx()
    decision = decide(ctx, _FakeTool("Bash", "command"), "ls -la")
    assert decision == Decision(effect="allow", reason="Safe read-only command")


def test_dangerous_command_denies_with_reason() -> None:
    ctx = _ctx()
    decision = decide(ctx, _FakeTool("Bash", "command"), "rm -rf /")
    assert decision.effect == "deny"
    assert decision.reason.startswith("危险命令拦截: ")


def test_sandbox_auto_allow_checks_each_subcommand() -> None:
    rule = parse_rule("Bash(npm run deploy)", "deny")
    ctx = _ctx(rule_engine=_CountingRuleEngine([rule]), sandbox_enabled=True)
    decision = decide(ctx, _FakeTool("Bash", "command"), "echo ok && npm run deploy")
    assert decision == Decision(effect="deny", reason="权限规则拒绝")


def test_sandbox_auto_allow_respects_ask_rules() -> None:
    rule = parse_rule("Bash(git push origin main)", "ask")
    ctx = _ctx(rule_engine=_CountingRuleEngine([rule]), sandbox_enabled=True)
    decision = decide(ctx, _FakeTool("Bash", "command"), "git push origin main")
    assert decision == Decision(effect="ask", reason="权限规则要求确认")


def test_sandbox_auto_allow_uses_single_snapshot_for_subcommands() -> None:
    engine = _CountingRuleEngine()
    ctx = _ctx(rule_engine=engine, sandbox_enabled=True)
    decision = decide(
        ctx,
        _FakeTool("Bash", "command"),
        "npm test && npm run build && npm run lint",
    )
    assert decision == Decision(effect="allow", reason="OS 沙箱自动放行")
    assert engine.calls == 1


def test_protected_path_denied_even_in_bypass() -> None:
    root = Path(tempfile.mkdtemp())
    ctx = _ctx(mode=PermissionMode.BYPASS, sandbox=PathSandbox(str(root)))
    decision = decide(
        ctx, _FakeTool("WriteFile", "write"), str(root / ".kyncode/config.yaml")
    )
    assert decision.effect == "deny"
    assert decision.reason.startswith("受保护路径: ")


def test_path_outside_sandbox_asks() -> None:
    ctx = _ctx()
    decision = decide(ctx, _FakeTool("ReadFile", "read"), "/etc/passwd")
    assert decision.effect == "ask"
    assert decision.reason.startswith("路径沙箱拦截: ")


def test_rules_layer_effects() -> None:
    allow = _ctx(rule_engine=_CountingRuleEngine([parse_rule("Bash(npm *)", "allow")]))
    assert decide(allow, _FakeTool("Bash", "command"), "npm test") == Decision(
        effect="allow", reason="权限规则放行"
    )
    deny = _ctx(rule_engine=_CountingRuleEngine([parse_rule("Bash(npm *)", "deny")]))
    assert decide(deny, _FakeTool("Bash", "command"), "npm test") == Decision(
        effect="deny", reason="权限规则拒绝"
    )


def test_mode_fallback_reasons() -> None:
    root = Path(tempfile.mkdtemp())
    accept = _ctx(mode=PermissionMode.ACCEPT_EDITS, sandbox=PathSandbox(str(root)))
    assert decide(
        accept, _FakeTool("WriteFile", "write"), str(root / "x.txt")
    ) == Decision(effect="allow", reason="权限模式 acceptEdits 放行")

    bypass = _ctx(mode=PermissionMode.BYPASS)
    assert decide(bypass, _FakeTool("Bash", "command"), "npm test") == Decision(
        effect="allow", reason="权限模式 bypassPermissions 放行"
    )

    ask_ctx = _ctx(mode=PermissionMode.DEFAULT)
    assert decide(ask_ctx, _FakeTool("Bash", "command"), "npm test") == Decision(
        effect="ask", reason="需要用户确认"
    )


def test_split_compound_subcommands_plain_regex() -> None:
    assert split_compound_subcommands("echo a && echo b; echo c") == [
        "echo a",
        "echo b",
        "echo c",
    ]
    assert split_compound_subcommands("   ") == ["   "]


def test_is_plan_file_variants() -> None:
    assert is_plan_file("/ws/.kyncode/plans/foo.md", "")
    assert is_plan_file("/ws/.kyncode/plans/foo.md", "/ws/.kyncode/plans/bar.md")
    assert is_plan_file("/ws/p.md", "/ws/p.md")
    assert not is_plan_file("/ws/other.md", "/ws/p.md")


def test_permission_checker_decide_matches_layer_chain() -> None:
    root = Path(tempfile.mkdtemp())
    checker = PermissionChecker(
        detector=DangerousCommandDetector(),
        sandbox=PathSandbox(str(root)),
        rule_engine=RuleEngine(),
        mode=PermissionMode.DEFAULT,
    )
    decision = checker._decide(_FakeTool("Bash", "command"), {"command": "ls"})
    assert decision == Decision(effect="allow", reason="Safe read-only command")
