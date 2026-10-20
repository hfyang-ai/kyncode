from __future__ import annotations

import json
from pathlib import Path
from typing import IO, Protocol, runtime_checkable

from kyncode.memory.session_models import SessionMeta, SessionRecord

SESSIONS_DIR = ".kyncode/sessions"


@runtime_checkable
class SessionStore(Protocol):
    """会话持久化后端接口。

    上层（Session / SessionManager）只依赖本协议：新增后端（如 SQLite）时
    实现同样的方法即可，无需改动调用方。
    """

    def create(self, meta: SessionMeta) -> None:
        """为新会话建立存储，并写入初始元数据。"""

    def append(self, session_id: str, record: SessionRecord) -> None:
        """追加一条记录；调用返回后记录必须已持久化。"""

    def save_meta(self, meta: SessionMeta) -> None:
        """覆盖写入会话元数据。"""

    def load_meta(self, session_id: str) -> SessionMeta | None:
        """读取元数据；不存在或损坏时返回 None。"""

    def read_records(self, session_id: str) -> list[SessionRecord] | None:
        """读取全部记录；会话不存在时返回 None。"""

    def list_metas(self) -> list[SessionMeta]:
        """列出所有可读元数据，不保证顺序。"""

    def delete(self, session_id: str) -> bool:
        """删除会话及其元数据，任一存在即返回 True。"""

    def close(self, session_id: str) -> None:
        """释放该会话占用的资源，重复调用无副作用。"""


class JsonlSessionStore:
    """默认后端：每条会话一个 `<id>.jsonl` 追加文件，外加一个 `<id>.meta`。

    文件布局与历史实现完全一致：
    - `<id>.jsonl` 每行一个紧凑 JSON 对象（`SessionRecord.to_dict`）；
    - `<id>.meta` 缩进 2 的空格 JSON（`SessionMeta.to_dict`）。
    """

    def __init__(self, sessions_dir: Path) -> None:
        self._sessions_dir = Path(sessions_dir)
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        self._handles: dict[str, IO[str]] = {}

    @property
    def sessions_dir(self) -> Path:
        return self._sessions_dir

    def _jsonl_path(self, session_id: str) -> Path:
        return self._sessions_dir / f"{session_id}.jsonl"

    def _meta_path(self, session_id: str) -> Path:
        return self._sessions_dir / f"{session_id}.meta"

    def _handle(self, session_id: str) -> IO[str]:
        handle = self._handles.get(session_id)
        if handle is None or handle.closed:
            handle = open(  # noqa: SIM115
                self._jsonl_path(session_id), "a", encoding="utf-8"
            )
            self._handles[session_id] = handle
        return handle

    def create(self, meta: SessionMeta) -> None:
        self.save_meta(meta)
        # 与旧实现一致：建会话时就创建空 jsonl，append 句柄保持打开。
        self._handle(meta.id)

    def append(self, session_id: str, record: SessionRecord) -> None:
        handle = self._handle(session_id)
        handle.write(
            json.dumps(record.to_dict(), ensure_ascii=False, separators=(",", ":"))
            + "\n"
        )
        handle.flush()

    def save_meta(self, meta: SessionMeta) -> None:
        self._meta_path(meta.id).write_text(
            json.dumps(meta.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def load_meta(self, session_id: str) -> SessionMeta | None:
        return self._read_meta(self._meta_path(session_id))

    @staticmethod
    def _read_meta(path: Path) -> SessionMeta | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return SessionMeta.from_dict(data)

    def read_records(self, session_id: str) -> list[SessionRecord] | None:
        path = self._jsonl_path(session_id)
        if not path.exists():
            return None
        records: list[SessionRecord] = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                record = SessionRecord.from_dict(data)
                if record is not None:
                    records.append(record)
        return records

    def list_metas(self) -> list[SessionMeta]:
        metas: list[SessionMeta] = []
        for meta_path in self._sessions_dir.glob("*.meta"):
            meta = self._read_meta(meta_path)
            if meta is not None:
                metas.append(meta)
        return metas

    def delete(self, session_id: str) -> bool:
        self.close(session_id)
        deleted = False
        for path in (self._jsonl_path(session_id), self._meta_path(session_id)):
            if path.exists():
                path.unlink()
                deleted = True
        return deleted

    def close(self, session_id: str) -> None:
        handle = self._handles.pop(session_id, None)
        if handle is not None and not handle.closed:
            handle.flush()
            handle.close()


def create_session_store(work_dir: str) -> SessionStore:
    """按配置创建会话存储后端。

    切换 SQLite 时在这里返回 SqliteSessionStore，上层无需改动。
    """
    return JsonlSessionStore(Path(work_dir) / SESSIONS_DIR)
