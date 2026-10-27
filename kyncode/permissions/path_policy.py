from __future__ import annotations

import tempfile
from pathlib import Path

# Default write-protected paths: sensitive configuration that the agent must
# never modify. The first entries are kyncode's own permission configuration
# (relative to project_root); the rest are user-level credentials, keys and
# shell profiles (leading ~ is expanded), protected even when project_root is
# the home directory.
DEFAULT_DENY_WRITE: list[str] = [
    ".kyncode/config.yaml",
    ".kyncode/permissions.local.yaml",
    ".kyncode/skills/",
    "~/.ssh/",
    "~/.aws/",
    "~/.gnupg/",
    "~/.netrc",
    "~/.bashrc",
    "~/.zshrc",
    "~/.profile",
    "~/.bash_profile",
    "~/.zprofile",
]


def resolve_allowed_roots(
    project_root: Path, extra_allowed: list[str] | None = None
) -> list[Path]:
    """Build the list of roots a path may live under.

    project_root goes first because callers treat ``roots[0]`` as the workspace
    root. The system temp directory is always allowed so tools can stage files.
    """
    roots = [project_root.resolve(), Path(tempfile.gettempdir()).resolve()]
    for extra in extra_allowed or []:
        roots.append(Path(extra).resolve())
    return roots


def resolve_deny_write_paths(deny_write: list[str] | None, root: Path) -> list[Path]:
    """Expand and normalize the deny-write list.

    Relative entries resolve against the resolved workspace root; entries
    starting with ``~`` (or otherwise absolute) are kept as-is after expansion.
    """
    resolved: list[Path] = []
    for entry in deny_write or DEFAULT_DENY_WRITE:
        deny_path = Path(entry).expanduser()
        if not deny_path.is_absolute():
            deny_path = root / deny_path
        resolved.append(deny_path.resolve())
    return resolved


def matches_deny_write(real_path: Path, deny_paths: list[Path]) -> bool:
    """Whether ``real_path`` is one of, or nested under, a deny-write entry."""
    for deny_path in deny_paths:
        if real_path == deny_path:
            return True
        try:
            real_path.relative_to(deny_path)
            return True
        except ValueError:
            continue
    return False


def resolve_real_path(path: str, project_root: Path) -> tuple[Path | None, str]:
    """Resolve an input path to a real absolute path.

    Symlinks are followed. For paths that do not exist yet, the deepest
    existing ancestor is resolved and the remaining suffix is re-attached, so
    new files inside allowed roots can be checked before creation returns
    ``(None, reason)`` when no ancestor can be resolved.
    """
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = project_root / candidate
    absolute = candidate.absolute()

    try:
        return absolute.resolve(strict=True), ""
    except OSError:
        ancestor = absolute
        while not ancestor.exists():
            parent = ancestor.parent
            if parent == ancestor:
                return None, f"无法解析路径: {path}"
            ancestor = parent
        try:
            resolved_ancestor = ancestor.resolve(strict=True)
        except OSError:
            return None, f"无法解析路径: {path}"
        return resolved_ancestor / absolute.relative_to(ancestor), ""


def is_under(real_path: Path, root: Path) -> bool:
    """Whether ``real_path`` is ``root`` itself or nested beneath it."""
    try:
        real_path.relative_to(root)
        return True
    except ValueError:
        return False
