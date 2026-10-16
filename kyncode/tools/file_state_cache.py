from __future__ import annotations

import time
from pathlib import Path


class FileStateCache:
    """Tracks which files have been read, enforcing read-before-edit.

    Stores { absolute_path: mtime_ns } after each ReadFile call.
    EditFile and WriteFile check the cache before proceeding:
      - Gate 1: file must have been read (present in cache).
      - Gate 2: file must not have been modified since the read (mtime_ns matches).

    额外记录每个文件的读取时间（read_at），并支持序列化 / 反序列化：这样它
    不再只是一份进程内的门禁表，还能在 resume 时用于「文件新鲜度」判断——
    哪些已读文件内容没变（历史里的读取结果仍有效，不必重读）、哪些变了（需重读）。
    """

    def __init__(self) -> None:
        self._cache: dict[str, int] = {}
        self._read_at: dict[str, float] = {}

    def record(self, path: str, mtime_ns: int) -> None:
        """Record a file's mtime after a successful read."""
        self._cache[path] = mtime_ns
        self._read_at[path] = time.time()

    def check(self, path: str) -> tuple[bool, str]:
        """Check whether the file is safe to edit/write.

        Returns (ok, error_message). If ok is True, error_message is empty.
        """
        cached_mtime_ns = self._cache.get(path)
        if cached_mtime_ns is None:
            return (
                False,
                "Error: file has not been read yet. Read it first before editing.",
            )

        try:
            current_mtime_ns = Path(path).stat().st_mtime_ns
        except OSError:
            return True, ""

        if current_mtime_ns != cached_mtime_ns:
            return (
                False,
                "Error: file has been modified since last read. Read it again before editing.",
            )

        return True, ""

    def update(self, path: str) -> None:
        """Update the cache entry after a successful edit/write."""
        try:
            mtime_ns = Path(path).stat().st_mtime_ns
            self._cache[path] = mtime_ns
            self._read_at[path] = time.time()
        except OSError:
            self._cache.pop(path, None)
            self._read_at.pop(path, None)

    def freshness(self) -> tuple[list[str], list[str]]:
        """Compare cached mtime against the current on-disk mtime.

        Returns (unchanged, changed): `unchanged` 是自上次读取后内容没变的文件，
        `changed` 是被修改过或已消失的文件。resume 时据此告知模型哪些文件可
        直接引用历史里的读取结果、哪些必须重读。
        """
        unchanged: list[str] = []
        changed: list[str] = []
        for path, mtime_ns in self._cache.items():
            try:
                current = Path(path).stat().st_mtime_ns
            except OSError:
                changed.append(path)
                continue
            if current == mtime_ns:
                unchanged.append(path)
            else:
                changed.append(path)
        return unchanged, changed

    def to_dict(self) -> dict[str, dict[str, int | float]]:
        return {
            path: {"mtime_ns": mtime_ns, "read_at": self._read_at.get(path, 0.0)}
            for path, mtime_ns in self._cache.items()
        }

    @classmethod
    def from_dict(cls, data: dict) -> FileStateCache:
        cache = cls()
        for path, info in data.items():
            if isinstance(info, dict):
                cache._cache[path] = int(info.get("mtime_ns", 0))
                cache._read_at[path] = float(info.get("read_at", 0.0))
            else:
                # 兼容旧格式：值直接是 mtime 数字。
                cache._cache[path] = int(info)
        return cache

    def load_dict(self, data: dict) -> None:
        """就地恢复缓存内容，保持对象身份不变。

        ReadFile/EditFile/WriteFile 在构造时共享同一个 FileStateCache 实例，
        resume 时必须原地填充这个实例，而不是换掉引用——否则那些工具还指着旧对象。
        """
        other = self.from_dict(data)
        self._cache = other._cache
        self._read_at = other._read_at
