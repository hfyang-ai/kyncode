from __future__ import annotations

from pathlib import Path

from kyncode.permissions.path_policy import (
    DEFAULT_DENY_WRITE,
    is_under,
    matches_deny_write,
    resolve_allowed_roots,
    resolve_deny_write_paths,
    resolve_real_path,
)


class PathSandbox:
    # Default deny-write paths. Kept as a class attribute for callers that
    # inspect it; the canonical list lives in path_policy.
    _DEFAULT_DENY_WRITE: list[str] = DEFAULT_DENY_WRITE

    def __init__(
        self,
        project_root: str,
        extra_allowed: list[str] | None = None,
        deny_write: list[str] | None = None,
    ) -> None:
        # Raw workspace root: absolute but symlinks unresolved. Used to build
        # the whole-workspace allow pattern so the prefix matches the absolute
        # paths the LLM passes in (also symlink-unresolved).
        self._raw_root = Path(project_root).absolute()
        root = self._raw_root.resolve()
        self._allowed_roots: list[Path] = resolve_allowed_roots(root, extra_allowed)
        self._deny_write: list[Path] = resolve_deny_write_paths(deny_write, root)

    @property
    def project_root(self) -> Path:
        return self._allowed_roots[0]

    @property
    def raw_root(self) -> Path:
        """Unresolved workspace root, used for whole-area allow patterns."""
        return self._raw_root

    def _is_deny_write(self, real_path: Path) -> bool:
        """Whether the path hits a deny-write entry (exact or nested under it)."""
        return matches_deny_write(real_path, self._deny_write)

    def _resolve(self, path: str) -> tuple[Path | None, str]:
        """Resolve an input path to a real absolute path, or a reason on failure."""
        return resolve_real_path(path, self.project_root)

    def check_deny_write(self, path: str) -> tuple[bool, str]:
        """Check protected paths only.

        These paths hold permission configuration and skill definitions and are
        never writable in any mode; callers must check this before mode logic.
        """
        real_path, err = self._resolve(path)
        if real_path is None:
            return False, err
        if self._is_deny_write(real_path):
            return False, f"路径 {path} 在禁写列表中"
        return True, ""

    def check(self, path: str) -> tuple[bool, str]:
        real_path, err = self._resolve(path)
        if real_path is None:
            return False, err

        # Deny-write takes precedence over the allow check.
        if self._is_deny_write(real_path):
            return False, f"路径 {path} 在禁写列表中"

        for root in self._allowed_roots:
            if is_under(real_path, root):
                return True, ""

        return False, f"路径 {path} 超出沙箱范围"

    def is_within_project(self, path: str) -> bool:
        """Whether the path lives under the workspace root.

        Unlike :meth:`check`, only the workspace root counts here (not the temp
        dir or extra allow roots). Used for the "allow and don't ask again"
        grant surface: in-workspace files generalize to the whole workspace,
        while out-of-workspace files keep asking.
        """
        real_path, _ = self._resolve(path)
        if real_path is None:
            return False
        return is_under(real_path, self.project_root)
