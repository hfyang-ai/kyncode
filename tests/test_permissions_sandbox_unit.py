from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from kyncode.permissions.modes import PermissionMode, mode_decide
from kyncode.permissions.path_policy import (
    DEFAULT_DENY_WRITE,
    is_under,
    matches_deny_write,
    resolve_allowed_roots,
    resolve_deny_write_paths,
    resolve_real_path,
)
from kyncode.permissions.sandbox import PathSandbox


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    return tmp_path


class TestResolveRealPath:
    def test_existing_file(self, workspace: Path) -> None:
        target = workspace / "a.txt"
        target.write_text("x")
        real, err = resolve_real_path(str(target), workspace)
        assert real == target.resolve()
        assert err == ""

    def test_missing_nested_path_reattaches_suffix(self, workspace: Path) -> None:
        real, err = resolve_real_path(str(workspace / "a" / "b" / "c.txt"), workspace)
        assert err == ""
        assert real == workspace.resolve() / "a" / "b" / "c.txt"

    def test_relative_path_is_project_based(self, workspace: Path) -> None:
        real, err = resolve_real_path("sub/file.txt", workspace)
        assert err == ""
        assert real == workspace.resolve() / "sub" / "file.txt"

    def test_symlink_is_followed(self, workspace: Path) -> None:
        outside = workspace.parent / f"{workspace.name}-outside"
        outside.mkdir()
        link = workspace / "link"
        link.symlink_to(outside, target_is_directory=True)
        real, err = resolve_real_path(str(link / "x.txt"), workspace)
        assert err == ""
        assert real == outside.resolve() / "x.txt"

    def test_tilde_is_expanded(self, workspace: Path) -> None:
        real, err = resolve_real_path("~/file.txt", workspace)
        assert err == ""
        assert real == Path("~/file.txt").expanduser().resolve()


class TestDenyWriteHelpers:
    def test_matches_exact_and_nested(self, workspace: Path) -> None:
        deny = [workspace / ".kyncode" / "config.yaml", workspace / "secrets"]
        assert matches_deny_write(workspace / ".kyncode" / "config.yaml", deny)
        assert matches_deny_write(workspace / "secrets" / "a" / "b", deny)
        assert not matches_deny_write(workspace / "secretsx" / "a", deny)

    def test_relative_entries_resolve_against_root(self, workspace: Path) -> None:
        resolved = resolve_deny_write_paths([".kyncode/skills/"], workspace)
        assert resolved == [(workspace / ".kyncode/skills").resolve()]

    def test_home_entries_are_expanded(self, workspace: Path) -> None:
        resolved = resolve_deny_write_paths(["~/.ssh/"], workspace)
        assert resolved == [Path("~/.ssh").expanduser().resolve()]

    def test_absolute_entry_is_preserved(self, workspace: Path) -> None:
        absolute = workspace / "abs"
        resolved = resolve_deny_write_paths([str(absolute)], workspace)
        assert resolved == [absolute.resolve()]

    def test_defaults_used_when_none(self, workspace: Path) -> None:
        resolved = resolve_deny_write_paths(None, workspace)
        assert len(resolved) == len(DEFAULT_DENY_WRITE)


class TestAllowedRoots:
    def test_project_root_first_and_tempdir_included(self, workspace: Path) -> None:
        roots = resolve_allowed_roots(workspace.resolve())
        assert roots[0] == workspace.resolve()
        assert Path(tempfile.gettempdir()).resolve() in roots

    def test_extra_allowed_appended(self, workspace: Path) -> None:
        extra = workspace / "extra"
        extra.mkdir()
        roots = resolve_allowed_roots(workspace.resolve(), [str(extra)])
        assert extra.resolve() in roots

    def test_is_under(self, workspace: Path) -> None:
        assert is_under(workspace / "a", workspace)
        assert is_under(workspace, workspace)
        assert not is_under(workspace.parent, workspace)


