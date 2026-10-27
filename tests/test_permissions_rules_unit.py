"""Unit tests for the split ``kyncode.permissions.rules`` package.

Behavior contract for the refactor: the package must expose the same public
symbols and semantics as the former single-module implementation.
"""

from __future__ import annotations

import logging
import os
import tempfile
import time
from pathlib import Path

import pytest
import yaml

from kyncode.permissions.rules import (
    FILE_PATH_TOOLS,
    Rule,
    RuleEngine,
    _pattern_covers,
    _redundancy,
    _suffix_wildcard_prefix,
    evaluate_rules,
    extract_content,
    parse_rule,
    strip_cd_prefix,
)
from kyncode.permissions.rules.model import Effect


def _write_rules(path: Path, entries: list[dict[str, str]]) -> None:
    path.write_text(yaml.dump(entries), encoding="utf-8")


class TestParsing:
    def test_parse_rule(self) -> None:
        rule = parse_rule("Bash(git *)", "deny")
        assert rule == Rule(tool_name="Bash", pattern="git *", effect="deny")

    def test_parse_rule_strips_whitespace(self) -> None:
        rule = parse_rule("  ReadFile(/tmp/*)  ", "allow")
        assert rule.tool_name == "ReadFile"
        assert rule.pattern == "/tmp/*"

    @pytest.mark.parametrize("raw", ["invalid", "Bash()", "Bash(git *) extra"])
    def test_parse_rule_rejects_invalid(self, raw: str) -> None:
        with pytest.raises(ValueError):
            parse_rule(raw, "allow")

    def test_extract_content_standard_fields(self) -> None:
        assert extract_content("Bash", {"command": "ls"}) == "ls"
        assert extract_content("Glob", {"pattern": "**/*.py"}) == "**/*.py"
        assert extract_content("WebFetch", {"url": "https://x.test"}) == "https://x.test"

    def test_extract_content_unknown_tool_is_empty(self) -> None:
        assert extract_content("Unknown", {"command": "ls"}) == ""

    def test_extract_content_mcp_call(self) -> None:
        content = extract_content(
            "mcp_call", {"server": "linear", "tool": "create_issue"}
        )
        assert content == "linear__create_issue"

    def test_file_path_tools(self) -> None:
        assert FILE_PATH_TOOLS == frozenset({"ReadFile", "WriteFile", "EditFile"})

    def test_strip_cd_prefix_multilayer(self) -> None:
        assert strip_cd_prefix("cd /a && cd /b && cmd") == "cmd"

    def test_strip_cd_prefix_keeps_dangling(self) -> None:
        assert strip_cd_prefix("cd /tmp && ") == "cd /tmp && "

    def test_strip_cd_prefix_noop(self) -> None:
        assert strip_cd_prefix("cd /tmp") == "cd /tmp"


class TestMatching:
    def test_rule_matches_only_same_tool(self) -> None:
        rule = Rule(tool_name="Bash", pattern="git *", effect="allow")
        assert rule.matches("Bash", "git status")
        assert not rule.matches("ReadFile", "git status")

    def test_evaluate_deny_beats_ask_and_allow(self) -> None:
        rules = [
            Rule("Bash", "*", "allow"),
            Rule("Bash", "*", "ask"),
            Rule("Bash", "*", "deny"),
        ]
        assert evaluate_rules(rules, "Bash", "anything") == "deny"

    def test_evaluate_ask_beats_allow(self) -> None:
        rules = [Rule("Bash", "*", "allow"), Rule("Bash", "*", "ask")]
        assert evaluate_rules(rules, "Bash", "anything") == "ask"

    def test_evaluate_no_hit(self) -> None:
        assert evaluate_rules([Rule("Bash", "git *", "allow")], "ReadFile", "x") is None

    def test_suffix_wildcard_prefix(self) -> None:
        assert _suffix_wildcard_prefix("git *") == "git "
        assert _suffix_wildcard_prefix("file?.txt*") is None
        assert _suffix_wildcard_prefix("git") is None

    def test_pattern_covers(self) -> None:
        assert _pattern_covers("git *", "git commit *")
        assert _pattern_covers("*", "anything *")
        assert not _pattern_covers("git commit *", "git *")
        assert not _pattern_covers("file?.txt*", "file1.txt*")

    def test_redundancy_duplicate(self) -> None:
        rule = Rule("Bash", "git *", "allow")
        assert _redundancy(rule, [rule]) == "duplicate"

    def test_redundancy_superset(self) -> None:
        wide = Rule("Bash", "git *", "allow")
        narrow = Rule("Bash", "git commit *", "allow")
        assert _redundancy(narrow, [wide]) == "superset"

    def test_redundancy_respects_effect(self) -> None:
        wide = Rule("Bash", "git *", "deny")
        narrow = Rule("Bash", "git commit *", "allow")
        assert _redundancy(narrow, [wide]) is None