class TestPathSandboxBehavior:
    def test_inside_project_allowed(self, tmp_path: Path) -> None:
        sandbox = PathSandbox(str(tmp_path))
        target = tmp_path / "src" / "main.py"
        target.parent.mkdir(parents=True)
        target.write_text("hello")
        ok, _ = sandbox.check(str(target))
        assert ok

    def test_outside_project_rejected(self, tmp_path: Path) -> None:
        sandbox = PathSandbox(str(tmp_path))
        ok, reason = sandbox.check("/etc/passwd")
        assert not ok
        assert "沙箱" in reason

    def test_temp_dir_allowed(self, tmp_path: Path) -> None:
        sandbox = PathSandbox(str(tmp_path))
        ok, _ = sandbox.check(str(Path(tempfile.gettempdir()) / "kyncode_unit.txt"))
        assert ok

    def test_deny_write_list(self, tmp_path: Path) -> None:
        sandbox = PathSandbox(str(tmp_path))
        for rel in (
            ".kyncode/permissions.local.yaml",
            ".kyncode/config.yaml",
            ".kyncode/skills/evil/SKILL.md",
        ):
            ok, reason = sandbox.check_deny_write(str(tmp_path / rel))
            assert not ok, f"{rel} should be denied"
            assert "禁写" in reason
        ok, _ = sandbox.check_deny_write(str(tmp_path / "a.txt"))
        assert ok

    def test_check_deny_write_is_independent_of_allow_roots(self, tmp_path: Path) -> None:
        sandbox = PathSandbox(str(tmp_path))
        # Outside every allow root but not deny-listed -> still passes this check.
        ok, err = sandbox.check_deny_write("/etc/hostname")
        assert ok
        assert err == ""

    def test_symlink_escape_rejected(self, tmp_path: Path) -> None:
        sandbox = PathSandbox(str(tmp_path))
        link = tmp_path / "escape.txt"
        link.symlink_to("/etc/hosts")
        ok, reason = sandbox.check(str(link))
        assert not ok
        assert "沙箱" in reason

    def test_is_within_project(self, tmp_path: Path) -> None:
        sandbox = PathSandbox(str(tmp_path))
        assert sandbox.is_within_project(str(tmp_path / "a.txt"))
        assert sandbox.is_within_project("sub/file.txt")
        assert not sandbox.is_within_project("/etc/passwd")
        assert not sandbox.is_within_project("~/.ssh/id_rsa")
        tmp = Path(tempfile.gettempdir()) / "kyncode_scope.txt"
        assert not sandbox.is_within_project(str(tmp))

    def test_raw_root_keeps_symlink_project_root_resolves(self, tmp_path: Path) -> None:
        target = tmp_path / "target"
        target.mkdir()
        link = tmp_path / "link"
        link.symlink_to(target, target_is_directory=True)
        sandbox = PathSandbox(str(link))
        assert sandbox.raw_root == link.absolute()
        assert sandbox.project_root == link.resolve()

    def test_custom_deny_write_overrides_defaults(self, tmp_path: Path) -> None:
        sandbox = PathSandbox(str(tmp_path), deny_write=["custom.txt"])
        ok, _ = sandbox.check_deny_write(str(tmp_path / "custom.txt"))
        assert not ok
        # Defaults are replaced, not merged.
        ok, _ = sandbox.check_deny_write(str(tmp_path / ".kyncode" / "config.yaml"))
        assert ok


class TestPermissionModeMatrix:
    @pytest.mark.parametrize(
        ("mode", "category", "expected"),
        [
            (PermissionMode.DEFAULT, "read", "allow"),
            (PermissionMode.DEFAULT, "write", "ask"),
            (PermissionMode.DEFAULT, "command", "ask"),
            (PermissionMode.DEFAULT, "network", "ask"),
            (PermissionMode.ACCEPT_EDITS, "read", "allow"),
            (PermissionMode.ACCEPT_EDITS, "write", "allow"),
            (PermissionMode.ACCEPT_EDITS, "command", "ask"),
            (PermissionMode.ACCEPT_EDITS, "network", "ask"),
            (PermissionMode.PLAN, "read", "allow"),
            (PermissionMode.PLAN, "write", "ask"),
            (PermissionMode.PLAN, "command", "ask"),
            (PermissionMode.PLAN, "network", "ask"),
            (PermissionMode.BYPASS, "read", "allow"),
            (PermissionMode.BYPASS, "write", "allow"),
            (PermissionMode.BYPASS, "command", "allow"),
            (PermissionMode.BYPASS, "network", "allow"),
        ],
    )
    def test_matrix(self, mode: PermissionMode, category, expected: str) -> None:
        assert mode_decide(mode, category) == expected

    def test_enum_values_stable(self) -> None:
        assert PermissionMode.DEFAULT.value == "default"
        assert PermissionMode.ACCEPT_EDITS.value == "acceptEdits"
        assert PermissionMode.PLAN.value == "plan"
        assert PermissionMode.BYPASS.value == "bypassPermissions"