class TestRuleEngine:
    def test_snapshot_merges_three_tiers_in_order(self) -> None:
        tmpdir = Path(tempfile.mkdtemp())
        paths = {t: tmpdir / f"{t}.yaml" for t in ("user", "project", "local")}
        for tier, path in paths.items():
            _write_rules(path, [{"rule": f"Bash({tier} *)", "effect": "allow"}])
        engine = RuleEngine(
            user_rules_path=paths["user"],
            project_rules_path=paths["project"],
            local_rules_path=paths["local"],
        )
        assert [r.pattern for r in engine.snapshot()] == [
            "user *",
            "project *",
            "local *",
        ]

    def test_missing_paths_yield_no_rules(self) -> None:
        engine = RuleEngine(
            user_rules_path=Path("/nonexistent/a.yaml"),
            project_rules_path=Path("/nonexistent/b.yaml"),
            local_rules_path=Path("/nonexistent/c.yaml"),
        )
        assert engine.snapshot() == []
        assert engine.evaluate("Bash", "ls") is None

    def test_reuses_parsed_rules_when_stat_unchanged(self) -> None:
        allow = '- rule: "Bash(git *)"\n  effect: allow\n'
        deny = '- rule: "Bash(git *)"\n  effect: deny \n'
        assert len(allow) == len(deny)
        tmpdir = Path(tempfile.mkdtemp())
        path = tmpdir / "rules.yaml"
        path.write_text(allow, encoding="utf-8")
        engine = RuleEngine(project_rules_path=path)
        assert engine.evaluate("Bash", "git push") == "allow"

        st = path.stat()
        path.write_text(deny, encoding="utf-8")
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
        assert engine.evaluate("Bash", "git push") == "allow"

    def test_detects_same_size_edit_with_newer_mtime(self) -> None:
        tmpdir = Path(tempfile.mkdtemp())
        path = tmpdir / "rules.yaml"
        path.write_text('- rule: "Bash(git *)"\n  effect: allow\n', encoding="utf-8")
        engine = RuleEngine(project_rules_path=path)
        assert engine.evaluate("Bash", "git push") == "allow"

        path.write_text('- rule: "Bash(git *)"\n  effect: deny \n', encoding="utf-8")
        future = time.time() + 2
        os.utime(path, (future, future))
        assert engine.evaluate("Bash", "git push") == "deny"

    def test_drops_cache_when_file_removed(self) -> None:
        tmpdir = Path(tempfile.mkdtemp())
        path = tmpdir / "rules.yaml"
        _write_rules(path, [{"rule": "Bash(git *)", "effect": "deny"}])
        engine = RuleEngine(project_rules_path=path)
        assert engine.evaluate("Bash", "git push") == "deny"

        path.unlink()
        assert engine.evaluate("Bash", "git push") is None

    @pytest.mark.parametrize(
        "payload",
        ["not-a-list\n", "- just-a-string\n", "- rule: bad\n  effect: allow\n", "[]"],
    )
    def test_malformed_files_are_tolerated(self, payload: str) -> None:
        tmpdir = Path(tempfile.mkdtemp())
        path = tmpdir / "rules.yaml"
        path.write_text(payload, encoding="utf-8")
        assert RuleEngine(project_rules_path=path).snapshot() == []

    def test_unreadable_yaml_is_tolerated(self) -> None:
        tmpdir = Path(tempfile.mkdtemp())
        path = tmpdir / "rules.yaml"
        path.write_text("rule: [unclosed\n", encoding="utf-8")
        assert RuleEngine(project_rules_path=path).snapshot() == []

    def test_invalid_effect_entries_are_skipped(self) -> None:
        tmpdir = Path(tempfile.mkdtemp())
        path = tmpdir / "rules.yaml"
        _write_rules(
            path,
            [
                {"rule": "Bash(git *)", "effect": "maybe"},
                {"rule": "Bash(npm *)", "effect": "allow"},
            ],
        )
        rules = RuleEngine(project_rules_path=path).snapshot()
        assert [r.pattern for r in rules] == ["npm *"]

    def test_append_local_rule_creates_parent_dirs(self) -> None:
        tmpdir = Path(tempfile.mkdtemp())
        local = tmpdir / ".kyncode" / "permissions.local.yaml"
        engine = RuleEngine(local_rules_path=local)
        engine.append_local_rule(Rule("Bash", "git commit *", "allow"))
        assert local.is_file()
        assert engine.evaluate("Bash", "git commit -m x") == "allow"

    def test_append_local_rule_without_path_is_noop(self) -> None:
        RuleEngine().append_local_rule(Rule("Bash", "git *", "allow"))

    def test_append_local_rule_skips_duplicate_and_superset(self) -> None:
        tmpdir = Path(tempfile.mkdtemp())
        local = tmpdir / "local.yaml"
        engine = RuleEngine(local_rules_path=local)
        engine.append_local_rule(Rule("Bash", "git *", "allow"))
        engine.append_local_rule(Rule("Bash", "git *", "allow"))
        engine.append_local_rule(Rule("Bash", "git status *", "allow"))
        loaded = yaml.safe_load(local.read_text(encoding="utf-8"))
        assert loaded == [{"rule": "Bash(git *)", "effect": "allow"}]

    def test_append_local_rule_keeps_distinct_effects(self) -> None:
        tmpdir = Path(tempfile.mkdtemp())
        local = tmpdir / "local.yaml"
        engine = RuleEngine(local_rules_path=local)
        engine.append_local_rule(Rule("Bash", "git *", "allow"))
        engine.append_local_rule(Rule("Bash", "git *", "deny"))
        loaded = yaml.safe_load(local.read_text(encoding="utf-8"))
        assert len(loaded) == 2

    def test_skip_and_add_audit_logs(self, caplog: pytest.LogCaptureFixture) -> None:
        tmpdir = Path(tempfile.mkdtemp())
        local = tmpdir / "local.yaml"
        engine = RuleEngine(local_rules_path=local)
        with caplog.at_level(logging.INFO, logger="kyncode.permissions.rules"):
            engine.append_local_rule(Rule("Bash", "git *", "allow"))
            engine.append_local_rule(Rule("Bash", "git status *", "allow"))

        messages = [r.getMessage() for r in caplog.records]
        assert any(m.startswith("local_rule_add ") for m in messages)
        skips = [m for m in messages if m.startswith("local_rule_skip ")]
        assert len(skips) == 1
        assert '"reason": "superset"' in skips[0]


def test_effect_alias_is_exported() -> None:
    assert Effect is not None
